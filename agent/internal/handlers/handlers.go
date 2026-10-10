// Package handlers is everything the agent does when WHPanel asks. Each command in the catalogue has exactly one handler here; a command with none is refused
// as "not implemented". Handlers re-validate their parameters (the catalogue is checked once when the envelope arrives and again here), work only through the
// system.System interface, and are safe to repeat.
//
// The website, SFTP, database and service handlers are tested against thousands of lines of what WHPanel's Python reference agent writes for the same commands.
// None of it has run on a real node.
package handlers

import (
	"encoding/json"
	"fmt"
	"strings"
	"time"

	"whagent/internal/catalog"
	"whagent/internal/system"
)

// Result is a successful answer.
type Result struct {
	Code string         `json:"code"`
	Data map[string]any `json:"data"`
}

// Error is a handler failure: a short code and a message that is safe to show to a customer.
type Error struct{ Code, Message string }

func (e *Error) Error() string { return e.Code + ": " + e.Message }

func herr(code, format string, a ...any) *Error { return &Error{code, fmt.Sprintf(format, a...)} }

// Agent holds what handlers need. Fields not needed by a deployment (a web node has no mail root) may be left empty: those commands are then refused.
type Agent struct {
	Spec   *catalog.Spec
	Sys    system.System
	Engine Engine
	// RootFor is the account's file area on disk (/home/<account>/sites); nil means "no such account".
	RootFor func(account string) string
	// MailRoot is where Maildir folders live; StageRoot is scratch space for backups and restores.
	MailRoot, StageRoot string
	// Restic opens the backup repository from the saved settings (nil: backups are not available).
	Restic func(env map[string]string) (Repository, error)
	// Acme gets certificates (nil: SSL is not available).
	Acme Acme
	// CgroupRoot is /sys/fs/cgroup.
	CgroupRoot string
	// LogFor is the web server access log of an account.
	LogFor func(account string) string
	// FileRunner, when set, carries out a file command as the account's own Linux user (production). Nil runs it directly (tests).
	FileRunner func(account, command string, params map[string]any) (Result, *Error)
	Now        func() time.Time
}

// New returns an Agent with sensible defaults.
func New(spec *catalog.Spec, sys system.System) *Agent {
	return &Agent{Spec: spec, Sys: sys, CgroupRoot: "/sys/fs/cgroup", Now: time.Now}
}

// Run validates and carries out one command. “raw“ is the parameters exactly as received.
func (a *Agent) Run(command string, raw any) (res Result, out *Error) {
	params, err := a.Spec.Validate(command, raw)
	if err != nil {
		return Result{}, herr("invalid", "%s", err.Error())
	}
	var e error
	switch command {
	case "CREATE_WEBSITE":
		res, e = a.createWebsite(params)
	case "DELETE_WEBSITE":
		res, e = a.deleteWebsite(params)
	case "SUSPEND_WEBSITE":
		res, e = a.suspendWebsite(params)
	case "UNSUSPEND_WEBSITE":
		res, e = a.unsuspendWebsite(params)
	case "CHANGE_PHP_VERSION":
		res, e = a.changePHP(params)
	case "SET_LIMITS":
		res, e = a.setLimits(params)
	case "SERVICE_RESTART":
		res, e = a.restartService(params)
	default:
		if h, ok := a.more(command); ok {
			res, e = h(params)
		} else {
			return Result{}, herr("not_implemented", "%s is not implemented on this node yet.", command)
		}
	}
	return finish(res, e)
}

func finish(res Result, e error) (Result, *Error) {
	if e == nil {
		if res.Data == nil {
			res.Data = map[string]any{}
		}
		return res, nil
	}
	switch v := e.(type) {
	case *Error:
		return Result{}, v
	case *system.Error:
		return Result{}, herr("system_error", "%s", clip(v.Msg, 300))
	default:
		return Result{}, herr("system_error", "%s", clip(e.Error(), 300))
	}
}

func clip(s string, n int) string {
	if r := []rune(s); len(r) > n {
		return string(r[:n])
	}
	return s
}

// more is the table of handlers added by the other files of this package.
func (a *Agent) more(command string) (func(map[string]any) (Result, error), bool) {
	if h, ok := a.registry()[command]; ok {
		return h, true
	}
	return nil, false
}

func (a *Agent) registry() map[string]func(map[string]any) (Result, error) {
	return extra(a)
}

// extra is filled in by the files that add commands (see init functions registering into extraHandlers).
var extraHandlers []func(*Agent) map[string]func(map[string]any) (Result, error)

func extra(a *Agent) map[string]func(map[string]any) (Result, error) {
	all := map[string]func(map[string]any) (Result, error){}
	for _, build := range extraHandlers {
		for k, v := range build(a) {
			all[k] = v
		}
	}
	return all
}

// ---- small helpers used by every handler ----------------------------------------------------------------------------------------------------------

func str(p map[string]any, k string) string { s, _ := p[k].(string); return s }
func num(p map[string]any, k string) int64  { n, _ := p[k].(int64); return n }

// State is an account's record on the node, kept as JSON under /var/lib/whp/accounts.
type State map[string]any

func (a *Agent) stateRead(path string) State {
	raw, ok := a.Sys.Read(path)
	if !ok || strings.TrimSpace(raw) == "" {
		return nil
	}
	v, err := catalog.Decode([]byte(raw))
	if err != nil {
		return nil
	}
	m, _ := v.(map[string]any)
	return State(m)
}

func (a *Agent) stateWrite(path string, st State) error {
	raw, _ := json.Marshal(map[string]any(st))
	return a.Sys.Write(path, string(raw), 0o600, "root", "")
}

func (st State) text(key string) string { s, _ := st[key].(string); return s }
func (st State) flag(key string) bool   { b, _ := st[key].(bool); return b }
func (st State) integer(key string) int64 {
	switch v := st[key].(type) {
	case json.Number:
		n, _ := v.Int64()
		return n
	case int64:
		return v
	case int:
		return int64(v)
	}
	return 0
}

// Limits returns the account's stored limits as numbers.
func (st State) Limits() map[string]int64 {
	out := map[string]int64{}
	if m, ok := st["limits"].(map[string]any); ok {
		for k, v := range m {
			switch n := v.(type) {
			case json.Number:
				i, _ := n.Int64()
				out[k] = i
			case int64:
				out[k] = n
			}
		}
	}
	return out
}

func (st State) copy() State {
	c := State{}
	for k, v := range st {
		c[k] = v
	}
	return c
}

// StateOf is the domain an account's website is for, or "" if it has none (the web server's log file is named after it).
func (a *Agent) StateOf(account string) string {
	if st := a.stateRead(pathsOfState(account)); st != nil {
		return st.text("domain")
	}
	return ""
}
