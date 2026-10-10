package handlers

import (
	"errors"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func backupRig(t *testing.T) (*rig, string) {
	r := mailRig(t)
	bucket := t.TempDir()
	r.agent.StageRoot = t.TempDir()
	r.agent.Restic = func(env map[string]string) (Repository, error) {
		if env["RESTIC_PASSWORD"] == "wrong password for the repository" {
			return nil, errors.New("wrong password")
		}
		return &FolderRepo{Dir: filepath.Join(bucket, "repo")}, nil
	}
	r.must("BACKUP_CONFIGURE", map[string]any{"endpoint": "https://s3.example.net", "bucket": "whp-backups", "prefix": "node-1", "access_key": "AKIAEXAMPLEKEY123456",
		"secret_key": "secret-key-material-1234567890", "repo_password": "backup password for the repository"})
	site := filepath.Join(r.root, "acme.com", "public_html")
	if err := os.MkdirAll(site, 0o755); err != nil {
		t.Fatal(err)
	}
	_ = os.WriteFile(filepath.Join(site, "index.html"), []byte("<h1>original</h1>"), 0o644)
	_ = os.WriteFile(filepath.Join(site, "style.css"), []byte("body{}"), 0o644)
	return r, site
}

func run(r *rig, kind string) string {
	res := r.must("BACKUP_RUN", map[string]any{"account": "abcd1234", "kind": kind, "source": "manual"})
	return res.Data["snapshot_id"].(string)
}

func TestTheStorageKeysAreWrittenForRootOnlyAndNeverLogged(t *testing.T) {
	r, _ := backupRig(t)
	env, _ := r.fake.Read("/etc/whp/restic.env")
	if !strings.Contains(env, "RESTIC_REPOSITORY=s3:https://s3.example.net/whp-backups/node-1") || !strings.Contains(env, "RESTIC_PASSWORD=backup password for the repository") {
		t.Fatal(env)
	}
	if !strings.Contains(strings.Join(r.fake.Commands[len(r.fake.Commands)-1], " "), "0o600 root") {
		t.Fatalf("%v", r.fake.Commands[len(r.fake.Commands)-1])
	}
	for _, c := range r.fake.Commands {
		if strings.Contains(strings.Join(c, " "), "secret-key-material") {
			t.Fatal("a key reached a command line")
		}
	}
	// a repository that refuses the password leaves the old settings in place
	r.fails("BACKUP_CONFIGURE", map[string]any{"endpoint": "https://s3.example.net", "bucket": "whp-backups", "prefix": "node-1", "access_key": "AKIAEXAMPLEKEY123456",
		"secret_key": "secret-key-material-1234567890", "repo_password": "wrong password for the repository"}, "repository_error")
	if again, _ := r.fake.Read("/etc/whp/restic.env"); again != env {
		t.Fatal("the old settings must be back")
	}
}

func TestABackupHoldsFilesDatabasesAndSettingsAndARestorePutsThemBack(t *testing.T) {
	r, site := backupRig(t)
	prefix := "abcd1234_"
	r.must("CREATE_DATABASE", map[string]any{"account": "abcd1234", "name": prefix + "shop"})
	r.agent.Engine.(*FakeEngine).AddTable(prefix+"shop", "orders", []Column{{Name: "id", Type: "int", Primary: true}}, [][]any{{1}, {2}, {3}})
	id := run(r, "full")
	repo, _ := r.agent.Restic(map[string]string{})
	meta, _ := repo.Meta(id)
	var names []string
	for k := range meta.Manifest {
		names = append(names, k)
	}
	joined := strings.Join(names, " ")
	for _, want := range []string{"files/acme.com/public_html/index.html", "databases/abcd1234_shop.sql", "config/var__lib__whp__accounts__abcd1234.json"} {
		if !strings.Contains(joined, want) {
			t.Fatalf("%s missing from %v", want, names)
		}
	}
	_ = os.WriteFile(filepath.Join(site, "index.html"), []byte("<h1>DEFACED</h1>"), 0o644)
	_ = os.WriteFile(filepath.Join(site, "extra.txt"), []byte("added later"), 0o644)
	r.agent.Engine.(*FakeEngine).DBs[prefix+"shop"]["orders"].Rows = nil
	res := r.must("BACKUP_RESTORE", map[string]any{"account": "abcd1234", "snapshot_id": id, "kind": "full"})
	if res.Data["safety_snapshot_id"] == "" || res.Data["databases"] != 1 {
		t.Fatalf("%v", res.Data)
	}
	if b, _ := os.ReadFile(filepath.Join(site, "index.html")); string(b) != "<h1>original</h1>" {
		t.Fatalf("not restored: %q", b)
	}
	if _, err := os.Stat(filepath.Join(site, "extra.txt")); err == nil {
		t.Fatal("a restore gives back exactly the backup's state")
	}
	if rows := len(r.agent.Engine.(*FakeEngine).DBs[prefix+"shop"]["orders"].Rows); rows != 3 {
		t.Fatalf("database rows %d", rows)
	}
	// the safety copy holds what the restore replaced, so a mistake can be undone
	safety := res.Data["safety_snapshot_id"].(string)
	r.must("BACKUP_RESTORE", map[string]any{"account": "abcd1234", "snapshot_id": safety, "kind": "files"})
	if b, _ := os.ReadFile(filepath.Join(site, "index.html")); string(b) != "<h1>DEFACED</h1>" {
		t.Fatalf("the safety copy must bring back the defaced page: %q", b)
	}
	r.fails("BACKUP_RESTORE", map[string]any{"account": "abcd1234", "snapshot_id": id, "kind": "email"}, "invalid")
}

func TestADamagedBackupIsDetectedAndNeverRestored(t *testing.T) {
	r, site := backupRig(t)
	id := run(r, "files")
	if res := r.must("BACKUP_VERIFY", map[string]any{"account": "abcd1234", "snapshot_id": id}); res.Code != "verified" {
		t.Fatal(res.Code)
	}
	bucket := filepath.Join(filepath.Dir(r.root), "x") // find the repository folder through a new FolderRepo
	_ = bucket
	repo, _ := r.agent.Restic(nil)
	data := repo.(*FolderRepo).Dir
	_ = os.WriteFile(filepath.Join(data, "snapshots", id, "data", "files", "acme.com", "public_html", "index.html"), []byte("tampered"), 0o644)
	r.fails("BACKUP_VERIFY", map[string]any{"account": "abcd1234", "snapshot_id": id}, "damaged")
	_ = os.WriteFile(filepath.Join(site, "index.html"), []byte("<h1>current</h1>"), 0o644)
	r.fails("BACKUP_RESTORE", map[string]any{"account": "abcd1234", "snapshot_id": id, "kind": "files"}, "damaged")
	if b, _ := os.ReadFile(filepath.Join(site, "index.html")); string(b) != "<h1>current</h1>" {
		t.Fatal("nothing may change when the check fails")
	}
	r.fails("BACKUP_TEST_RESTORE", map[string]any{"account": "abcd1234", "snapshot_id": id}, "restore_test_failed")
}

func TestLinksAreNeverCopiedIntoABackup(t *testing.T) {
	r, site := backupRig(t)
	secret := filepath.Join(t.TempDir(), "shadow.txt")
	_ = os.WriteFile(secret, []byte("root:SECRETHASH"), 0o644)
	if err := os.Symlink(secret, filepath.Join(site, "leak.txt")); err != nil {
		t.Skip("this machine cannot make symbolic links")
	}
	id := run(r, "files")
	repo, _ := r.agent.Restic(nil)
	meta, _ := repo.Meta(id)
	for path := range meta.Manifest {
		if strings.Contains(path, "leak.txt") {
			t.Fatal("a link was followed into the backup")
		}
	}
}

func TestAnotherAccountsBackupCannotBeUsed(t *testing.T) {
	r, _ := backupRig(t)
	id := run(r, "files")
	for command, extra := range map[string]map[string]any{"BACKUP_VERIFY": nil, "BACKUP_FORGET": nil, "BACKUP_TEST_RESTORE": nil, "BACKUP_RESTORE": {"kind": "files"}} {
		p := map[string]any{"account": "other567", "snapshot_id": id}
		for k, v := range extra {
			p[k] = v
		}
		r.fails(command, p, "forbidden")
	}
}

func TestForgetTestRestoreAndNodeConfiguration(t *testing.T) {
	r, _ := backupRig(t)
	id := run(r, "files")
	if res := r.must("BACKUP_TEST_RESTORE", map[string]any{"account": "abcd1234", "snapshot_id": id}); res.Code != "restore_ok" || res.Data["files"] != 2 {
		t.Fatalf("%v", res)
	}
	cfg := r.must("BACKUP_NODE_CONFIG", map[string]any{})
	repo, _ := r.agent.Restic(nil)
	meta, _ := repo.Meta(cfg.Data["snapshot_id"].(string))
	joined := ""
	for k := range meta.Manifest {
		joined += k + " "
	}
	if !strings.Contains(joined, "var__lib__whp__accounts__abcd1234.json") || strings.Contains(joined, "restic.env") {
		t.Fatalf("the node's configuration is backed up, never its backup keys: %s", joined)
	}
	if res := r.must("BACKUP_FORGET", map[string]any{"account": "abcd1234", "snapshot_id": id}); res.Code != "deleted" {
		t.Fatal(res.Code)
	}
	r.fails("BACKUP_VERIFY", map[string]any{"account": "abcd1234", "snapshot_id": id}, "repository_error")
}

func TestNothingRunsBeforeStorageIsSetUpAndEmailNeedsAMailNode(t *testing.T) {
	r := newRig(t)
	r.fails("BACKUP_RUN", map[string]any{"account": "abcd1234", "kind": "files", "source": "manual"}, "not_configured")
	r2, _ := backupRig(t)
	r2.agent.MailRoot = ""
	r2.fails("BACKUP_RUN", map[string]any{"account": "abcd1234", "kind": "email", "source": "manual"}, "not_applicable")
	r2.agent.MailRoot = t.TempDir()
	r2.fails("BACKUP_RUN", map[string]any{"account": "abcd1234", "kind": "email", "source": "manual"}, "not_found")
}

func TestEmailBackupAndRestoreOnAMailNode(t *testing.T) {
	r, _ := backupRig(t)
	addDomain(r, nil)
	newBox(r, "info")
	raw := []byte("From: a@example.org\r\nTo: info@acme.com\r\nSubject: keep me\r\n\r\nbody\r\n")
	if _, err := r.agent.Deliver("acme.com", "info", raw, "inbox", false); err != nil {
		t.Fatal(err)
	}
	id := run(r, "email")
	_ = os.RemoveAll(filepath.Join(r.agent.MailRoot, "acme.com", "info", "new"))
	_ = os.MkdirAll(filepath.Join(r.agent.MailRoot, "acme.com", "info", "new"), 0o755)
	r.must("BACKUP_RESTORE", map[string]any{"account": "abcd1234", "snapshot_id": id, "kind": "email"})
	msgs := r.must("MAIL_MESSAGES", map[string]any{"account": "abcd1234", "address": "info@acme.com", "folder": "inbox"}).Data["messages"].([]any)
	if len(msgs) != 1 || msgs[0].(map[string]any)["subject"] != "keep me" {
		t.Fatalf("%v", msgs)
	}
	repo, _ := r.agent.Restic(nil)
	meta, _ := repo.Meta(id)
	for path := range meta.Manifest {
		if strings.HasSuffix(path, ".json") {
			text, _ := os.ReadFile(filepath.Join(repo.(*FolderRepo).Dir, "snapshots", id, "data", filepath.FromSlash(path)))
			if strings.Contains(string(text), "SHA512-CRYPT") {
				t.Fatal("password hashes must not be in the mail backup's settings file")
			}
		}
	}
}
