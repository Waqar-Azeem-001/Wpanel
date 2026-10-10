package service

import (
	"crypto/ed25519"
	"crypto/rand"
	"encoding/base64"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync"
	"testing"
	"time"

	"whagent/internal/catalog"
	"whagent/internal/envelope"
	"whagent/internal/handlers"
	"whagent/internal/panel"
	"whagent/internal/system"
)

// A pretend WHPanel: hands out signed commands, records what the agent reports.
type fakePanel struct {
	mu        sync.Mutex
	pub       ed25519.PublicKey
	priv      ed25519.PrivateKey
	queue     []envelope.Envelope
	results   map[int64]panel.Report
	beats     []map[string]any
	headers   http.Header
	renewed   int
	forceCode int
}

func newPanel(t *testing.T) (*fakePanel, *httptest.Server) {
	pub, priv, _ := ed25519.GenerateKey(rand.Reader)
	p := &fakePanel{pub: pub, priv: priv, results: map[int64]panel.Report{}}
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		p.mu.Lock()
		defer p.mu.Unlock()
		p.headers = r.Header.Clone()
		if p.forceCode != 0 {
			w.WriteHeader(p.forceCode)
			_, _ = w.Write([]byte(`{"error":"too old"}`))
			return
		}
		if r.Header.Get("Authorization") != "Agent secret-token" {
			w.WriteHeader(401)
			return
		}
		switch {
		case strings.HasPrefix(r.URL.Path, "/api/agent/v1/poll/"):
			if len(p.queue) == 0 {
				_, _ = w.Write([]byte(`{"job":null}`))
				return
			}
			next := p.queue[0]
			p.queue = p.queue[1:]
			_ = json.NewEncoder(w).Encode(map[string]any{"job": next})
		case strings.HasPrefix(r.URL.Path, "/api/agent/v1/jobs/"):
			var id int64
			_, _ = fmtSscan(r.URL.Path, &id)
			var rep panel.Report
			_ = json.NewDecoder(r.Body).Decode(&rep)
			p.results[id] = rep
			_, _ = w.Write([]byte(`{"status":"ok"}`))
		case r.URL.Path == "/api/agent/v1/heartbeat/":
			var figures map[string]any
			_ = json.NewDecoder(r.Body).Decode(&figures)
			p.beats = append(p.beats, figures)
			_, _ = w.Write([]byte(`{"ok":true}`))
		default:
			w.WriteHeader(404)
		}
	}))
	t.Cleanup(srv.Close)
	return p, srv
}

func (p *fakePanel) sign(payload map[string]any) envelope.Envelope {
	raw, _ := json.Marshal(payload)
	return envelope.Envelope{Payload: base64.StdEncoding.EncodeToString(raw), Signature: base64.StdEncoding.EncodeToString(ed25519.Sign(p.priv, raw))}
}

func (p *fakePanel) command(job int64, command string, params map[string]any, over map[string]any) {
	now := time.Now().Unix()
	payload := map[string]any{"v": 1, "job": job, "correlation_id": "c", "node": 7, "command": command, "params": params, "issued_at": now, "expires_at": now + 300,
		"nonce": strings.Repeat("n", 12) + string(rune('a'+job%26)) + "abcdefghij", "idempotency_key": "k"}
	for k, v := range over {
		payload[k] = v
	}
	p.queue = append(p.queue, p.sign(payload))
}

func build(t *testing.T) (*Service, *fakePanel, *system.Fake) {
	t.Helper()
	spec, _ := catalog.Load()
	handlers.Sleep = func(time.Duration) {}
	p, srv := newPanel(t)
	fake := system.NewFake()
	agent := handlers.New(spec, fake)
	agent.Engine = handlers.NewFakeEngine()
	cfg := &panel.Config{Panel: srv.URL, NodeID: 7, Token: "secret-token"}
	client, _ := panel.New(cfg)
	svc := &Service{Spec: spec, Agent: agent, Client: client, Guard: envelope.NewGuard(""), PublicKey: p.pub, NodeID: 7, WaitSeconds: 0, RenewBefore: time.Hour}
	return svc, p, fake
}

func TestACommandIsFetchedVerifiedCarriedOutAndReported(t *testing.T) {
	svc, p, fake := build(t)
	p.command(1, "CREATE_WEBSITE", map[string]any{"account": "abcd1234", "domain": "acme.com", "php_version": "8.3"}, nil)
	got, err := svc.Once()
	if err != nil || !got {
		t.Fatalf("%v %v", got, err)
	}
	rep := p.results[1]
	if !rep.OK || rep.Code != "created" || rep.Data["root"] != "/home/abcd1234/sites/acme.com/public_html" {
		t.Fatalf("%+v", rep)
	}
	if _, ok := fake.Read("/var/lib/whp/accounts/abcd1234.json"); !ok {
		t.Fatal("the website must really have been made on the machine")
	}
	if p.headers.Get("X-Agent-Version") != panel.Version || p.headers.Get("Authorization") != "Agent secret-token" {
		t.Fatalf("%v", p.headers)
	}
	if got, _ := svc.Once(); got {
		t.Fatal("nothing left to do")
	}
}

func TestAFailureComesBackStructured(t *testing.T) {
	svc, p, _ := build(t)
	p.command(2, "CHANGE_PHP_VERSION", map[string]any{"account": "abcd1234", "domain": "acme.com", "php_version": "8.1"}, nil)
	_, _ = svc.Once()
	if rep := p.results[2]; rep.OK || rep.Code != "not_found" || rep.Message == "" {
		t.Fatalf("%+v", rep)
	}
}

func TestACommandThatIsForgedExpiredForAnotherNodeOrOffTheListIsNeverRun(t *testing.T) {
	svc, p, fake := build(t)
	forged := p.sign(map[string]any{"v": 1, "job": 3, "node": 7, "command": "CREATE_DATABASE", "params": map[string]any{"account": "abcd1234", "name": "abcd1234_x"}, "issued_at": time.Now().Unix(), "expires_at": time.Now().Unix() + 300,
		"nonce": "n123456789012345678", "idempotency_key": "k"})
	forged.Signature = base64.StdEncoding.EncodeToString(make([]byte, 64))
	p.queue = append(p.queue, forged)
	_, _ = svc.Once()
	if _, reported := p.results[3]; reported {
		t.Fatal("a forged envelope must be dropped, never reported")
	}
	p.command(4, "CREATE_WEBSITE", map[string]any{"account": "abcd1234", "domain": "acme.com", "php_version": "8.3"}, map[string]any{"expires_at": time.Now().Unix() - 10})
	p.command(5, "CREATE_WEBSITE", map[string]any{"account": "abcd1234", "domain": "acme.com", "php_version": "8.3"}, map[string]any{"node": 99})
	p.command(6, "RUN_SHELL", map[string]any{"cmd": "id"}, nil)
	for i := 0; i < 3; i++ {
		_, _ = svc.Once()
	}
	for _, id := range []int64{4, 5, 6} {
		if rep := p.results[id]; rep.OK || rep.Code != "refused" {
			t.Fatalf("job %d: %+v", id, rep)
		}
	}
	if len(fake.Commands) != 0 {
		t.Fatalf("nothing may have run: %v", fake.Commands)
	}
}

func TestAReplayedCommandIsRefused(t *testing.T) {
	svc, p, _ := build(t)
	p.command(7, "SERVICE_RESTART", map[string]any{"service": "httpd"}, nil)
	p.queue = append(p.queue, p.queue[0]) // the very same envelope again
	_, _ = svc.Once()
	_, _ = svc.Once()
	if len(p.results) != 1 || p.results[7].Code == "refused" && false {
		t.Fatalf("%v", p.results)
	}
	if rep := p.results[7]; rep.Code != "refused" || !strings.Contains(rep.Message, "replay") {
		t.Fatalf("the second delivery must be refused as a replay (it overwrote the first report): %+v", rep)
	}
}

func TestHeartbeatsCarryTheNodesFigures(t *testing.T) {
	svc, p, fake := build(t)
	fake.ServiceState["httpd"] = "active"
	svc.Metrics = &handlers.Metrics{Agent: svc.Agent, Capabilities: []string{"web"}}
	if err := svc.Beat(); err != nil {
		t.Fatal(err)
	}
	services := p.beats[0]["services"].(map[string]any)
	if services["httpd"] != "up" || services["sshd"] != "down" {
		t.Fatalf("%v", p.beats)
	}
}

func TestMaintenanceRunsTheJobsAndAnOldAgentIsToldSo(t *testing.T) {
	svc, p, _ := build(t)
	calls := 0
	svc.Jobs = []handlers.Job{{Name: "x", Every: time.Hour, Run: func() (string, error) { calls++; return "ok", nil }}}
	svc.Maintain()
	svc.Maintain()
	if calls != 1 {
		t.Fatalf("calls %d", calls)
	}
	p.forceCode = 426
	if _, err := svc.Once(); err == nil || !strings.Contains(err.Error(), "426") {
		t.Fatalf("%v", err)
	}
}

func fmtSscan(path string, id *int64) (int, error) {
	parts := strings.Split(strings.Trim(path, "/"), "/")
	var n int64
	for _, c := range parts[len(parts)-2] {
		n = n*10 + int64(c-'0')
	}
	*id = n
	return 1, nil
}
