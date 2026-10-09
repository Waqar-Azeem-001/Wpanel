// WH Agent (N1) -- SKELETON. NOT COMPILED, NOT RUN, NOT TESTED: the machine this was written on has no Go toolchain.
// It mirrors apps/agent/reference_agent.py (which is tested). Before use: build it, port the tests in
// apps/agent/tests/test_agent_protocol.py to Go, and have it reviewed. It performs no real work yet: every handler is a stub.
//
// Rules it must keep (docs/n1-wh-agent.md): connect out only; verify the Ed25519 signature over the received bytes BEFORE parsing; refuse
// wrong node, wrong protocol, expired, repeated nonce, unknown command, unknown or malformed parameter; never run a shell string.
package main

import (
	"bytes"
	"crypto/ed25519"
	"encoding/base64"
	"encoding/json"
	"errors"
	"fmt"
	"net/http"
	"os"
	"regexp"
	"time"
)

const (
	version     = "0.1.0"
	protocol    = 1
	clockSkew   = 60
	pollEvery   = 5 * time.Second
	maxBodySize = 1 << 20
)

type envelope struct {
	Payload   string `json:"payload"`
	Signature string `json:"signature"`
}

type payload struct {
	V         int                    `json:"v"`
	Job       int                    `json:"job"`
	Node      int                    `json:"node"`
	Command   string                 `json:"command"`
	Params    map[string]interface{} `json:"params"`
	IssuedAt  int64                  `json:"issued_at"`
	ExpiresAt int64                  `json:"expires_at"`
	Nonce     string                 `json:"nonce"`
}

type result struct {
	Ok      bool                   `json:"ok"`
	Code    string                 `json:"code"`
	Message string                 `json:"message"`
	Data    map[string]interface{} `json:"data"`
}

// The allow-list. Patterns must match apps/agent/catalog.py exactly.
var (
	domain = regexp.MustCompile(`^(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$`)
	acct   = regexp.MustCompile(`^[a-z][a-z0-9]{2,15}$`)
	dbName = regexp.MustCompile(`^[a-z][a-z0-9_]{2,47}$`)
	php    = regexp.MustCompile(`^(7\.4|8\.0|8\.1|8\.2|8\.3|8\.4)$`)
)

// checks maps command -> parameter -> validator. A parameter not listed here is refused (TODO: port every command from catalog.py).
var checks = map[string]map[string]func(interface{}) bool{
	"CREATE_WEBSITE":     {"account": text(acct), "domain": text(domain), "php_version": text(php)},
	"CREATE_DATABASE":    {"account": text(acct), "name": text(dbName)},
	"CHANGE_PHP_VERSION": {"account": text(acct), "domain": text(domain), "php_version": text(php)},
}

func text(re *regexp.Regexp) func(interface{}) bool {
	return func(v interface{}) bool { s, ok := v.(string); return ok && re.MatchString(s) }
}

var seen = map[string]int64{} // TODO: persist to disk so a restart does not reopen the replay window

func verify(env envelope, pub ed25519.PublicKey, node int) (*payload, error) {
	data, err := base64.StdEncoding.DecodeString(env.Payload)
	sig, err2 := base64.StdEncoding.DecodeString(env.Signature)
	if err != nil || err2 != nil || !ed25519.Verify(pub, data, sig) {
		return nil, errors.New("the signature is not valid")
	}
	var p payload
	if json.Unmarshal(data, &p) != nil || p.V != protocol {
		return nil, errors.New("unsupported protocol")
	}
	now := time.Now().Unix()
	if p.Node != node {
		return nil, errors.New("this command is for another node")
	}
	if p.IssuedAt > now+clockSkew || p.ExpiresAt < now {
		return nil, errors.New("the command is expired or dated in the future")
	}
	if len(p.Nonce) < 16 {
		return nil, errors.New("the nonce is missing")
	}
	rules, ok := checks[p.Command]
	if !ok {
		return nil, errors.New("that command is not on the allow-list")
	}
	for name, v := range p.Params {
		if check, known := rules[name]; !known || !check(v) {
			return nil, fmt.Errorf("parameter %q is not allowed", name)
		}
	}
	if len(p.Params) != len(rules) {
		return nil, errors.New("a parameter is missing")
	}
	for n, exp := range seen { // forget nonces that can no longer be valid
		if exp+clockSkew < now {
			delete(seen, n)
		}
	}
	if _, dup := seen[p.Nonce]; dup {
		return nil, errors.New("this command was already received (replay)")
	}
	seen[p.Nonce] = p.ExpiresAt
	return &p, nil
}

func run(p *payload) result {
	// TODO N3: real handlers (create the Linux user, PHP-FPM pool, web vhost ...) using exec.Command with an argument LIST, never a shell string.
	return result{Ok: false, Code: "not_implemented", Message: "this handler is a stub", Data: map[string]interface{}{}}
}

func post(client *http.Client, url, token string, body interface{}) (*http.Response, error) {
	raw, _ := json.Marshal(body)
	req, _ := http.NewRequest("POST", url, bytes.NewReader(raw))
	req.Header.Set("Authorization", "Agent "+token)
	req.Header.Set("X-Agent-Version", version)
	req.Header.Set("Content-Type", "application/json")
	return client.Do(req)
}

func main() {
	base, token := os.Getenv("WHPANEL_URL"), os.Getenv("WHAGENT_TOKEN")
	pubRaw, _ := base64.StdEncoding.DecodeString(os.Getenv("WHPANEL_PUBLIC_KEY"))
	var node int
	fmt.Sscanf(os.Getenv("WHAGENT_NODE_ID"), "%d", &node)
	if base == "" || token == "" || len(pubRaw) != ed25519.PublicKeySize {
		fmt.Fprintln(os.Stderr, "set WHPANEL_URL, WHAGENT_TOKEN, WHAGENT_NODE_ID and WHPANEL_PUBLIC_KEY")
		os.Exit(2)
	}
	client := &http.Client{Timeout: 30 * time.Second} // TODO N2: mutual TLS client certificate
	for {
		resp, err := post(client, base+"/api/agent/v1/poll/", token, map[string]string{})
		if err == nil && resp.StatusCode == 200 {
			var reply struct {
				Job *envelope `json:"job"`
			}
			json.NewDecoder(http.MaxBytesReader(nil, resp.Body, maxBodySize)).Decode(&reply)
			if reply.Job != nil {
				if p, err := verify(*reply.Job, ed25519.PublicKey(pubRaw), node); err == nil {
					post(client, fmt.Sprintf("%s/api/agent/v1/jobs/%d/result/", base, p.Job), token, run(p))
				} else {
					fmt.Fprintln(os.Stderr, "refused:", err) // a forged envelope is only logged, never reported
				}
			}
		}
		if resp != nil {
			resp.Body.Close()
		}
		time.Sleep(pollEvery)
	}
}
