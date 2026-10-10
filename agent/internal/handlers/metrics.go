package handlers

import (
	"encoding/json"
	"math"
	"strconv"
	"strings"
	"time"
)

// What a node reports about itself (N10): CPU, load, memory, disk, network errors, the state of its services, the mail queue and the soonest certificate expiry. WHPanel decides what it
// means, so the thresholds live in one place. It reads /proc and asks systemctl; it has been run only against made-up /proc files. A figure that cannot be read is simply left out of the
// report (WHPanel treats "not reported" as unknown, never as fine or as broken); a service that cannot be asked about is reported as down.

var servicesByCapability = map[string][]string{"web": {"httpd", "sshd"}, "database": {"mariadb"}, "mail": {"postfix", "dovecot", "rspamd"}}

// Metrics collects the heartbeat figures. It remembers its last readings, because CPU and network errors need two.
type Metrics struct {
	Agent        *Agent
	Capabilities []string
	DiskPath     string
	DiskUsage    func(path string) (total, used uint64, err error)
	cpuBusy      uint64
	cpuTotal     uint64
	haveCPU      bool
	netErrors    uint64
	haveNet      bool
}

func (m *Metrics) read(path string) string { text, _ := m.Agent.Sys.Read(path); return text }

func (m *Metrics) cpu() (float64, bool) {
	lines := strings.Split(m.read("/proc/stat"), "\n")
	if len(lines) == 0 || !strings.HasPrefix(lines[0], "cpu ") {
		return 0, false
	}
	var total, idle uint64
	for i, f := range strings.Fields(lines[0])[1:] {
		n, err := strconv.ParseUint(f, 10, 64)
		if err != nil {
			continue
		}
		total += n
		if i == 3 || i == 4 {
			idle += n
		}
	}
	busy := total - idle
	had, prevBusy, prevTotal := m.haveCPU, m.cpuBusy, m.cpuTotal
	m.cpuBusy, m.cpuTotal, m.haveCPU = busy, total, true
	if !had || total <= prevTotal {
		return 0, false
	}
	return math.Round(float64(busy-prevBusy)*100/float64(total-prevTotal)*10) / 10, true
}

func (m *Metrics) memory() (int64, int64, bool) {
	values := map[string]float64{}
	for _, line := range strings.Split(m.read("/proc/meminfo"), "\n") {
		key, rest, ok := strings.Cut(line, ":")
		if f := strings.Fields(rest); ok && len(f) > 0 {
			values[key], _ = strconv.ParseFloat(f[0], 64)
		}
	}
	total, haveTotal := values["MemTotal"]
	available, haveAvailable := values["MemAvailable"]
	if !haveTotal || !haveAvailable || total == 0 {
		return 0, 0, false
	}
	return int64(math.RoundToEven((total - available) / 1024)), int64(math.RoundToEven(total / 1024)), true
}

func (m *Metrics) network() (int64, bool) {
	var errors uint64
	lines := strings.Split(m.read("/proc/net/dev"), "\n")
	for _, line := range lines[min(2, len(lines)):] {
		name, rest, ok := strings.Cut(line, ":")
		cols := strings.Fields(rest)
		if !ok || strings.TrimSpace(name) == "lo" || len(cols) < 12 {
			continue
		}
		for _, i := range []int{2, 3, 10, 11} {
			n, _ := strconv.ParseUint(cols[i], 10, 64)
			errors += n
		}
	}
	had, prev := m.haveNet, m.netErrors
	m.netErrors, m.haveNet = errors, true
	if !had {
		return 0, false
	}
	if errors < prev {
		return 0, true
	}
	return int64(errors - prev), true
}

// ServiceState is "up", "degraded" or "down" for one systemd unit.
func (a *Agent) ServiceState(unit string) string {
	status, out := a.Sys.Probe([]string{"systemctl", "is-active", unit})
	if status == 0 {
		return "up"
	}
	switch out {
	case "activating", "reloading", "deactivating":
		return "degraded"
	}
	return "down"
}

func (a *Agent) certMinDays() (int64, bool) {
	var best int64
	found := false
	for _, path := range a.Sys.List("/var/lib/whp/accounts/") {
		if !strings.HasSuffix(path, ".json") || strings.Contains(path, ".cpu") {
			continue
		}
		account := strings.TrimSuffix(path[strings.LastIndex(path, "/")+1:], ".json")
		st := a.stateRead(path)
		if st == nil || st["ssl"] == nil {
			continue
		}
		if days, ok := a.sslRow(account, st)["days_left"].(int64); ok && (!found || days < best) {
			best, found = days, true
		}
	}
	return best, found
}

// Collect is the heartbeat's figures.
func (m *Metrics) Collect() map[string]any {
	out := map[string]any{}
	if v, ok := m.cpu(); ok {
		out["cpu_percent"] = v
	}
	if f := strings.Fields(m.read("/proc/loadavg")); len(f) > 0 {
		if load, err := strconv.ParseFloat(f[0], 64); err == nil {
			out["load_1m"] = load
		}
	}
	if used, total, ok := m.memory(); ok {
		out["memory_used_mb"], out["memory_total_mb"] = used, total
	}
	if m.DiskUsage != nil {
		if total, used, err := m.DiskUsage(m.DiskPath); err == nil {
			out["disk_total_gb"], out["disk_used_gb"] = int64(math.RoundToEven(float64(total)/1073741824)), int64(math.RoundToEven(float64(used)/1073741824))
		}
	}
	if v, ok := m.network(); ok {
		out["net_errors"] = v
	}
	services := map[string]any{}
	for _, capability := range m.Capabilities {
		for _, unit := range servicesByCapability[capability] {
			services[unit] = m.Agent.ServiceState(unit)
		}
	}
	out["services"] = services
	if contains(m.Capabilities, "mail") {
		if status, listing := m.Agent.Sys.Probe([]string{"postqueue", "-j"}); status == 0 {
			n := 0
			for _, line := range strings.Split(listing, "\n") {
				if strings.TrimSpace(line) != "" {
					n++
				}
			}
			out["mail_queue"] = n
		}
	}
	if days, ok := m.Agent.certMinDays(); ok {
		out["certs_min_days"] = days
	}
	return out
}

// ---- recurring jobs ----------------------------------------------------------------------------------------------------------------------------------

// Job is something the agent does on a schedule.
type Job struct {
	Name  string
	Every time.Duration
	Run   func() (string, error)
}

const schedulerState = "/var/lib/whp/scheduler.json"

// RunDue runs every job that is due and remembers when each last ran (so a restart repeats nothing and skips nothing). A job that has never run is due now; one that fails is retried in
// an hour, not on every loop; one failing never stops the others. It returns one line per job that ran.
func (a *Agent) RunDue(jobs []Job) [][2]string {
	state := map[string]float64{}
	if raw, ok := a.Sys.Read(schedulerState); ok {
		_ = json.Unmarshal([]byte(raw), &state)
	}
	now := float64(a.now().UnixNano()) / 1e9
	var done [][2]string
	for _, j := range jobs {
		if last, ok := state[j.Name]; ok && now-last < j.Every.Seconds() {
			continue
		}
		text, err := j.Run()
		if err != nil {
			done = append(done, [2]string{j.Name, "failed: " + clip(err.Error(), 200)})
			state[j.Name] = now - j.Every.Seconds() + 3600
			continue
		}
		done = append(done, [2]string{j.Name, "ok: " + text})
		state[j.Name] = now
	}
	if len(done) > 0 {
		raw, _ := json.Marshal(state)
		_ = a.Sys.Write(schedulerState, string(raw), 0o600, "root", "")
	}
	return done
}

// StandardJobs are the jobs a node runs. accounts lists the account names on the node; mailLog gives the mail log lines of the last hour (mail nodes; nil elsewhere).
func (a *Agent) StandardJobs(accounts func() []string, mailLog func() []string) []Job {
	jobs := []Job{
		{"usage", 24 * time.Hour, func() (string, error) { return strconv.Itoa(len(a.RecordDay(accounts()))) + " accounts", nil }},
		{"certificates", 24 * time.Hour, func() (string, error) { return strconv.Itoa(len(a.RenewDue(accounts()))) + " checked or renewed", nil }},
	}
	if mailLog != nil {
		jobs = append(jobs, Job{"mail-abuse", time.Hour, func() (string, error) { return "blocked " + strings.Join(a.ScanLogs(mailLog()), ","), nil }})
	}
	jobs = append(jobs, Job{"node-config-backup", 7 * 24 * time.Hour, func() (string, error) {
		res, err := a.Run("BACKUP_NODE_CONFIG", map[string]any{})
		if err != nil {
			return "", err
		}
		return res.Code, nil
	}})
	return jobs
}

// Accounts lists the accounts that have a website on this node.
func (a *Agent) Accounts() []string {
	var out []string
	for _, path := range a.Sys.List("/var/lib/whp/accounts/") {
		if strings.HasSuffix(path, ".json") && !strings.Contains(path, ".cpu") {
			out = append(out, strings.TrimSuffix(path[strings.LastIndex(path, "/")+1:], ".json"))
		}
	}
	return out
}
