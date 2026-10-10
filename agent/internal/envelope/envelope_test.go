package envelope

import (
	"os"
	"path/filepath"
	"testing"
	"time"

	"whagent/internal/catalog"
)

type fixture struct {
	PublicKey string `json:"public_key"`
	Node      int64  `json:"node"`
	Now       int64  `json:"now"`
	Cases     []struct {
		Name     string   `json:"name"`
		Envelope Envelope `json:"envelope"`
		Ok       bool     `json:"ok"`
	} `json:"cases"`
}

func load(t *testing.T) (*catalog.Spec, fixture) {
	t.Helper()
	s, err := catalog.Load()
	if err != nil {
		t.Fatal(err)
	}
	raw, err := os.ReadFile("../../spec/envelopes.json")
	if err != nil {
		t.Fatal(err)
	}
	var f fixture
	if err := jsonUnmarshal(raw, &f); err != nil {
		t.Fatal(err)
	}
	return s, f
}

// TestAgreesWithPython verifies envelopes signed by WHPanel's Python code (good and bad) and demands the same verdict for each.
func TestAgreesWithPython(t *testing.T) {
	s, f := load(t)
	key, err := PublicKey(f.PublicKey)
	if err != nil {
		t.Fatal(err)
	}
	now := time.Unix(f.Now, 0)
	for _, c := range f.Cases {
		p, err := Verify(s, c.Envelope, key, f.Node, NewGuard(""), now)
		if (err == nil) != c.Ok {
			t.Errorf("%s: python ok=%v, go error=%v", c.Name, c.Ok, err)
		}
		if c.Ok && err == nil && (p.Command != "CREATE_DATABASE" || p.Params["name"] != "shop_db") {
			t.Errorf("%s: wrong content %+v", c.Name, p)
		}
	}
}

func TestAReplayIsRefusedAndRemembersAcrossARestart(t *testing.T) {
	s, f := load(t)
	key, _ := PublicKey(f.PublicKey)
	now := time.Unix(f.Now, 0)
	path := filepath.Join(t.TempDir(), "nonces.json")
	good := f.Cases[0].Envelope
	if _, err := Verify(s, good, key, f.Node, NewGuard(path), now); err != nil {
		t.Fatal(err)
	}
	if _, err := Verify(s, good, key, f.Node, NewGuard(path), now); err == nil { // a NEW guard, as after a restart
		t.Fatal("a replay after a restart was accepted")
	}
}

func TestOldNoncesAreForgotten(t *testing.T) {
	g := NewGuard("")
	if !g.Remember("a-long-nonce-value-1234", 1000, 1000) || g.Remember("a-long-nonce-value-1234", 1000, 1000) {
		t.Fatal("first must pass, second must fail")
	}
	if !g.Remember("a-long-nonce-value-1234", 1000, 2000) {
		t.Fatal("after it could no longer be valid, the nonce is forgotten")
	}
}

func TestAWrongKeyIsRefused(t *testing.T) {
	s, f := load(t)
	other, _ := PublicKey("AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=")
	if _, err := Verify(s, f.Cases[0].Envelope, other, f.Node, nil, time.Unix(f.Now, 0)); err == nil {
		t.Fatal("verified with someone else's key")
	}
	if _, err := PublicKey("not base64"); err == nil {
		t.Fatal("accepted a bad key")
	}
}
