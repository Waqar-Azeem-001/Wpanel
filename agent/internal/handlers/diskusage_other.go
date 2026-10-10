//go:build !linux

package handlers

import "errors"

// DiskUsage is only available on Linux; elsewhere the figure is left out of the report.
func DiskUsage(string) (uint64, uint64, error) {
	return 0, 0, errors.New("not available on this system")
}
