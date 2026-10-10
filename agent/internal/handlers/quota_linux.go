//go:build linux

package handlers

import (
	"errors"
	"syscall"
)

// isQuota reports a disk quota error (EDQUOT), which only exists on Linux.
func isQuota(err error) bool { return errors.Is(err, syscall.EDQUOT) }
