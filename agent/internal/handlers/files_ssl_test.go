package handlers

import (
	"encoding/base64"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"whagent/internal/catalog"
	"whagent/internal/system"
)

// rig is an agent on a pretend machine with one website created and a real folder for its files.
type rig struct {
	t     *testing.T
	agent *Agent
	fake  *system.Fake
	root  string
	acme  *FakeAcme
}

func newRig(t *testing.T) *rig {
	t.Helper()
	spec, err := catalog.Load()
	if err != nil {
		t.Fatal(err)
	}
	Sleep = func(time.Duration) {}
	tmp := t.TempDir()
	fake := system.NewFake()
	a := New(spec, fake)
	a.Engine = NewFakeEngine()
	a.RootFor = func(account string) string {
		p := filepath.Join(tmp, account, "sites")
		if info, err := os.Stat(p); err == nil && info.IsDir() {
			return p
		}
		return ""
	}
	acme := &FakeAcme{}
	a.Acme = acme
	r := &rig{t, a, fake, filepath.Join(tmp, "abcd1234", "sites"), acme}
	r.must("CREATE_WEBSITE", map[string]any{"account": "abcd1234", "domain": "acme.com", "php_version": "8.3"})
	if err := os.MkdirAll(filepath.Join(r.root, "acme.com", "public_html"), 0o755); err != nil {
		t.Fatal(err)
	}
	return r
}

// call runs a command with parameters given as plain Go values (numbers become JSON numbers, as they would arrive).
func (r *rig) call(command string, params map[string]any) (Result, *Error) {
	r.t.Helper()
	raw, err := catalog.Decode(mustJSON(params))
	if err != nil {
		r.t.Fatal(err)
	}
	return r.agent.Run(command, raw)
}

func (r *rig) must(command string, params map[string]any) Result {
	r.t.Helper()
	res, err := r.call(command, params)
	if err != nil {
		r.t.Fatalf("%s: %v", command, err)
	}
	return res
}

func (r *rig) fails(command string, params map[string]any, code string) *Error {
	r.t.Helper()
	_, err := r.call(command, params)
	if err == nil || err.Code != code {
		r.t.Fatalf("%s: expected %q, got %v", command, code, err)
	}
	return err
}

func b64(s string) string { return base64.StdEncoding.EncodeToString([]byte(s)) }

func TestFileCommandsWorkOnRealFolders(t *testing.T) {
	r := newRig(t)
	base := "acme.com/public_html"
	a := func(extra map[string]any) map[string]any {
		out := map[string]any{"account": "abcd1234"}
		for k, v := range extra {
			out[k] = v
		}
		return out
	}
	r.must("FILE_WRITE", a(map[string]any{"path": base + "/a.txt", "content": b64("hello"), "overwrite": "no"}))
	r.fails("FILE_WRITE", a(map[string]any{"path": base + "/a.txt", "content": b64("x"), "overwrite": "no"}), "file_error")
	r.must("FILE_WRITE", a(map[string]any{"path": base + "/a.txt", "content": b64("hello world"), "overwrite": "yes"}))
	got := r.must("FILE_READ", a(map[string]any{"path": base + "/a.txt", "max_bytes": 1000}))
	if got.Data["content"] != b64("hello world") {
		t.Fatalf("%v", got.Data)
	}
	r.fails("FILE_READ", a(map[string]any{"path": base + "/a.txt", "max_bytes": 3}), "file_error")
	r.fails("FILE_WRITE", a(map[string]any{"path": base + "/b.txt", "content": "***not base64***", "overwrite": "no"}), "invalid")
	r.must("FILE_MKDIR", a(map[string]any{"path": base + "/css"}))
	r.must("FILE_COPY", a(map[string]any{"path": base + "/css", "new_path": base + "/css2"}))
	r.must("FILE_RENAME", a(map[string]any{"path": base + "/css2", "new_path": base + "/styles"}))
	list := r.must("FILE_LIST", a(map[string]any{"path": base}))
	if n := len(list.Data["entries"].([]any)); n != 3 {
		t.Fatalf("expected 3 entries, got %d", n)
	}
	r.must("FILE_CHMOD", a(map[string]any{"path": base + "/a.txt", "mode": "600"}))
	r.fails("FILE_CHMOD", a(map[string]any{"path": base + "/a.txt", "mode": "777"}), "file_error")
	n := r.must("FILE_ARCHIVE", a(map[string]any{"paths": []any{base + "/a.txt"}, "archive_path": base + "/out.zip"}))
	if n.Data["count"] != 1 {
		t.Fatalf("%v", n.Data)
	}
	r.must("FILE_MKDIR", a(map[string]any{"path": base + "/unz"}))
	r.must("FILE_EXTRACT", a(map[string]any{"path": base + "/out.zip", "destination": base + "/unz"}))
	if data, _ := os.ReadFile(filepath.Join(r.root, "acme.com", "public_html", "unz", "a.txt")); string(data) != "hello world" {
		t.Fatalf("extracted %q", data)
	}
	found := r.must("FILE_SEARCH", a(map[string]any{"path": "", "query": "a.t"}))
	if len(found.Data["entries"].([]any)) < 2 {
		t.Fatalf("%v", found.Data)
	}
	id := r.must("FILE_TRASH", a(map[string]any{"path": base + "/a.txt"})).Data["item_id"].(string)
	if trash := r.must("FILE_TRASH_LIST", a(nil)); len(trash.Data["items"].([]any)) != 1 {
		t.Fatalf("%v", trash.Data)
	}
	r.must("FILE_TRASH_RESTORE", a(map[string]any{"item_id": id}))
	r.must("FILE_DELETE", a(map[string]any{"path": base + "/styles"}))
	r.must("FILE_TRASH", a(map[string]any{"path": base + "/a.txt"}))
	r.must("FILE_TRASH_PURGE", a(map[string]any{"item_id": "ALL"}))
	if len(r.must("FILE_TRASH_LIST", a(nil)).Data["items"].([]any)) != 0 {
		t.Fatal("trash not emptied")
	}
}

func TestFileCommandsRefuseAnythingOutsideTheAccount(t *testing.T) {
	r := newRig(t)
	for _, path := range []string{"../x", "/etc/passwd", "a/../../b", "c:/x"} {
		r.fails("FILE_READ", map[string]any{"account": "abcd1234", "path": path, "max_bytes": 10}, "invalid") // refused by the catalogue before anything runs
	}
	r.fails("FILE_LIST", map[string]any{"account": "ghost999", "path": ""}, "not_found")
	r.fails("FILE_DELETE", map[string]any{"account": "abcd1234", "path": ""}, "file_error") // the main folder itself
	if _, err := os.Stat(filepath.Dir(r.root)); err != nil {
		t.Fatal("the account folder must still exist")
	}
}

func TestACertificateIsIssuedInstalledReadAndRenewed(t *testing.T) {
	r := newRig(t)
	a := map[string]any{"account": "abcd1234"}
	st := r.must("SSL_STATUS", a)
	if st.Data["certificates"].([]any)[0].(map[string]any)["status"] != "none" {
		t.Fatalf("%v", st.Data)
	}
	issued := r.must("ISSUE_SSL", map[string]any{"account": "abcd1234", "domain": "acme.com", "method": "http-01"})
	row := issued.Data
	if row["status"] != "active" || row["issuer"] != "Fake ACME CA" || row["auto_renew"] != true || row["https_redirect"] != false {
		t.Fatalf("%v", row)
	}
	if days := row["days_left"].(int64); days < 89 || days > 90 {
		t.Fatalf("days left %d", days)
	}
	if got := r.acme.Calls[0]; got[0] != "/home/abcd1234/sites/acme.com/public_html" || got[1] != "http-01" || got[2] != "acme.com" || got[3] != "www.acme.com" {
		t.Fatalf("%v", got)
	}
	if text, _ := r.fake.Read("/etc/httpd/conf.d/whp/abcd1234-acme.com.conf"); !strings.Contains(text, "<VirtualHost *:443>") || strings.Contains(text, "R=301") {
		t.Fatal("the 443 host must exist, with no redirect yet")
	}
	keyWritten := false
	for _, c := range r.fake.Commands {
		if len(c) == 4 && c[0] == "write" && strings.HasSuffix(c[1], "privkey.pem") && c[2] == "0o600" && c[3] == "root" {
			keyWritten = true
		}
	}
	if !keyWritten {
		t.Fatal("the key must be written for root only")
	}
	r.must("SET_SSL_OPTIONS", map[string]any{"account": "abcd1234", "domain": "acme.com", "auto_renew": "yes", "https_redirect": "yes"})
	text, _ := r.fake.Read("/etc/httpd/conf.d/whp/abcd1234-acme.com.conf")
	if !strings.Contains(text, "R=301") || !strings.Contains(text, "acme-challenge") {
		t.Fatal("the redirect must leave the validation path open")
	}
	r.must("SUSPEND_WEBSITE", a)
	r.must("UNSUSPEND_WEBSITE", a)
	if text, _ := r.fake.Read("/etc/httpd/conf.d/whp/abcd1234-acme.com.conf"); !strings.Contains(text, "R=301") {
		t.Fatal("a suspension must not lose the HTTPS settings")
	}
	// an old certificate is shown as expiring and renewed by the daily job
	r.acme.IssuedAt = time.Now().Add(-70 * 24 * time.Hour)
	r.must("ISSUE_SSL", map[string]any{"account": "abcd1234", "domain": "acme.com", "method": "http-01"})
	if row := r.must("SSL_STATUS", a).Data["certificates"].([]any)[0].(map[string]any); row["days_left"].(int64) > 21 {
		t.Fatalf("%v", row)
	}
	r.acme.IssuedAt = time.Time{}
	reloads := countCommand(r.fake, "systemctl", "reload", "httpd")
	done := r.agent.RenewDue([]string{"abcd1234", "nobody123"})
	if len(done) != 1 || done[0].Outcome != "renewed" || countCommand(r.fake, "systemctl", "reload", "httpd") != reloads+1 {
		t.Fatalf("%v", done)
	}
	if again := r.agent.RenewDue([]string{"abcd1234"}); len(again) != 0 {
		t.Fatalf("a fresh certificate must not be renewed: %v", again)
	}
	r.must("REMOVE_SSL", map[string]any{"account": "abcd1234", "domain": "acme.com"})
	if res := r.must("REMOVE_SSL", map[string]any{"account": "abcd1234", "domain": "acme.com"}); res.Code != "absent" {
		t.Fatal("removing twice is fine")
	}
	if text, _ := r.fake.Read("/etc/httpd/conf.d/whp/abcd1234-acme.com.conf"); strings.Contains(text, "443") {
		t.Fatal("HTTPS must be gone")
	}
}

func countCommand(f *system.Fake, parts ...string) int {
	n := 0
	for _, c := range f.Commands {
		if strings.Join(c, " ") == strings.Join(parts, " ") {
			n++
		}
	}
	return n
}

func TestRenewalRespectsTheSwitchAndFailuresDoNotStopOthers(t *testing.T) {
	r := newRig(t)
	r.acme.IssuedAt = time.Now().Add(-80 * 24 * time.Hour)
	r.must("ISSUE_SSL", map[string]any{"account": "abcd1234", "domain": "acme.com", "method": "http-01"})
	r.must("SET_SSL_OPTIONS", map[string]any{"account": "abcd1234", "domain": "acme.com", "auto_renew": "no", "https_redirect": "no"})
	if done := r.agent.RenewDue([]string{"abcd1234"}); len(done) != 0 {
		t.Fatalf("switched off: %v", done)
	}
	r.must("SET_SSL_OPTIONS", map[string]any{"account": "abcd1234", "domain": "acme.com", "auto_renew": "yes", "https_redirect": "no"})
	r.acme.Fail = &AcmeError{"acme_failed", "boom"}
	r.acme.IssuedAt = time.Time{}
	if done := r.agent.RenewDue([]string{"abcd1234"}); len(done) != 1 || done[0].Outcome != "acme_failed" {
		t.Fatalf("%v", done)
	}
	r.fails("ISSUE_SSL", map[string]any{"account": "abcd1234", "domain": "acme.com", "method": "http-01"}, "acme_failed")
	r.fails("SET_SSL_OPTIONS", map[string]any{"account": "ghost999", "domain": "acme.com", "auto_renew": "yes", "https_redirect": "no"}, "not_found")
}

func TestASuspendedSiteGetsNoCertificateAndOptionsNeedOne(t *testing.T) {
	r := newRig(t)
	r.fails("SET_SSL_OPTIONS", map[string]any{"account": "abcd1234", "domain": "acme.com", "auto_renew": "yes", "https_redirect": "yes"}, "no_certificate")
	r.must("SUSPEND_WEBSITE", map[string]any{"account": "abcd1234"})
	r.fails("ISSUE_SSL", map[string]any{"account": "abcd1234", "domain": "acme.com", "method": "http-01"}, "suspended")
}
