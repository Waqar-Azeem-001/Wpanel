//go:build linux

package handlers

import "syscall"

// DiskUsage is the total and used bytes of the filesystem holding path.
func DiskUsage(path string) (total, used uint64, err error) {
	var st syscall.Statfs_t
	if err = syscall.Statfs(path, &st); err != nil {
		return 0, 0, err
	}
	total = st.Blocks * uint64(st.Bsize)
	return total, total - st.Bfree*uint64(st.Bsize), nil
}
