package handlers

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"whagent/internal/render"
)

const netdevTemplate = "Inter-|   Receive |  Transmit\n face |bytes packets errs drop fifo frame compressed multicast|bytes packets errs drop fifo colls carrier compressed\n" +
	"    lo: 1000 10 0 0 0 0 0 0 1000 10 0 0 0 0 0 0\n  eth0: 5000 100 E1 E2 0 0 0 0 7000 90 E3 E4 0 0 0 0\n"

func netdev(e1, e2, e3, e4 string) string {
	return strings.NewReplacer("E1", e1, "E2", e2, "E3", e3, "E4", e4).Replace(netdevTemplate)
}

func TestMetricsFromMadeUpProcFiles(t *testing.T) {
	r := newRig(t)
	f := r.fake
	f.Files["/proc/stat"] = "cpu  1000 0 500 8000 500 0 0 0 0 0\n"
	f.Files["/proc/loadavg"] = "0.52 0.40 0.30 1/200 999\n"
	f.Files["/proc/meminfo"] = "MemTotal:       16384000 kB\nMemFree:         1000000 kB\nMemAvailable:    4096000 kB\n"
	f.Files["/proc/net/dev"] = netdev("0", "0", "0", "0")
	for _, u := range []string{"httpd", "sshd", "mariadb", "postfix", "dovecot", "rspamd"} {
		f.ServiceState[u] = "active"
	}
	f.Probes["postqueue -j"] = "{\"a\":1}\n{\"b\":2}\n{\"c\":3}\n"
	m := &Metrics{Agent: r.agent, Capabilities: []string{"web", "database", "mail"}, DiskPath: "/home", DiskUsage: func(string) (uint64, uint64, error) { return 200 << 30, 50 << 30, nil }}
	first := m.Collect()
	if _, ok := first["cpu_percent"]; ok {
		t.Fatal("CPU needs two readings")
	}
	if first["load_1m"] != 0.52 || first["memory_used_mb"] != int64(12000) || first["memory_total_mb"] != int64(16000) || first["disk_total_gb"] != int64(200) || first["disk_used_gb"] != int64(50) || first["mail_queue"] != 3 {
		t.Fatalf("%v", first)
	}
	services := first["services"].(map[string]any)
	if len(services) != 6 || services["httpd"] != "up" {
		t.Fatalf("%v", services)
	}
	f.Files["/proc/stat"] = "cpu  1600 0 700 8200 500 0 0 0 0 0\n"
	f.Files["/proc/net/dev"] = netdev("2", "1", "0", "3")
	second := m.Collect()
	if second["cpu_percent"] != 80.0 || second["net_errors"] != int64(6) {
		t.Fatalf("%v", second)
	}
}

func TestAServiceThatIsStoppedOrStartingIsReportedAsSuchAndNothingIsInvented(t *testing.T) {
	r := newRig(t)
	r.fake.ServiceState["httpd"], r.fake.ServiceState["sshd"] = "inactive", "activating"
	got := (&Metrics{Agent: r.agent, Capabilities: []string{"web"}}).Collect()
	services := got["services"].(map[string]any)
	if len(got) != 1 || services["httpd"] != "down" || services["sshd"] != "degraded" {
		t.Fatalf("%v", got)
	}
}

func TestUsageReportsFilesDatabasesBandwidthAndLiveCgroupFigures(t *testing.T) {
	r := newRig(t)
	docroot := filepath.Join(r.root, "acme.com", "public_html")
	_ = os.MkdirAll(docroot, 0o755)
	_ = os.WriteFile(filepath.Join(docroot, "a.bin"), make([]byte, 2097152), 0o644)
	_ = os.WriteFile(filepath.Join(docroot, "b.txt"), []byte("hello"), 0o644)
	logFile := filepath.Join(t.TempDir(), "access.log")
	_ = os.WriteFile(logFile, []byte(`1.2.3.4 - - [09/Oct/2026:10:00:00 +0000] "GET / HTTP/1.1" 200 1048576 "-" "x"`+"\n"+
		`1.2.3.4 - - [10/Oct/2026:10:00:00 +0000] "GET /a HTTP/1.1" 200 2097152 "-" "x"`+"\n"+`1.2.3.4 - - [10/Oct/2026:11:00:00 +0000] "GET /b HTTP/1.1" 304 - "-" "x"`+"\nnot a log line\n"), 0o644)
	r.agent.LogFor = func(string) string { return logFile }
	r.agent.CgroupRoot = t.TempDir()
	clock := time.Date(2026, 10, 10, 12, 0, 0, 0, time.UTC)
	r.agent.Now = func() time.Time { return clock }
	res := r.must("USAGE_REPORT", map[string]any{"account": "abcd1234", "days": 7})
	now := res.Data["now"].(map[string]any)
	if now["disk_mb"] != 2.0 || now["bandwidth_month_mb"] != 3.0 || now["bandwidth_today_mb"] != 2.0 || now["inodes"].(int64) < 4 {
		t.Fatalf("%v", now)
	}
	if hist := res.Data["history"].([]any); len(hist) != 2 || hist[0].(map[string]any)["day"] != "2026-10-09" || hist[0].(map[string]any)["bandwidth_mb"] != int64(1) {
		t.Fatalf("%v", hist)
	}
	if res.Data["limits"].(map[string]any)["disk_mb"] != int64(5120) || res.Data["live"] != nil {
		t.Fatalf("%v", res.Data)
	}
	if done := r.agent.RecordDay([]string{"abcd1234", "nobody123"}); len(done) != 1 {
		t.Fatalf("%v", done)
	}
	if text, _ := r.fake.Read("/var/lib/whp/usage/abcd1234.json"); !strings.Contains(text, `"2026-10-10"`) || !strings.Contains(text, `"2026-10-09"`) {
		t.Fatal(text)
	}
	group := filepath.Join(r.agent.CgroupRoot, "whp-abcd1234.slice")
	_ = os.MkdirAll(group, 0o755)
	write := func(name, value string) { _ = os.WriteFile(filepath.Join(group, name), []byte(value), 0o644) }
	write("memory.current", "104857600")
	write("memory.max", "268435456")
	write("pids.current", "12")
	write("pids.max", "50")
	write("cpu.stat", "usage_usec 1000000\nuser_usec 900000\n")
	write("io.stat", "8:0 rbytes=1048576 wbytes=1048576 rios=1 wios=1\n")
	first := r.must("USAGE_REPORT", map[string]any{"account": "abcd1234", "days": 1}).Data["live"].(map[string]any)
	if first["memory_mb"] != 100.0 || first["memory_limit_mb"] != int64(256) || first["processes"] != int64(12) || first["processes_limit"] != int64(50) || first["cpu_percent"] != nil {
		t.Fatalf("%v", first)
	}
	clock = clock.Add(10 * time.Second)
	write("cpu.stat", "usage_usec 6000000\n")
	write("io.stat", "8:0 rbytes=3145728 wbytes=1048576\n")
	second := r.must("USAGE_REPORT", map[string]any{"account": "abcd1234", "days": 1}).Data["live"].(map[string]any)
	if second["cpu_percent"] != 50.0 || second["io_kb_s"] != 204.8 {
		t.Fatalf("%v", second)
	}
	r.fails("USAGE_REPORT", map[string]any{"account": "ghost999", "days": 1}, "not_found")
}

func TestTheIsolationCheckFindsMissingProtectionsAndDrift(t *testing.T) {
	r := newRig(t)
	good := map[string]string{"/sys/fs/cgroup/cgroup.controllers": "cpuset cpu io memory hugetlb pids rdma misc", "/proc/mounts": "proc /proc proc rw,nosuid,hidepid=2 0 0\n/dev/sdb1 /home xfs rw,prjquota 0 0\n",
		"/proc/sys/fs/protected_symlinks": "1", "/proc/sys/fs/protected_hardlinks": "1", "/proc/sys/kernel/yama/ptrace_scope": "1"}
	for k, v := range good {
		r.fake.Files[k] = v
	}
	res := r.must("ISOLATION_CHECK", map[string]any{})
	if res.Data["ok"] != true || len(res.Data["accounts"].(map[string]any)) != 0 {
		t.Fatalf("%v", res.Data)
	}
	for path, bad := range map[string]string{"/proc/mounts": "proc /proc proc rw 0 0\n/dev/sdb1 /home ext4 rw 0 0\n", "/sys/fs/cgroup/cgroup.controllers": "cpu memory", "/proc/sys/fs/protected_symlinks": "0", "/proc/sys/kernel/yama/ptrace_scope": "0"} {
		old := r.fake.Files[path]
		r.fake.Files[path] = bad
		if r.must("ISOLATION_CHECK", map[string]any{}).Data["ok"] == true {
			t.Fatalf("%s: a missing protection must be reported", path)
		}
		r.fake.Files[path] = old
	}
	delete(r.fake.Files, "/proc/sys/fs/protected_hardlinks")
	found := false
	for _, c := range r.must("ISOLATION_CHECK", map[string]any{}).Data["baseline"].([]any) {
		if c.(map[string]any)["ok"] == false && strings.Contains(c.(map[string]any)["detail"].(string), "None") {
			found = true
		}
	}
	if !found {
		t.Fatal("a file that cannot be read is a failure, not a pass")
	}
	r.fake.Files["/proc/sys/fs/protected_hardlinks"] = "1"
	p := render.PathsOf("abcd1234", "acme.com")
	r.fake.Files[p.Unit] = strings.Replace(strings.Replace(r.fake.Files[p.Unit], "ProtectHome=tmpfs\n", "", 1), "User=abcd1234\n", "", 1)
	r.fake.Files[p.Slice] = strings.Replace(r.fake.Files[p.Slice], "MemoryMax=1024M", "MemoryMax=99999M", 1)
	delete(r.fake.Files, p.Vhost)
	issues := r.must("ISOLATION_CHECK", map[string]any{}).Data["accounts"].(map[string]any)["abcd1234"].([]any)
	text := ""
	for _, i := range issues {
		text += i.(string) + "|"
	}
	for _, want := range []string{"ProtectHome=tmpfs", "own user", "resource limits", "web server settings are missing"} {
		if !strings.Contains(text, want) {
			t.Fatalf("%q not in %s", want, text)
		}
	}
}

func TestJobsRunWhenDueNotAgainAndAFailureDoesNotStopTheOthers(t *testing.T) {
	r := newRig(t)
	clock := time.Date(2026, 10, 10, 12, 0, 0, 0, time.UTC)
	r.agent.Now = func() time.Time { return clock }
	calls := 0
	jobs := []Job{{"daily", 24 * time.Hour, func() (string, error) { calls++; return "did it", nil }}, {"bad", 24 * time.Hour, func() (string, error) { return "", os.ErrPermission }}}
	done := r.agent.RunDue(jobs)
	if len(done) != 2 || done[0][1] != "ok: did it" || !strings.HasPrefix(done[1][1], "failed:") {
		t.Fatalf("%v", done)
	}
	if again := r.agent.RunDue(jobs); len(again) != 0 {
		t.Fatalf("not yet due: %v", again)
	}
	clock = clock.Add(61 * time.Minute)
	if again := r.agent.RunDue(jobs); len(again) != 1 || again[0][0] != "bad" {
		t.Fatalf("a failed job is retried in an hour: %v", again)
	}
	clock = clock.Add(24 * time.Hour)
	if again := r.agent.RunDue(jobs); len(again) != 2 || calls != 2 {
		t.Fatalf("%v %d", again, calls)
	}
	r2 := *r.agent // a new process remembers: the state is in the file
	if again := r2.RunDue(jobs); len(again) != 0 {
		t.Fatal("a restart must not repeat a job")
	}
}

func TestTheStandardJobsRunThroughTheHandlers(t *testing.T) {
	r := newRig(t)
	_ = os.MkdirAll(filepath.Join(r.root, "acme.com", "public_html"), 0o755)
	r.agent.MailRoot, r.agent.StageRoot = t.TempDir(), t.TempDir()
	jobs := r.agent.StandardJobs(r.agent.Accounts, func() []string { return nil })
	names := []string{}
	for _, j := range jobs {
		names = append(names, j.Name)
	}
	if strings.Join(names, ",") != "usage,certificates,mail-abuse,node-config-backup" {
		t.Fatal(names)
	}
	got := map[string]string{}
	for _, d := range r.agent.RunDue(jobs) {
		got[d[0]] = d[1]
	}
	if got["usage"] != "ok: 1 accounts" || !strings.HasPrefix(got["mail-abuse"], "ok:") || !strings.HasPrefix(got["node-config-backup"], "failed:") {
		t.Fatalf("%v", got) // the node-configuration backup fails here: no backup storage is set up on this pretend node
	}
	if _, ok := r.fake.Read("/var/lib/whp/usage/abcd1234.json"); !ok {
		t.Fatal("the usage history must have been written")
	}
}
