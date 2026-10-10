package handlers

import (
	"crypto/sha256"
	"encoding/hex"
	"io"
	"io/fs"
	"os"
	"path/filepath"
	"strings"
)

// Backups (ADR 10): gather one account's data into a staging folder, store it as an encrypted snapshot in a bucket the node does not control the lifecycle of, check it, restore it, and prove
// it can be restored.
//
// Safety rules that matter:
//   - Links are never followed. A customer could point a link at /etc/shadow; copying what it points to would put the server's secrets into their backup. Links are skipped.
//   - A snapshot belongs to the account it was made for (a tag): every command refuses a snapshot of another account.
//   - A restore takes a safety backup first, then replaces only the parts the backup holds.
//   - The backup keys are never backed up: the repository settings file is excluded from the node-configuration snapshot.
//   - A test restore uses a scratch folder and touches nothing live.
//
// Only the copying has run for real (on temporary folders against a fake repository); restic itself has not.

const envFile = "/etc/whp/restic.env"

var nodeConfigRoots = []string{"/var/lib/whp/", "/etc/whp/", "/etc/httpd/conf.d/whp/", "/etc/postfix/whp/", "/etc/dovecot/whp/", "/etc/rspamd/local.d/", "/etc/ssh/sshd_config.d/", "/etc/rspamd/dkim/"}

// covers is what a restore of each kind puts back. "full" is the web node's whole account; mail is its own backup on the mail node.
var covers = map[string][]string{"files": {"files"}, "database": {"database"}, "email": {"email"}, "full": {"files", "database"}}

// Snapshot is what the repository knows about one backup.
type Snapshot struct {
	ID       string
	Tags     []string
	Manifest map[string]string // every file's SHA-256, when the repository keeps one (restic checks content itself)
}

// Repository is the encrypted backup store.
type Repository interface {
	Init() (created bool, err error)
	Backup(folder string, tags []string) (id string, size int64, err error)
	Meta(id string) (Snapshot, error)
	Restore(id, target string) (dataDir string, err error)
	Check(id string) (ok bool, detail string)
	Forget(id string) (bool, error)
}

func init() {
	extraHandlers = append(extraHandlers, func(a *Agent) map[string]func(map[string]any) (Result, error) {
		return map[string]func(map[string]any) (Result, error){"BACKUP_CONFIGURE": a.backupConfigure, "BACKUP_RUN": a.backupRun, "BACKUP_VERIFY": a.backupVerify,
			"BACKUP_RESTORE": a.backupRestore, "BACKUP_FORGET": a.backupForget, "BACKUP_TEST_RESTORE": a.backupTestRestore, "BACKUP_NODE_CONFIG": a.backupNodeConfig}
	})
}

func parseEnv(text string) map[string]string {
	env := map[string]string{}
	for _, line := range strings.Split(text, "\n") {
		if k, v, ok := strings.Cut(line, "="); ok {
			env[k] = v
		}
	}
	return env
}

func (a *Agent) repo() (Repository, error) {
	text, ok := a.Sys.Read(envFile)
	if !ok || strings.TrimSpace(text) == "" {
		return nil, herr("not_configured", "Backups have not been set up on this node.")
	}
	if a.Restic == nil {
		return nil, herr("not_available", "This node cannot reach backup storage.")
	}
	r, err := a.Restic(parseEnv(text))
	if err != nil {
		return nil, herr("repository_error", "%s", clip(err.Error(), 300))
	}
	return r, nil
}

func (a *Agent) backupConfigure(p map[string]any) (Result, error) {
	env := "RESTIC_REPOSITORY=s3:" + str(p, "endpoint") + "/" + str(p, "bucket") + "/" + str(p, "prefix") + "\nRESTIC_PASSWORD=" + str(p, "repo_password") +
		"\nAWS_ACCESS_KEY_ID=" + str(p, "access_key") + "\nAWS_SECRET_ACCESS_KEY=" + str(p, "secret_key") + "\n"
	old, hadOld := a.Sys.Read(envFile)
	if err := a.Sys.Write(envFile, env, 0o600, "root", ""); err != nil { // root only
		return Result{}, err
	}
	created, err := func() (bool, error) {
		r, err := a.repo()
		if err != nil {
			return false, err
		}
		c, err := r.Init() // an existing repository must accept this password
		if err != nil {
			return false, herr("repository_error", "%s", clip(err.Error(), 300))
		}
		return c, nil
	}()
	if err != nil {
		if hadOld {
			_ = a.Sys.Write(envFile, old, 0o600, "root", "")
		} else {
			_ = a.Sys.Remove(envFile)
		}
		return Result{}, err
	}
	return Result{"configured", map[string]any{"created": created}}, nil
}

func (a *Agent) stage(account string) (string, error) {
	dir := filepath.Join(a.StageRoot, account+"-"+randomHex(4))
	return dir, os.MkdirAll(dir, 0o700)
}

// copyTree copies a folder, SKIPPING every symbolic link and the trash. It returns the number of files copied.
func copyTree(source, target string) (int, error) {
	if err := os.MkdirAll(target, 0o755); err != nil {
		return 0, err
	}
	items, err := os.ReadDir(source)
	if err != nil {
		return 0, err
	}
	count := 0
	for _, it := range items {
		src, dst := filepath.Join(source, it.Name()), filepath.Join(target, it.Name())
		switch {
		case it.Type()&fs.ModeSymlink != 0:
			continue
		case it.IsDir():
			if it.Name() == ".trash" {
				continue
			}
			n, err := copyTree(src, dst)
			if err != nil {
				return count, err
			}
			count += n
		case it.Type().IsRegular():
			in, err := os.Open(src)
			if err != nil {
				return count, err
			}
			out, err := os.Create(dst)
			if err != nil {
				in.Close()
				return count, err
			}
			_, err = io.Copy(out, in)
			in.Close()
			out.Close()
			if err != nil {
				return count, err
			}
			count++
		}
	}
	return count, nil
}

func configName(path string) string { return strings.ReplaceAll(strings.Trim(path, "/"), "/", "__") }

func (a *Agent) gather(account, kind, stage string) (map[string]any, error) {
	found := map[string]any{"files": 0, "databases": 0, "mail": 0}
	st := a.stateRead(pathsOfState(account))
	if kind == "files" || kind == "full" {
		root := ""
		if a.RootFor != nil {
			root = a.RootFor(account)
		}
		if root == "" || st == nil {
			return nil, herr("not_found", "No such website on this node.")
		}
		n, err := copyTree(root, filepath.Join(stage, "files"))
		if err != nil {
			return nil, err
		}
		found["files"] = n
	}
	if kind == "database" || kind == "full" {
		if st == nil {
			return nil, herr("not_found", "No such account on this node.")
		}
		if err := os.MkdirAll(filepath.Join(stage, "databases"), 0o700); err != nil {
			return nil, err
		}
		names, err := a.Engine.Databases(account + "_")
		if err != nil {
			return nil, err
		}
		for _, name := range names {
			if err := a.Engine.Dump(name, filepath.Join(stage, "databases", name+".sql")); err != nil {
				return nil, err
			}
			found["databases"] = found["databases"].(int) + 1
		}
	}
	if kind == "full" {
		if err := os.MkdirAll(filepath.Join(stage, "config"), 0o700); err != nil {
			return nil, err
		}
		for _, path := range configPaths(account, st.text("domain")) {
			if text, ok := a.Sys.Read(path); ok {
				if err := os.WriteFile(filepath.Join(stage, "config", configName(path)), []byte(text), 0o600); err != nil {
					return nil, err
				}
			}
		}
	}
	if kind == "email" {
		if a.MailRoot == "" {
			return nil, herr("not_applicable", "This node does not hold mail.")
		}
		var states []*mailDomain
		for _, s := range a.mailAll() {
			if s.Account == account {
				states = append(states, s)
			}
		}
		if len(states) == 0 {
			return nil, herr("not_found", "No mail for this account on this node.")
		}
		for _, s := range states {
			dir := filepath.Join(stage, "mail", s.Domain)
			if err := os.MkdirAll(dir, 0o700); err != nil {
				return nil, err
			}
			if info, err := os.Stat(filepath.Join(a.MailRoot, s.Domain)); err == nil && info.IsDir() {
				n, err := copyTree(filepath.Join(a.MailRoot, s.Domain), dir)
				if err != nil {
					return nil, err
				}
				found["mail"] = found["mail"].(int) + n
			}
			settings := *s // the domain's settings, not its password hashes
			settings.Mailboxes = nil
			raw, _ := jsonMarshal(settings)
			if err := os.WriteFile(filepath.Join(stage, "mail", s.Domain+".json"), raw, 0o600); err != nil {
				return nil, err
			}
		}
	}
	return found, nil
}

func (a *Agent) owned(repo Repository, account, snapshot string) (string, error) {
	meta, err := repo.Meta(snapshot)
	if err != nil {
		return "", herr("repository_error", "%s", clip(err.Error(), 300))
	}
	if !contains(meta.Tags, "account:"+account) {
		return "", herr("forbidden", "That backup does not belong to this account.")
	}
	for _, t := range meta.Tags {
		if strings.HasPrefix(t, "kind:") {
			return strings.TrimPrefix(t, "kind:"), nil
		}
	}
	return "", nil
}

func (a *Agent) backupRun(p map[string]any) (Result, error) {
	repo, err := a.repo()
	if err != nil {
		return Result{}, err
	}
	stage, err := a.stage(str(p, "account"))
	if err != nil {
		return Result{}, err
	}
	defer os.RemoveAll(stage)
	found, err := a.gather(str(p, "account"), str(p, "kind"), stage)
	if err != nil {
		return Result{}, err
	}
	id, size, err := repo.Backup(stage, []string{"account:" + str(p, "account"), "kind:" + str(p, "kind"), "source:" + str(p, "source")})
	if err != nil {
		return Result{}, herr("repository_error", "%s", clip(err.Error(), 300))
	}
	data := map[string]any{"snapshot_id": id, "size_mb": float64(int64(float64(size)/1048576*100+0.5)) / 100}
	for k, v := range found {
		data[k] = v
	}
	return Result{"created", data}, nil
}

func (a *Agent) backupVerify(p map[string]any) (Result, error) {
	repo, err := a.repo()
	if err != nil {
		return Result{}, err
	}
	if _, err := a.owned(repo, str(p, "account"), str(p, "snapshot_id")); err != nil {
		return Result{}, err
	}
	if ok, detail := repo.Check(str(p, "snapshot_id")); !ok {
		return Result{}, herr("damaged", "%s", detail)
	} else {
		return Result{"verified", map[string]any{"detail": detail}}, nil
	}
}

func (a *Agent) backupForget(p map[string]any) (Result, error) {
	repo, err := a.repo()
	if err != nil {
		return Result{}, err
	}
	if _, err := a.owned(repo, str(p, "account"), str(p, "snapshot_id")); err != nil {
		return Result{}, err
	}
	gone, err := repo.Forget(str(p, "snapshot_id"))
	if err != nil {
		return Result{}, herr("repository_error", "%s", clip(err.Error(), 300))
	}
	if gone {
		return Result{"deleted", nil}, nil
	}
	return Result{"absent", nil}, nil
}

func (a *Agent) safetyBackup(repo Repository, account, kind string) (string, error) {
	stage, err := a.stage(account)
	if err != nil {
		return "", err
	}
	defer os.RemoveAll(stage)
	if _, err := a.gather(account, kind, stage); err != nil {
		return "", err
	}
	id, _, err := repo.Backup(stage, []string{"account:" + account, "kind:" + kind, "source:before_restore"})
	return id, err
}

func (a *Agent) backupRestore(p map[string]any) (Result, error) {
	account, kind := str(p, "account"), str(p, "kind")
	repo, err := a.repo()
	if err != nil {
		return Result{}, err
	}
	have, err := a.owned(repo, account, str(p, "snapshot_id"))
	if err != nil {
		return Result{}, err
	}
	if kind != have && !contains(covers[have], kind) {
		return Result{}, herr("invalid", "That backup holds %s, not %s.", have, kind)
	}
	if ok, detail := repo.Check(str(p, "snapshot_id")); !ok {
		return Result{}, herr("damaged", "This backup failed its check, so it was not restored: %s", detail)
	}
	wanted := covers[kind]
	safety, err := a.safetyBackup(repo, account, kind)
	if err != nil {
		return Result{}, err
	}
	scratch, err := a.stage(account)
	if err != nil {
		return Result{}, err
	}
	defer os.RemoveAll(scratch)
	data, err := repo.Restore(str(p, "snapshot_id"), scratch)
	if err != nil {
		return Result{}, herr("repository_error", "%s", clip(err.Error(), 300))
	}
	restored := map[string]any{"files": 0, "databases": 0, "mail": 0}
	if contains(wanted, "files") {
		if info, e := os.Stat(filepath.Join(data, "files")); e == nil && info.IsDir() {
			root := a.RootFor(account)
			items, _ := os.ReadDir(root)
			for _, it := range items {
				if it.Name() != ".trash" {
					if err := os.RemoveAll(filepath.Join(root, it.Name())); err != nil {
						return Result{}, err
					}
				}
			}
			n, err := copyTree(filepath.Join(data, "files"), root)
			if err != nil {
				return Result{}, err
			}
			restored["files"] = n
		}
	}
	if contains(wanted, "database") {
		dumps, _ := filepath.Glob(filepath.Join(data, "databases", "*.sql"))
		for _, dump := range dumps {
			name := strings.TrimSuffix(filepath.Base(dump), ".sql")
			if _, err := Ident(name); err != nil {
				return Result{}, err
			}
			if !strings.HasPrefix(name, account+"_") {
				return Result{}, herr("forbidden", "The backup holds a database that is not this account's.")
			}
			if err := a.Engine.Load(name, dump); err != nil {
				return Result{}, err
			}
			restored["databases"] = restored["databases"].(int) + 1
		}
	}
	if contains(wanted, "email") && a.MailRoot != "" {
		domains, _ := os.ReadDir(filepath.Join(data, "mail"))
		for _, d := range domains {
			if !d.IsDir() {
				continue
			}
			st := a.mailState(d.Name())
			if st == nil || st.Account != account {
				continue // mail for a domain that no longer belongs to the account is not put back
			}
			boxes, _ := os.ReadDir(filepath.Join(data, "mail", d.Name()))
			for _, b := range boxes {
				if _, ok := st.Mailboxes[b.Name()]; !ok || !b.IsDir() {
					continue
				}
				target := filepath.Join(a.MailRoot, d.Name(), b.Name())
				_ = os.RemoveAll(target)
				n, err := copyTree(filepath.Join(data, "mail", d.Name(), b.Name()), target)
				if err != nil {
					return Result{}, err
				}
				restored["mail"] = restored["mail"].(int) + n
			}
		}
	}
	restored["safety_snapshot_id"] = safety
	return Result{"restored", restored}, nil
}

func fileSHA(path string) (string, error) {
	f, err := os.Open(path)
	if err != nil {
		return "", err
	}
	defer f.Close()
	h := sha256.New()
	if _, err := io.Copy(h, f); err != nil {
		return "", err
	}
	return hex.EncodeToString(h.Sum(nil)), nil
}

// Manifest is every file's SHA-256 under a folder, by relative path.
func Manifest(root string) (map[string]string, error) {
	out := map[string]string{}
	err := filepath.WalkDir(root, func(p string, d fs.DirEntry, err error) error {
		if err != nil || d.IsDir() {
			return err
		}
		sum, err := fileSHA(p)
		if err != nil {
			return err
		}
		rel, _ := filepath.Rel(root, p)
		out[filepath.ToSlash(rel)] = sum
		return nil
	})
	return out, err
}

func (a *Agent) backupTestRestore(p map[string]any) (Result, error) {
	account := str(p, "account")
	repo, err := a.repo()
	if err != nil {
		return Result{}, err
	}
	if _, err := a.owned(repo, account, str(p, "snapshot_id")); err != nil {
		return Result{}, err
	}
	meta, _ := repo.Meta(str(p, "snapshot_id"))
	scratch, err := a.stage(account)
	if err != nil {
		return Result{}, err
	}
	defer os.RemoveAll(scratch)
	data, err := repo.Restore(str(p, "snapshot_id"), scratch)
	if err != nil {
		return Result{}, herr("restore_test_failed", "%s", clip(err.Error(), 200))
	}
	found, err := Manifest(data)
	if err != nil {
		return Result{}, herr("restore_test_failed", "%s", clip(err.Error(), 200))
	}
	if meta.Manifest != nil && !sameManifest(found, meta.Manifest) { // a real restic repository checks content itself and keeps no manifest here
		return Result{}, herr("restore_test_failed", "The restored files differ from the list made at backup time.")
	}
	return Result{"restore_ok", map[string]any{"files": len(found), "detail": "every file matched"}}, nil
}

func sameManifest(a, b map[string]string) bool {
	if len(a) != len(b) {
		return false
	}
	for k, v := range a {
		if b[k] != v {
			return false
		}
	}
	return true
}

func (a *Agent) backupNodeConfig(p map[string]any) (Result, error) {
	repo, err := a.repo()
	if err != nil {
		return Result{}, err
	}
	stage := filepath.Join(a.StageRoot, "nodeconfig-"+randomHex(4))
	if err := os.MkdirAll(stage, 0o700); err != nil {
		return Result{}, err
	}
	defer os.RemoveAll(stage)
	count := 0
	for _, prefix := range nodeConfigRoots {
		for _, path := range a.Sys.List(prefix) {
			if path == envFile || strings.HasSuffix(path, ".whp-tmp") {
				continue
			}
			if text, ok := a.Sys.Read(path); ok {
				if err := os.WriteFile(filepath.Join(stage, configName(path)), []byte(text), 0o600); err != nil {
					return Result{}, err
				}
				count++
			}
		}
	}
	id, size, err := repo.Backup(stage, []string{"node-config"})
	if err != nil {
		return Result{}, herr("repository_error", "%s", clip(err.Error(), 300))
	}
	return Result{"created", map[string]any{"snapshot_id": id, "files": count, "size_mb": float64(int64(float64(size)/1048576*100+0.5)) / 100}}, nil
}
