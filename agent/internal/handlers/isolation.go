package handlers

import (
	"fmt"
	"path/filepath"
	"strings"

	"whagent/internal/render"
)

// The isolation baseline (N9, ADR 03/11): what the node itself must look like for tenants to be kept apart, and a check that every account's installed limits and service hardening are
// still what the plan says (drift, for example a unit edited by hand, is reported). The baseline checks read /proc and /sys/fs/cgroup and have been run only against made-up files, never
// on a real machine. They are necessary, not sufficient: the real proof is the cross-tenant probe and the noisy-neighbour load test, which must be run on a real node before it takes customers.

func check(name string, ok bool, detail, why string) map[string]any {
	return map[string]any{"name": name, "ok": ok, "detail": detail, "why": why}
}

func (a *Agent) sysctl(path string) (string, bool) {
	text, ok := a.Sys.Read(path)
	return strings.TrimSpace(text), ok
}

func mountLine(lines []string, point string) string {
	for _, m := range lines {
		if f := strings.Fields(m); len(f) >= 2 && f[1] == point {
			return m
		}
	}
	return ""
}

func (a *Agent) baseline() []any {
	text, _ := a.Sys.Read("/sys/fs/cgroup/cgroup.controllers")
	controllers := strings.Fields(text)
	have := map[string]bool{}
	for _, c := range controllers {
		have[c] = true
	}
	needed := have["cpu"] && have["memory"] && have["io"] && have["pids"]
	detail := strings.Join(controllers, " ")
	if detail == "" {
		detail = "could not be read"
	}
	out := []any{check("cgroup v2 with cpu, memory, io and pids", needed, detail, "Without these the plan's CPU, memory, process and I/O limits cannot be enforced.")}
	mounts, _ := a.Sys.Read("/proc/mounts")
	lines := strings.Split(mounts, "\n")
	proc := mountLine(lines, "/proc")
	detail = proc
	if detail == "" {
		detail = "could not be read"
	}
	out = append(out, check("/proc hides other users' processes (hidepid=2)", strings.Contains(proc, "hidepid=2") || strings.Contains(proc, "hidepid=invisible"), detail, "Otherwise a tenant can list every other tenant's processes and their command lines."))
	home := mountLine(lines, "/home")
	detail = home
	if detail == "" {
		detail = "no separate /home mount found"
	}
	out = append(out, check("/home is XFS with project quotas", strings.Contains(home, " xfs ") && (strings.Contains(home, "prjquota") || strings.Contains(home, "pquota")), detail, "Disk and file limits per account are project quotas."))
	for _, c := range []struct{ name, path, wanted, why string }{
		{"symlink protection", "/proc/sys/fs/protected_symlinks", "1", "Stops tricks with links in shared folders."},
		{"hardlink protection", "/proc/sys/fs/protected_hardlinks", "1", "Stops hard links to files a tenant does not own."},
		{"ptrace restricted", "/proc/sys/kernel/yama/ptrace_scope", "", "A tenant must not attach to another process."}} {
		value, found := a.sysctl(c.path)
		ok := value == c.wanted
		if c.wanted == "" {
			ok = value == "1" || value == "2" || value == "3"
		}
		shown := value
		if !found {
			shown = "None"
		}
		out = append(out, check(c.name, ok, fmt.Sprintf("%s = %s", c.path, shown), c.why))
	}
	return out
}

func (a *Agent) driftedAccounts() map[string]any {
	issues := map[string]any{}
	for _, path := range a.Sys.List("/var/lib/whp/accounts/") {
		if !strings.HasSuffix(path, ".json") || strings.Contains(path, ".cpu") {
			continue
		}
		account := strings.TrimSuffix(filepath.Base(path), ".json")
		st := a.stateRead(path)
		if st == nil {
			continue
		}
		limits := map[string]int64{}
		for k, v := range render.Defaults {
			limits[k] = v
		}
		for k, v := range st.Limits() {
			limits[k] = v
		}
		paths := render.PathsOf(account, st.text("domain"))
		var found []any
		if unit, ok := a.Sys.Read(paths.Unit); !ok {
			found = append(found, "its PHP service file is missing")
		} else {
			unitLines := strings.Split(unit, "\n")
			for _, line := range render.UnitHardening {
				if !contains(unitLines, line) {
					found = append(found, "the service lacks "+line)
				}
			}
			if !contains(unitLines, "User="+account) {
				found = append(found, "the PHP service does not run as the account's own user")
			}
		}
		want, _ := render.SliceUnit(account, limits)
		if slice, ok := a.Sys.Read(paths.Slice); !ok {
			found = append(found, "its resource limits file is missing")
		} else if slice != want {
			found = append(found, "the resource limits on the node differ from the account's plan")
		}
		wantPool, _ := render.FpmConf(account, limits)
		if pool, ok := a.Sys.Read(paths.FpmConf); !ok {
			found = append(found, "its PHP settings file is missing")
		} else if pool != wantPool {
			found = append(found, "its PHP settings differ from what WHPanel would write")
		}
		if _, ok := a.Sys.Read(paths.Vhost); !ok {
			found = append(found, "its web server settings are missing")
		}
		if len(found) > 0 {
			issues[account] = found
		}
	}
	return issues
}

func (a *Agent) isolationCheck(map[string]any) (Result, error) {
	base, drift := a.baseline(), a.driftedAccounts()
	ok := len(drift) == 0
	for _, c := range base {
		if !c.(map[string]any)["ok"].(bool) {
			ok = false
		}
	}
	return Result{"checked", map[string]any{"baseline": base, "accounts": drift, "ok": ok}}, nil
}
