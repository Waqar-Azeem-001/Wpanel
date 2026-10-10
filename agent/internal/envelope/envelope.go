// Package envelope checks a signed command from WHPanel before anything is done with it. The agent holds only WHPanel's PUBLIC key: a node that is
// taken over can verify commands but can never forge one. The signature is checked over the exact bytes received, before they are parsed.
package envelope

import (
	"crypto/ed25519"
	"encoding/base64"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"sync"
	"time"

	"whagent/internal/catalog"
)

const (
	// ClockSkew is how far the node's clock may differ from WHPanel's.
	ClockSkew = 60
	// ProtocolVersion must match WHPanel's.
	ProtocolVersion = 1
)

// Envelope is what WHPanel sends.
type Envelope struct {
	Payload   string `json:"payload"`
	Signature string `json:"signature"`
}

// Payload is a verified command.
type Payload struct {
	V              int            `json:"v"`
	Job            int64          `json:"job"`
	CorrelationID  string         `json:"correlation_id"`
	Node           int64          `json:"node"`
	Command        string         `json:"command"`
	IdempotencyKey string         `json:"idempotency_key"`
	IssuedAt       int64          `json:"issued_at"`
	ExpiresAt      int64          `json:"expires_at"`
	Nonce          string         `json:"nonce"`
	Params         map[string]any `json:"-"` // validated and cleaned
	RawParams      any            `json:"-"` // as received (numbers kept as numbers): handlers validate again on their own side
}

// Guard remembers nonces so a command that was already received is refused. Its table is kept on disk: a restart must not reopen the window.
type Guard struct {
	mu   sync.Mutex
	path string
	seen map[string]int64
}

// NewGuard loads the table from path (an empty path keeps it in memory only, for tests).
func NewGuard(path string) *Guard {
	g := &Guard{path: path, seen: map[string]int64{}}
	if path != "" {
		if raw, err := os.ReadFile(path); err == nil {
			_ = json.Unmarshal(raw, &g.seen)
		}
	}
	return g
}

// Remember records a nonce until it could no longer be valid. It returns false if the nonce was seen before.
func (g *Guard) Remember(nonce string, expires int64, now int64) bool {
	g.mu.Lock()
	defer g.mu.Unlock()
	for n, e := range g.seen {
		if e+ClockSkew < now {
			delete(g.seen, n)
		}
	}
	if _, dup := g.seen[nonce]; dup {
		return false
	}
	g.seen[nonce] = expires
	g.save()
	return true
}

func (g *Guard) save() {
	if g.path == "" {
		return
	}
	raw, _ := json.Marshal(g.seen)
	_ = os.MkdirAll(filepath.Dir(g.path), 0o700)
	tmp := g.path + ".tmp"
	if os.WriteFile(tmp, raw, 0o600) == nil {
		_ = os.Rename(tmp, g.path)
	}
}

// Verify runs every check the Python reference agent runs, in the same order: the signature first, then the protocol, the node, the dates, the nonce, and
// finally the allow-list and the parameters. When the signature is good but a later check refuses, the payload is still returned (with the error) so the refusal can be reported for that job;
// when the signature is not good nothing is returned and nothing may be reported.
func Verify(spec *catalog.Spec, env Envelope, publicKey ed25519.PublicKey, node int64, guard *Guard, now time.Time) (*Payload, error) {
	data, err1 := base64.StdEncoding.Strict().DecodeString(env.Payload)
	sig, err2 := base64.StdEncoding.Strict().DecodeString(env.Signature)
	if err1 != nil || err2 != nil || env.Payload == "" || len(sig) != ed25519.SignatureSize || !ed25519.Verify(publicKey, data, sig) {
		return nil, errors.New("the signature is not valid")
	}
	decoded, err := catalog.Decode(data)
	obj, ok := decoded.(map[string]any)
	if err != nil || !ok {
		return nil, errors.New("the payload is not JSON")
	}
	var p Payload
	if err := json.Unmarshal(data, &p); err != nil {
		return nil, errors.New("the payload is not valid")
	}
	if p.V != ProtocolVersion {
		return &p, errors.New("unsupported protocol version")
	}
	if p.Node != node {
		return &p, errors.New("this command is for another node")
	}
	secs := now.Unix()
	if p.IssuedAt > secs+ClockSkew {
		return &p, errors.New("the command is dated in the future")
	}
	if p.ExpiresAt < secs {
		return &p, errors.New("the command has expired")
	}
	if len(p.Nonce) < 16 {
		return &p, errors.New("the nonce is missing")
	}
	params, err := spec.Validate(p.Command, obj["params"])
	if err != nil {
		return &p, err
	}
	if guard != nil && !guard.Remember(p.Nonce, p.ExpiresAt, secs) {
		return &p, errors.New("this command was already received (replay)")
	}
	p.Params, p.RawParams = params, obj["params"]
	return &p, nil
}

// PublicKey decodes the base64 key WHPanel hands out at enrolment.
func PublicKey(b64 string) (ed25519.PublicKey, error) {
	raw, err := base64.StdEncoding.DecodeString(b64)
	if err != nil || len(raw) != ed25519.PublicKeySize {
		return nil, fmt.Errorf("the signing key is not valid")
	}
	return ed25519.PublicKey(raw), nil
}
