package handlers

import (
	"encoding/json"
	"strconv"
	"crypto/sha1"
	"crypto/sha512"
	"encoding/hex"
	"fmt"
	"os"
	"path/filepath"
	"reflect"
	"sort"
	"strings"
	"testing"

	"whagent/internal/catalog"
	"whagent/internal/system"
)

// The Go installer does what the Python installer does on the same scenarios: same answers, same files, same database objects, same programs run in the same order.
func TestGoInstallerDoesWhatThePythonInstallerDoes(t *testing.T) {
	spec, err := catalog.Load()
	if err != nil {
		t.Fatal(err)
	}
	raw, err := os.ReadFile("../../spec/wordpress_golden.json")
	if err != nil {
		t.Fatal(err)
	}
	decoded, err := catalog.Decode(raw)
	if err != nil {
		t.Fatal(err)
	}
	golden := decoded.(map[string]any)
	good, err := os.ReadFile("../../spec/" + golden["archive"].(string))
	if err != nil {
		t.Fatal(err)
	}
	evil, err := os.ReadFile("../../spec/" + golden["evil"].(string))
	if err != nil {
		t.Fatal(err)
	}
	cli := []byte("#!/usr/bin/env php\n<?php // pretend wp-cli\n")

	for _, sc := range golden["scenarios"].([]any) {
		scenario := sc.(map[string]any)
		t.Run(scenario["name"].(string), func(t *testing.T) {
			tmp := t.TempDir()
			options := scenario["fetch"].(map[string]any)
			archive := good
			if options["evil"] == true {
				archive = evil
			}
			sum1 := sha1.Sum(archive)
			sum512 := sha512.Sum512(cli)
			checksum := hex.EncodeToString(sum1[:])
			if options["tamper"] == true {
				checksum = strings.Repeat("0", 40)
			}
			table := map[string][]byte{wpArchive: archive, wpChecksum: []byte(checksum + "  latest.tar.gz\n"), wpCLIPhar: cli, wpCLISum: []byte(hex.EncodeToString(sum512[:]) + "  wp-cli.phar\n")}

			fake, engine := system.NewFake(), NewFakeEngine()
			agent := New(spec, fake)
			agent.Engine, agent.StageRoot, agent.ToolsDir = engine, filepath.Join(tmp, "_stage"), filepath.Join(tmp, "_tools")
			agent.RootFor = func(account string) string {
				if agent.StateOf(account) == "" {
					return ""
				}
				return filepath.Join(tmp, account, "sites")
			}
			agent.Fetch = func(url string) ([]byte, error) {
				if options["down"] == true {
					return nil, fmt.Errorf("network is unreachable")
				}
				if data, ok := table[url]; ok {
					return data, nil
				}
				return nil, fmt.Errorf("unexpected address %s", url)
			}
			counter := 0
			agent.Token = func(n int) string { // the same counting strings as the Python tests
				counter++
				s := fmt.Sprintf("tok%05d", counter)
				for len(s) < n {
					s += "x"
				}
				return s[:n]
			}
			limit := int64(3)
			if l, ok := scenario["limits"].(map[string]any); ok {
				n, _ := l["databases"].(json.Number).Int64()
				limit = n
			}
			if _, e := agent.Run("CREATE_WEBSITE", map[string]any{"account": "abcd1234", "domain": "acme.com", "php_version": "8.3", "databases": json.Number(strconv.FormatInt(limit, 10))}); e != nil {
				t.Fatal(e)
			}
			docroot := filepath.Join(tmp, "abcd1234", "sites", "acme.com", "public_html")
			if err := os.MkdirAll(docroot, 0o755); err != nil {
				t.Fatal(err)
			}
			if err := os.WriteFile(filepath.Join(docroot, "index.html"), []byte("placeholder"), 0o644); err != nil {
				t.Fatal(err)
			}
			for rel, text := range scenario["files"].(map[string]any) {
				if err := os.WriteFile(filepath.Join(docroot, rel), []byte(text.(string)), 0o644); err != nil {
					t.Fatal(err)
				}
			}
			expected := scenario["expected"].(map[string]any)
			want := expected["results"].([]any)
			for i, s := range scenario["steps"].([]any) {
				step := s.(map[string]any)
				res, herr := agent.Run(step["command"].(string), step["params"])
				w := want[i].(map[string]any)
				if w["ok"].(bool) {
					if herr != nil {
						t.Fatalf("step %d: python succeeded, go failed: %v", i, herr)
					}
					if res.Code != w["code"] {
						t.Fatalf("step %d: code %q != %q", i, res.Code, w["code"])
					}
					if got, exp := roundTrip(t, res.Data), norm(w["data"]); !reflect.DeepEqual(got, exp) {
						t.Fatalf("step %d: data differ\n go:     %v\n python: %v", i, got, exp)
					}
				} else {
					if herr == nil {
						t.Fatalf("step %d: python failed (%v), go succeeded", i, w["code"])
					}
					if herr.Code != w["code"] || herr.Message != w["message"] {
						t.Fatalf("step %d: go %q %q, python %q %q", i, herr.Code, herr.Message, w["code"], w["message"])
					}
				}
			}
			// the website folder, file by file
			got := map[string]any{}
			_ = filepath.Walk(docroot, func(path string, info os.FileInfo, err error) error {
				if err == nil && info.Mode().IsRegular() {
					data, _ := os.ReadFile(path)
					rel, _ := filepath.Rel(tmp, path)
					got[filepath.ToSlash(rel)] = string(data)
				}
				return nil
			})
			wantTree := expected["tree"].(map[string]any)
			if !reflect.DeepEqual(keys(got), keys(wantTree)) {
				t.Fatalf("files differ\n go:     %v\n python: %v", keys(got), keys(wantTree))
			}
			for path, text := range wantTree {
				if got[path] != text {
					t.Fatalf("file %s differs\n go:     %q\n python: %q", path, got[path], text)
				}
			}
			// the database objects
			dbs, _ := engine.Databases("")
			sort.Strings(dbs)
			var wantDBs []string
			for _, d := range expected["databases"].([]any) {
				wantDBs = append(wantDBs, d.(string))
			}
			if !(len(dbs) == 0 && len(wantDBs) == 0) && !reflect.DeepEqual(dbs, wantDBs) {
				t.Fatalf("databases: %v vs %v", dbs, wantDBs)
			}
			users, _ := engine.UsersWith("")
			sort.Strings(users)
			var wantUsers []string
			for _, u := range expected["users"].([]any) {
				wantUsers = append(wantUsers, u.(string))
			}
			if !(len(users) == 0 && len(wantUsers) == 0) && !reflect.DeepEqual(users, wantUsers) {
				t.Fatalf("users: %v vs %v", users, wantUsers)
			}
			// the programs it ran, in order, with the temporary folder called <ROOT>
			root := filepath.ToSlash(tmp)
			var gotCmds [][]string
			for _, c := range fake.Commands {
				if len(c) > 0 && (c[0] == "chown" || c[0] == "chmod" || c[0] == "systemd-run") {
					row := make([]string, len(c))
					for i, a := range c {
						row[i] = strings.ReplaceAll(filepath.ToSlash(a), root, "<ROOT>")
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
			if !reflect.DeepEqual(gotCmds, wantCmds) && !(len(gotCmds) == 0 && len(wantCmds) == 0) {
				t.Fatalf("commands differ\n go:     %v\n python: %v", gotCmds, wantCmds)
			}
			// the administrator's password went to the installer on standard input only
			for _, c := range fake.Commands {
				if strings.Contains(strings.Join(c, " "), "Kq7vRt2mXzLp-91") {
					t.Fatalf("the password is in a command: %v", c)
				}
			}
		})
	}
}
