//go:build linux

// Package runas starts the agent's file worker under an account's own Linux user, so a file command can reach nothing but that account's files even if a link is swapped between a check and
// its use. It has never been run on a real node.
package runas

import (
	"bytes"
	"encoding/json"
	"os"
	"os/exec"
	"os/user"
	"strconv"
	"syscall"
	"time"

	"whagent/internal/catalog"
	"whagent/internal/handlers"
)

// Runner returns a FileRunner for handlers.Agent that runs each file command in a child process as the account's uid and gid.
func Runner() func(account, command string, params map[string]any) (handlers.Result, *handlers.Error) {
	return func(account, command string, params map[string]any) (handlers.Result, *handlers.Error) {
		u, err := user.Lookup(account)
		if err != nil {
			return handlers.Result{}, &handlers.Error{Code: "not_found", Message: "No such account on this node."}
		}
		uid, _ := strconv.ParseUint(u.Uid, 10, 32)
		gid, _ := strconv.ParseUint(u.Gid, 10, 32)
		if uid == 0 {
			return handlers.Result{}, &handlers.Error{Code: "forbidden", Message: "File commands never run as root."}
		}
		self, err := os.Executable()
		if err != nil {
			return handlers.Result{}, &handlers.Error{Code: "system_error", Message: "The file worker could not be started."}
		}
		request, _ := json.Marshal(map[string]any{"command": command, "params": params})
		cmd := exec.Command(self, "file-worker")
		cmd.Stdin = bytes.NewReader(request)
		cmd.Dir = "/"
		cmd.Env = []string{"PATH=/usr/bin:/bin"}
		cmd.SysProcAttr = &syscall.SysProcAttr{Credential: &syscall.Credential{Uid: uint32(uid), Gid: uint32(gid), Groups: []uint32{}}}
		var out bytes.Buffer
		cmd.Stdout = &out
		done := make(chan error, 1)
		if err := cmd.Start(); err != nil {
			return handlers.Result{}, &handlers.Error{Code: "system_error", Message: "The file worker could not be started."}
		}
		go func() { done <- cmd.Wait() }()
		select {
		case <-done:
		case <-time.After(10 * time.Minute):
			_ = cmd.Process.Kill()
			return handlers.Result{}, &handlers.Error{Code: "timeout", Message: "The file operation took too long."}
		}
		decoded, err := catalog.Decode(out.Bytes())
		reply, ok := decoded.(map[string]any)
		if err != nil || !ok {
			return handlers.Result{}, &handlers.Error{Code: "system_error", Message: "The file worker failed."}
		}
		if e, bad := reply["error"].(map[string]any); bad {
			code, _ := e["code"].(string)
			message, _ := e["message"].(string)
			return handlers.Result{}, &handlers.Error{Code: code, Message: message}
		}
		data, _ := reply["data"].(map[string]any)
		code, _ := reply["code"].(string)
		return handlers.Result{Code: code, Data: data}, nil
	}
}
