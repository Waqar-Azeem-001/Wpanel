package catalog

import (
	"os"
	"testing"
)

// TestAgreesWithPython runs every case WHPanel's Python catalogue answered (valid and invalid commands) through the Go validator: they must agree on all of them.
func TestAgreesWithPython(t *testing.T) {
	s, err := Load()
	if err != nil {
		t.Fatal(err)
	}
	raw, err := os.ReadFile("../../spec/validation_cases.json")
	if err != nil {
		t.Fatal(err)
	}
	decoded, err := Decode(raw)
	if err != nil {
		t.Fatal(err)
	}
	cases := decoded.([]any)
	if len(cases) < 3000 {
		t.Fatalf("expected thousands of cases, got %d", len(cases))
	}
	wrong := 0
	for i, c := range cases {
		m := c.(map[string]any)
		command, _ := m["command"].(string) // a missing or non-text command can never be allowed
		_, err := s.Validate(command, m["params"])
		want := m["ok"].(bool)
		if (err == nil) != want {
			wrong++
			if wrong <= 15 {
				t.Errorf("case %d: %s %v: python ok=%v, go error=%v", i, command, m["params"], want, err)
			}
		}
	}
	if wrong > 0 {
		t.Fatalf("%d of %d cases disagree with Python", wrong, len(cases))
	}
}

func TestEveryCommandHasATimeoutAndNoFreeShellField(t *testing.T) {
	s, _ := Load()
	if len(s.Commands) < 60 {
		t.Fatalf("only %d commands loaded", len(s.Commands))
	}
	for name, c := range s.Commands {
		if c.Timeout <= 0 {
			t.Errorf("%s has no timeout", name)
		}
		for f, rule := range c.Fields {
			switch rule.Kind {
			case "relpath", "path_list", "b64", "query", "text":
				ok := false
				for _, prefix := range []string{"FILE_", "SFTP_", "MAIL_"} {
					if len(name) >= len(prefix) && name[:len(prefix)] == prefix {
						ok = true
					}
				}
				if !ok {
					t.Errorf("%s.%s carries free text but is not a file, SFTP or mail command", name, f)
				}
			}
		}
	}
}

func TestScrubHidesSecrets(t *testing.T) {
	s, _ := Load()
	out := s.Scrub("CREATE_MAILBOX", map[string]any{"password": "hunter2hunter2", "domain": "acme.com"})
	if out["password"] != "***" || out["domain"] != "acme.com" {
		t.Fatalf("%v", out)
	}
}
