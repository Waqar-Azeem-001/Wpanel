package handlers

import (
	"os"
	"path/filepath"
	"reflect"
	"sort"
	"strings"
	"time"
	"testing"

	"whagent/internal/catalog"
	"whagent/internal/system"
)

// The importer does what the Python importer does: the same steps against the same synthetic cPanel archive give the same answers, the same files, databases, mail and commands.
func TestGoImporterDoesWhatThePythonImporterDoes(t *testing.T) {
	spec, err := catalog.Load()
	if err != nil {
		t.Fatal(err)
	}
	rawSpec, err := os.ReadFile("../../spec/migrate_golden.json")
	if err != nil {
		t.Fatal(err)
	}
	decoded, err := catalog.Decode(rawSpec)
	if err != nil {
		t.Fatal(err)
	}
	golden := decoded.(map[string]any)
	archiveBytes, err := os.ReadFile("../../spec/" + golden["archive"].(string))
	if err != nil {
		t.Fatal(err)
	}
	tmp := t.TempDir()
	stage := filepath.Join(tmp, "_stage", migrationDir)
	if err := os.MkdirAll(stage, 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(stage, golden["archive"].(string)), archiveBytes, 0o644); err != nil {
		t.Fatal(err)
	}
	if err := os.MkdirAll(filepath.Join(tmp, "abcd1234", "sites"), 0o755); err != nil {
		t.Fatal(err)
	}
	Sleep = func(d time.Duration) {}
	fake, engine := system.NewFake(), NewFakeEngine()
	agent := New(spec, fake)
	agent.Engine, agent.StageRoot, agent.MailRoot = engine, filepath.Join(tmp, "_stage"), filepath.Join(tmp, "_mail")
	agent.RootFor = func(account string) string {
		if agent.StateOf(account) == "" {
			return ""
		}
		return filepath.Join(tmp, account, "sites")
	}
	expected := golden["expected"].(map[string]any)
	want := expected["results"].([]any)
	for i, s := range golden["steps"].([]any) {
		step := s.(map[string]any)
		res, herr := agent.Run(step["command"].(string), step["params"])
		w, _ := want[i].(map[string]any)
		if w == nil { // a step whose answer is random (the mail domain's key) is only run
			if herr != nil {
				t.Fatalf("step %d %v: %v", i, step["command"], herr)
			}
			continue
		}
		if w["ok"].(bool) {
			if herr != nil {
				t.Fatalf("step %d %v: python succeeded, go failed: %v", i, step["command"], herr)
			}
			if res.Code != w["code"] {
				t.Fatalf("step %d %v: code %q != %q", i, step["command"], res.Code, w["code"])
			}
			if got, exp := roundTrip(t, res.Data), norm(w["data"]); !reflect.DeepEqual(got, exp) {
				t.Fatalf("step %d %v: data differ\n go:     %v\n python: %v", i, step["command"], got, exp)
			}
		} else {
			if herr == nil {
				t.Fatalf("step %d %v: python failed (%v), go succeeded", i, step["command"], w["code"])
			}
			if herr.Code != w["code"] || herr.Message != w["message"] {
				t.Fatalf("step %d %v: go %q %q, python %q %q", i, step["command"], herr.Code, herr.Message, w["code"], w["message"])
			}
		}
	}

	// every file the import (and the mail commands) put on disk
	root := filepath.ToSlash(tmp)
	gotTree := map[string]any{}
	for _, base := range []string{filepath.Join(tmp, "abcd1234"), filepath.Join(tmp, "_mail")} {
		_ = filepath.Walk(base, func(path string, info os.FileInfo, err error) error {
			if err == nil && info.Mode().IsRegular() && !strings.HasSuffix(path, ".json") {
				raw, _ := os.ReadFile(path)
				gotTree[strings.Replace(filepath.ToSlash(path), root, "<ROOT>", 1)] = string(raw)
			}
			return nil
		})
	}
	wantTree := expected["tree"].(map[string]any)
	if !reflect.DeepEqual(keys(gotTree), keys(wantTree)) {
		t.Fatalf("files differ\n go:     %v\n python: %v", keys(gotTree), keys(wantTree))
	}
	for path, text := range wantTree {
		if gotTree[path] != text {
			t.Fatalf("file %s differs\n go:     %q\n python: %q", path, gotTree[path], text)
		}
	}
	// the mail state
	st := agent.mailState("acme.com")
	wantMail := expected["mail"].(map[string]any)
	hashes := map[string]any{}
	for local, box := range st.Mailboxes {
		hashes[local] = box.Hash
	}
	if !reflect.DeepEqual(hashes, wantMail["mailboxes"].(map[string]any)) {
		t.Fatalf("mailboxes: %v vs %v", hashes, wantMail["mailboxes"])
	}
	var fw []string
	for _, f := range st.Forwarders {
		fw = append(fw, f.Source+">"+f.Destination)
	}
	sort.Strings(fw)
	var wantFw []string
	for _, f := range wantMail["forwarders"].([]any) {
		wantFw = append(wantFw, f.(string))
	}
	if !reflect.DeepEqual(fw, wantFw) {
		t.Fatalf("forwarders: %v vs %v", fw, wantFw)
	}
	// databases, users and grants
	gotDB := map[string]any{}
	for db, tables := range engine.DBs {
		ts := map[string]any{}
		for name, tb := range tables {
			ts[name] = tb.Rows
		}
		gotDB[db] = ts
	}
	if got, exp := roundTrip(t, gotDB), norm(expected["databases"]); !reflect.DeepEqual(got, exp) {
		t.Fatalf("databases differ\n go:     %v\n python: %v", got, exp)
	}
	if got, exp := roundTrip(t, engine.Users), norm(expected["users"]); !reflect.DeepEqual(got, exp) {
		t.Fatalf("users differ: %v vs %v", got, exp)
	}
	var grants []any
	for k, privs := range engine.Grants {
		var ps []string
		for p := range privs {
			ps = append(ps, p)
		}
		sort.Strings(ps)
		grants = append(grants, []any{k[0], k[1], ps})
	}
	sort.Slice(grants, func(i, j int) bool { return grants[i].([]any)[0].(string)+grants[i].([]any)[1].(string) < grants[j].([]any)[0].(string)+grants[j].([]any)[1].(string) })
	if got, exp := roundTrip(t, grants), norm(expected["grants"]); !reflect.DeepEqual(got, exp) {
		t.Fatalf("grants differ: %v vs %v", got, exp)
	}
	// the ownership commands, in order, with the temporary folder named <ROOT>
	var gotCmds [][]string
	for _, c := range fake.Commands {
		if len(c) > 0 && (c[0] == "chown" || c[0] == "chmod") {
			row := make([]string, len(c))
			for i, a := range c {
				row[i] = strings.Replace(filepath.ToSlash(a), root, "<ROOT>", 1)
			}
			gotCmds = append(gotCmds, row)
		}
	}
	var wantCmds [][]string
	for _, c := range expected["commands"].([]any) {
		var row []string
		for _, a := range c.([]any) {
			row = append(row, a.(string))
		}
		wantCmds = append(wantCmds, row)
	}
	if !reflect.DeepEqual(gotCmds, wantCmds) {
		t.Fatalf("commands differ\n go:     %v\n python: %v", gotCmds, wantCmds)
	}
}

func TestAnArchiveThatLeadsOutsideItselfIsRefused(t *testing.T) {
	if _, err := cleanMember("cpmove-x/../../escape"); err == nil {
		t.Fatal("a path with .. must be refused")
	}
	if _, err := cleanMember("/abs/path"); err == nil {
		t.Fatal("an absolute path must be refused")
	}
	if got, _ := cleanMember("cpmove-x/homedir/a.txt"); got != "homedir/a.txt" {
		t.Fatal(got)
	}
}

func TestGrantAndShadowParsers(t *testing.T) {
	hashes, grants, unsupported := parseMysqlGrants("GRANT USAGE ON *.* TO 'u'@'localhost' IDENTIFIED BY PASSWORD '*" + strings.Repeat("A", 40) + "';\n" +
		"GRANT SELECT, LOCK TABLES, EVENT ON `d\\_x`.* TO 'u'@'localhost';\n")
	if hashes["u"] != "*"+strings.Repeat("A", 40) || len(grants) != 1 || grants[0].db != "d_x" || !reflect.DeepEqual(grants[0].privs, []string{"select"}) ||
		!reflect.DeepEqual(unsupported, []string{"EVENT", "LOCK TABLES"}) {
		t.Fatalf("%v %v %v", hashes, grants, unsupported)
	}
	if got := parseShadow("info:$6$s$h:1\nbroken:*:1\n"); len(got) != 1 || got["info"] != "$6$s$h" {
		t.Fatal(got)
	}
	if got := parseValiases("sales@a.com: info@a.com, x|y\n"); len(got) != 1 || got[0].destination != "info@a.com" {
		t.Fatal(got)
	}
}
