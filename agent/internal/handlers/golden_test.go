package handlers

import (
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"reflect"
	"sort"
	"strconv"
	"strings"
	"testing"
	"time"

	"whagent/internal/catalog"
	"whagent/internal/system"
)

// The Python reference agent ran 44 scenarios on a pretend machine and recorded everything: each result, every file, every program run in order. The Go agent runs the same scenarios and
// must produce exactly the same record. A difference is a bug in one of the two.

type scenario struct {
	Name    string   `json:"name"`
	Folders []string `json:"folders"`
	Steps   []map[string]any
	Exp     struct {
		Results  []map[string]any
		Files    map[string]any
		Dirs     []string
		Users    map[string]json.Number
		Commands [][]string
	} `json:"expected"`
}

func loadGolden(t *testing.T) []map[string]any {
	t.Helper()
	raw, err := os.ReadFile("../../spec/golden.json")
	if err != nil {
		t.Fatal(err)
	}
	v, err := catalog.Decode(raw)
	if err != nil {
		t.Fatal(err)
	}
	var out []map[string]any
	for _, s := range v.([]any) {
		out = append(out, s.(map[string]any))
	}
	return out
}

// norm turns any decoded JSON into a comparable form: numbers as text, empty and null values dropped.
func norm(v any) any {
	switch x := v.(type) {
	case map[string]any:
		out := map[string]any{}
		for k, val := range x {
			n := norm(val)
			if n == nil {
				continue
			}
			if m, ok := n.(map[string]any); ok && len(m) == 0 {
				continue
			}
			if l, ok := n.([]any); ok && len(l) == 0 {
				continue
			}
			out[k] = n
		}
		return out
	case []any:
		out := make([]any, len(x))
		for i, val := range x {
			out[i] = norm(val)
		}
		return out
	case json.Number:
		f, _ := x.Float64() // 0.0 and 0 are the same number
		return strconv.FormatFloat(f, 'g', -1, 64)
	case float64:
		return strconv.FormatFloat(x, 'g', -1, 64)
	case int64:
		return fmt.Sprint(x)
	case int:
		return fmt.Sprint(x)
	}
	return v
}

func roundTrip(t *testing.T, v any) any {
	t.Helper()
	raw, err := json.Marshal(v)
	if err != nil {
		t.Fatal(err)
	}
	back, err := catalog.Decode(raw)
	if err != nil {
		t.Fatal(err)
	}
	return norm(back)
}

func TestGoAgentDoesWhatThePythonAgentDoes(t *testing.T) {
	spec, err := catalog.Load()
	if err != nil {
		t.Fatal(err)
	}
	Sleep = func(d time.Duration) {}
	for _, raw := range loadGolden(t) {
		name := raw["name"].(string)
		t.Run(name, func(t *testing.T) {
			tmp := t.TempDir()
			for _, f := range raw["folders"].([]any) {
				if err := os.MkdirAll(filepath.Join(tmp, "abcd1234", "sites", f.(string)), 0o755); err != nil {
					t.Fatal(err)
				}
			}
			_ = os.MkdirAll(filepath.Join(tmp, "abcd1234", "sites"), 0o755)
			fake, engine := system.NewFake(), NewFakeEngine()
			agent := New(spec, fake)
			agent.Engine = engine
			agent.Now = func() time.Time { return time.Unix(1_800_000_000, 0) }
			agent.RootFor = func(account string) string {
				p := filepath.Join(tmp, account, "sites")
				if info, err := os.Stat(p); err == nil && info.IsDir() {
					return p
				}
				return ""
			}
			expected := raw["expected"].(map[string]any)
			wantResults := expected["results"].([]any)
			index := 0
			for _, s := range raw["steps"].([]any) {
				step := s.(map[string]any)
				if seed, ok := step["seed_table"].(map[string]any); ok {
					var cols []Column
					for _, c := range seed["columns"].([]any) {
						c := c.([]any)
						col := Column{Name: c[0].(string), Type: c[1].(string), Nullable: c[2].(bool), Primary: c[3].(bool)}
						if c[4] != nil {
							d := c[4].(string)
							col.Default = &d
						}
						cols = append(cols, col)
					}
					var rows [][]any
					for _, r := range seed["rows"].([]any) {
						rows = append(rows, r.([]any))
					}
					engine.AddTable(seed["db"].(string), seed["table"].(string), cols, rows)
					continue
				}
				if states, ok := step["service_state"].(map[string]any); ok {
					for k, v := range states {
						fake.ServiceState[k] = v.(string)
					}
				}
				fake.FailOn = -1
				if off, ok := step["fail_offset"]; ok {
					n, _ := off.(json.Number).Int64()
					fake.FailOn = len(fake.Commands) + int(n)
				}
				res, herr := agent.Run(step["command"].(string), step["params"])
				fake.FailOn = -1
				want := wantResults[index].(map[string]any)
				index++
				if want["ok"].(bool) {
					if herr != nil {
						t.Fatalf("step %d %v: python succeeded (%v), go failed: %v", index, step["command"], want["code"], herr)
					}
					if res.Code != want["code"] {
						t.Fatalf("step %d %v: code %q != %q", index, step["command"], res.Code, want["code"])
					}
					if got, exp := roundTrip(t, res.Data), norm(want["data"]); !reflect.DeepEqual(got, exp) {
						t.Fatalf("step %d %v: data differ\n go:     %v\n python: %v", index, step["command"], got, exp)
					}
				} else {
					if herr == nil {
						t.Fatalf("step %d %v: python failed (%v: %v), go succeeded", index, step["command"], want["code"], want["message"])
					}
					if herr.Code != want["code"] {
						t.Fatalf("step %d %v: error code %q != %q (%s)", index, step["command"], herr.Code, want["code"], herr.Message)
					}
					if want["code"] != "invalid" && herr.Message != want["message"] {
						t.Fatalf("step %d %v: message\n go:     %q\n python: %q", index, step["command"], herr.Message, want["message"])
					}
				}
			}
			// the whole record of what happened to the machine
			wantFiles := expected["files"].(map[string]any)
			gotFiles := map[string]any{}
			for path, text := range fake.Files {
				if strings.HasSuffix(path, ".json") {
					v, err := catalog.Decode([]byte(text))
					if err != nil {
						t.Fatalf("%s is not JSON: %v", path, err)
					}
					gotFiles[path] = norm(v)
				} else {
					gotFiles[path] = text
				}
			}
			for path, want := range wantFiles {
				if strings.HasSuffix(path, ".json") {
					wantFiles[path] = norm(want)
				}
			}
			if !reflect.DeepEqual(keys(gotFiles), keys(wantFiles)) {
				t.Fatalf("files differ\n go:     %v\n python: %v", keys(gotFiles), keys(wantFiles))
			}
			for path, want := range wantFiles {
				if !reflect.DeepEqual(gotFiles[path], want) {
					t.Fatalf("file %s differs\n go:     %v\n python: %v", path, gotFiles[path], want)
				}
			}
			var dirs []string
			for d := range fake.Dirs {
				dirs = append(dirs, d)
			}
			sort.Strings(dirs)
			var wantDirs []string
			for _, d := range expected["dirs"].([]any) {
				wantDirs = append(wantDirs, d.(string))
			}
			if !reflect.DeepEqual(dirs, wantDirs) && !(len(dirs) == 0 && len(wantDirs) == 0) {
				t.Fatalf("folders differ\n go:     %v\n python: %v", dirs, wantDirs)
			}
			for user, uid := range expected["users"].(map[string]any) {
				if got, ok := fake.Users[user]; !ok || fmt.Sprint(got) != uid.(json.Number).String() {
					t.Fatalf("user %s: go %v, python %v", user, got, uid)
				}
			}
			if len(fake.Users) != len(expected["users"].(map[string]any)) {
				t.Fatalf("users differ: %v vs %v", fake.Users, expected["users"])
			}
			wantCommands := expected["commands"].([]any)
			if len(fake.Commands) != len(wantCommands) {
				t.Fatalf("number of commands: go %d, python %d\n go:     %v\n python: %v", len(fake.Commands), len(wantCommands), fake.Commands, wantCommands)
			}
			for i, c := range wantCommands {
				var want []string
				for _, p := range c.([]any) {
					want = append(want, p.(string))
				}
				if !reflect.DeepEqual(fake.Commands[i], want) {
					t.Fatalf("command %d differs\n go:     %v\n python: %v", i, fake.Commands[i], want)
				}
			}
		})
	}
}

func keys(m map[string]any) []string {
	out := make([]string, 0, len(m))
	for k := range m {
		out = append(out, k)
	}
	sort.Strings(out)
	return out
}
