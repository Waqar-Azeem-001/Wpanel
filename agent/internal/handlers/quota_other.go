//go:build !linux

package handlers

// isQuota: other systems have no EDQUOT; a full disk is ENOSPC there.
func isQuota(error) bool { return false }
