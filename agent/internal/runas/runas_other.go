//go:build !linux

// Package runas: running a worker under another user needs Linux. Elsewhere the agent runs file commands in-process, which is only acceptable for tests.
package runas

import "whagent/internal/handlers"

// Runner is not available on this system.
func Runner() func(account, command string, params map[string]any) (handlers.Result, *handlers.Error) {
	return nil
}
