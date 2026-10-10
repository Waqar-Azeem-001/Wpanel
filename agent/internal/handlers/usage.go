package handlers

import (
	"bufio"
	"encoding/json"
	"io/fs"
	"math"
	"os"
	"path/filepath"
	"regexp"
	"sort"
	"strconv"
	"strings"

	"whagent/internal/render"
	"whagent/internal/sandbox"
)

// Usage (N9): disk, files, database size, bandwidth and what the account's cgroup is using right now. Disk and file counts come from walking the account's folder (links are counted but
// never followed): exact, but slow on huge accounts, where reading the XFS quota report would be cheaper (not built). Bandwidth comes from the web server's access log (bytes sent per day).
// Live figures come from the cgroup files systemd keeps for the account's slice. RecordDay is the daily job that keeps one row per day for the usage charts.

const keepDays = 400

var (
	logRe   = regexp.MustCompile(`\[(\d{2})/([A-Za-z]{3})/(\d{4}):[^\]]*\] "[^"]*" \d{3} (\d+|-)`)
	monthOf = map[string]int{"Jan": 1, "Feb": 2, "Mar": 3, "Apr": 4, "May": 5, "Jun": 6, "Jul": 7, "Aug": 8, "Sep": 9, "Oct": 10, "Nov": 11, "Dec": 12}
)

func init() {
	extraHandlers = append(extraHandlers, func(a *Agent) map[string]func(map[string]any) (Result, error) {
		return map[string]func(map[string]any) (Result, error){"USAGE_REPORT": a.usageReport, "ISOLATION_CHECK": a.isolationCheck}
	})
}

// MeasureTree is the bytes in regular files and the number of files, folders and links under a folder.
func MeasureTree(root string) (int64, int64) {
	var total, inodes int64
	_ = filepath.WalkDir(root, func(p string, d fs.DirEntry, err error) error {
		if err != nil {
			return nil
		}
		if p != root {
			inodes++
		}
		if d.Type().IsRegular() {
			if info, e := d.Info(); e == nil {
				total += info.Size()
			}
		}
		return nil
	})
	return total, inodes
}

// ParseAccessLog turns lines in the combined log format into bytes sent per day. Lines that do not fit are ignored.
func ParseAccessLog(lines []string) map[string]int64 {
	days := map[string]int64{}
	for _, line := range lines {
		m := logRe.FindStringSubmatch(line)
		if m == nil || monthOf[m[2]] == 0 {
			continue
		}
		day := m[3] + "-" + pad2(monthOf[m[2]]) + "-" + m[1]
		if m[4] != "-" {
			n, _ := strconv.ParseInt(m[4], 10, 64)
			days[day] += n
		} else {
			days[day] += 0
		}
	}
	return days
}

func pad2(n int) string {
	if n < 10 {
		return "0" + strconv.Itoa(n)
	}
	return strconv.Itoa(n)
}

func round2(v float64) float64 { return math.Round(v*100) / 100 }

type usageMeasure struct {
	DiskMB    float64
	Inodes    int64
	DBMB      float64
	Bandwidth map[string]int64
}

func (a *Agent) measure(account string) (usageMeasure, error) {
	root := ""
	if a.RootFor != nil {
		root = a.RootFor(account)
	}
	if root == "" || a.stateRead(pathsOfState(account)) == nil {
		return usageMeasure{}, herr("not_found", "No such account on this node.")
	}
	size, inodes := MeasureTree(root)
	names, _ := a.Engine.Databases(account + "_")
	var db float64
	for _, n := range names {
		s, _ := a.Engine.SizeMB(n)
		db += s
	}
	m := usageMeasure{DiskMB: round2(float64(size) / 1048576), Inodes: inodes, DBMB: round2(db), Bandwidth: map[string]int64{}}
	if a.LogFor != nil {
		if path := a.LogFor(account); path != "" {
			if f, err := os.Open(path); err == nil {
				var lines []string
				sc := bufio.NewScanner(f)
				sc.Buffer(make([]byte, 1<<20), 1<<20)
				for sc.Scan() {
					lines = append(lines, sc.Text())
				}
				f.Close()
				m.Bandwidth = ParseAccessLog(lines)
			}
		}
	}
	return m, nil
}

func usagePath(account string) string   { return "/var/lib/whp/usage/" + account + ".json" }
func cpuMarkPath(account string) string { return "/var/lib/whp/usage/" + account + ".cpu.json" }

func (a *Agent) history(account string) map[string]map[string]float64 {
	out := map[string]map[string]float64{}
	if raw, ok := a.Sys.Read(usagePath(account)); ok {
		_ = json.Unmarshal([]byte(raw), &out)
	}
	return out
}

func (a *Agent) today() string { return a.now().UTC().Format("2006-01-02") }

// RecordDay is the daily job: write today's figures for each account into its history. It returns the accounts measured.
func (a *Agent) RecordDay(accounts []string) []string {
	var done []string
	for _, account := range accounts {
		m, err := a.measure(account)
		if err != nil {
			continue
		}
		days := a.history(account)
		for day, sent := range m.Bandwidth {
			if days[day] == nil {
				days[day] = map[string]float64{}
			}
			days[day]["bandwidth_mb"] = round2(float64(sent) / 1048576)
		}
		t := a.today()
		if days[t] == nil {
			days[t] = map[string]float64{}
		}
		days[t]["disk_mb"], days[t]["inodes"], days[t]["db_mb"] = m.DiskMB, float64(m.Inodes), m.DBMB
		if _, ok := days[t]["bandwidth_mb"]; !ok {
			days[t]["bandwidth_mb"] = 0
		}
		keys := make([]string, 0, len(days))
		for d := range days {
			keys = append(keys, d)
		}
		sort.Strings(keys)
		if len(keys) > keepDays {
			for _, d := range keys[:len(keys)-keepDays] {
				delete(days, d)
			}
		}
		raw, _ := json.Marshal(days)
		_ = a.Sys.Write(usagePath(account), string(raw), 0o600, "root", "")
		done = append(done, account)
	}
	return done
}

func readTrim(path string) (string, bool) {
	raw, err := os.ReadFile(path)
	if err != nil {
		return "", false
	}
	return strings.TrimSpace(string(raw)), true
}

func readInt(path string) (int64, bool) {
	text, ok := readTrim(path)
	if !ok {
		return 0, false
	}
	n, err := strconv.ParseInt(text, 10, 64)
	return n, err == nil
}

func (a *Agent) live(account string) map[string]any {
	base := filepath.Join(a.CgroupRoot, "whp-"+account+".slice")
	if info, err := os.Stat(base); err != nil || !info.IsDir() {
		return nil
	}
	out := map[string]any{"cpu_percent": nil, "memory_mb": nil, "memory_limit_mb": nil, "processes": nil, "processes_limit": nil, "io_kb_s": nil}
	if mem, ok := readInt(filepath.Join(base, "memory.current")); ok {
		out["memory_mb"] = math.Round(float64(mem)/1048576*10) / 10
	}
	if text, _ := readTrim(filepath.Join(base, "memory.max")); text != "" && text != "max" {
		if n, err := strconv.ParseInt(text, 10, 64); err == nil {
			out["memory_limit_mb"] = int64(math.RoundToEven(float64(n) / 1048576))
		}
	}
	if n, ok := readInt(filepath.Join(base, "pids.current")); ok {
		out["processes"] = n
	}
	if text, _ := readTrim(filepath.Join(base, "pids.max")); text != "" && text != "max" {
		if n, err := strconv.ParseInt(text, 10, 64); err == nil {
			out["processes_limit"] = n
		}
	}
	var usec int64 = -1
	if text, _ := readTrim(filepath.Join(base, "cpu.stat")); text != "" {
		for _, line := range strings.Split(text, "\n") {
			if f := strings.Fields(line); len(f) == 2 && f[0] == "usage_usec" {
				usec, _ = strconv.ParseInt(f[1], 10, 64)
			}
		}
	}
	var io int64
	if text, _ := readTrim(filepath.Join(base, "io.stat")); text != "" {
		for _, line := range strings.Split(text, "\n") {
			for _, kv := range strings.Fields(line)[1:] {
				if k, v, ok := strings.Cut(kv, "="); ok && (k == "rbytes" || k == "wbytes") {
					n, _ := strconv.ParseInt(v, 10, 64)
					io += n
				}
			}
		}
	}
	now := float64(a.now().UnixNano()) / 1e9
	if raw, ok := a.Sys.Read(cpuMarkPath(account)); ok && usec >= 0 {
		var mark struct {
			Usec int64   `json:"usec"`
			T    float64 `json:"t"`
			IO   int64   `json:"io"`
		}
		if json.Unmarshal([]byte(raw), &mark) == nil && now > mark.T {
			out["cpu_percent"] = math.Round(math.Max(0, float64(usec-mark.Usec))/((now-mark.T)*1e6)*100*10) / 10
			out["io_kb_s"] = math.Round(math.Max(0, float64(io-mark.IO))/1024/(now-mark.T)*10) / 10
		}
	}
	if usec >= 0 {
		raw, _ := json.Marshal(map[string]any{"usec": usec, "t": now, "io": io})
		_ = a.Sys.Write(cpuMarkPath(account), string(raw), 0o600, "root", "")
	}
	return out
}

func (a *Agent) usageReport(p map[string]any) (Result, error) {
	account := str(p, "account")
	st, err := a.need(account, "")
	if err != nil {
		return Result{}, err
	}
	m, err := a.measure(account)
	if err != nil {
		return Result{}, err
	}
	hist := a.history(account)
	for day, sent := range m.Bandwidth {
		if hist[day] == nil {
			hist[day] = map[string]float64{}
		}
		hist[day]["bandwidth_mb"] = round2(float64(sent) / 1048576)
	}
	today := a.today()
	if hist[today] == nil {
		hist[today] = map[string]float64{}
	}
	hist[today]["disk_mb"], hist[today]["inodes"], hist[today]["db_mb"] = m.DiskMB, float64(m.Inodes), m.DBMB
	month := today[:7]
	var usedMonth float64
	days := make([]string, 0, len(hist))
	for d, v := range hist {
		days = append(days, d)
		if strings.HasPrefix(d, month) {
			usedMonth += v["bandwidth_mb"]
		}
	}
	sort.Strings(days)
	n := int(num(p, "days"))
	if len(days) > n {
		days = days[len(days)-n:]
	}
	rows := []any{}
	for _, d := range days {
		rows = append(rows, map[string]any{"day": d, "disk_mb": int64(math.RoundToEven(hist[d]["disk_mb"] + hist[d]["db_mb"])), "bandwidth_mb": int64(math.RoundToEven(hist[d]["bandwidth_mb"]))})
	}
	limits := map[string]int64{}
	for k, v := range render.Defaults {
		limits[k] = v
	}
	for k, v := range st.Limits() {
		limits[k] = v
	}
	var liveFigures any
	if l := a.live(account); l != nil {
		liveFigures = l
	}
	return Result{"reported", map[string]any{"limits": limitsAsAny(limits), "now": map[string]any{"disk_mb": m.DiskMB, "db_mb": m.DBMB, "inodes": m.Inodes, "bandwidth_month_mb": round2(usedMonth),
		"bandwidth_today_mb": round2(hist[today]["bandwidth_mb"])}, "history": rows, "live": liveFigures}}, nil
}

var _ = sandbox.TrashDir
