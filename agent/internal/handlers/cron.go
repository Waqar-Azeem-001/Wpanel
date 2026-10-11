package handlers

import (
	"crypto/sha1"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"regexp"
	"sort"
	"strconv"
	"strings"

)

// Scheduled jobs on a node (the customer's Cron screen). The Go twin of apps/agent/node/cron.py.
//
// A job is never a free shell: only "php" and a script inside the account's own website folder, plain arguments, a five-field schedule not more often than every five minutes, ten jobs per
// account. /etc/cron.d/whp-<account> holds one line per enabled job and every line is the same fixed program with two checked words, "wh-agent cron-run <account> <job id>"; the agent then
// reads the job from its state file, refuses it if the website is suspended, and starts PHP through systemd-run in the account's own slice, as its user, for at most an hour.
// Nothing the customer typed is ever placed in the cron file or in a shell. Never run on a real node.

const (
	cronMaxJobs     = 10
	cronMinInterval = 5
	cronMaxCommand  = 300
	cronMaxOutput   = 500
	cronRunLimit    = 3600
)

var (
	cronScript   = regexp.MustCompile(`^[A-Za-z0-9_][A-Za-z0-9_./-]*\.php$`)
	cronArgument = regexp.MustCompile(`^[A-Za-z0-9_.=:/-]{1,100}$`)
	cronDigits   = regexp.MustCompile(`^\d+$`)
	cronRange    = regexp.MustCompile(`^\d+-\d+$`)
	cronFields   = []struct {
		label     string
		low, high int
	}{{"minute", 0, 59}, {"hour", 0, 23}, {"day of the month", 1, 31}, {"month", 1, 12}, {"day of the week", 0, 7}}
	cronPresets = [][2]string{{"Every 5 minutes", "*/5 * * * *"}, {"Every 15 minutes", "*/15 * * * *"}, {"Every hour", "0 * * * *"}, {"Every day at 02:00", "0 2 * * *"},
		{"Every Sunday at 03:00", "0 3 * * 0"}, {"First day of the month", "0 4 1 * *"}}
)

func init() {
	extraHandlers = append(extraHandlers, func(a *Agent) map[string]func(map[string]any) (Result, error) {
		return map[string]func(map[string]any) (Result, error){"CRON_LIST": a.cronList, "CRON_CREATE": a.cronCreate, "CRON_UPDATE": a.cronUpdate, "CRON_DELETE": a.cronDelete, "CRON_RUN": a.cronRunNow}
	})
}

type cronJob struct {
	ID        string  `json:"id"`
	Schedule  string  `json:"schedule"`
	Command   string  `json:"command"`
	Enabled   bool    `json:"enabled"`
	LastRunAt *string `json:"last_run_at,omitempty"`
	LastState string  `json:"last_status,omitempty"`
	LastOut   string  `json:"last_output,omitempty"`
}

func cronJobID(schedule, command string) string {
	sum := sha1.Sum([]byte(schedule + "\n" + command))
	return hex.EncodeToString(sum[:])[:10]
}

func cronValues(text string, low, high int, label string) (map[int]bool, error) {
	bad := herr("invalid", "The %s part of the schedule is not valid.", label)
	found := map[int]bool{}
	for _, part := range strings.Split(text, ",") {
		step := 1
		if strings.Contains(part, "/") {
			p, stepText, _ := strings.Cut(part, "/")
			part = p
			n, err := strconv.Atoi(stepText)
			if !cronDigits.MatchString(stepText) || err != nil || n < 1 {
				return nil, bad
			}
			step = n
		}
		var start, end int
		switch {
		case part == "*":
			start, end = low, high
		case cronRange.MatchString(part):
			a, b, _ := strings.Cut(part, "-")
			start, _ = strconv.Atoi(a)
			end, _ = strconv.Atoi(b)
		case cronDigits.MatchString(part):
			start, _ = strconv.Atoi(part)
			end = start
			if step > 1 {
				end = high
			}
		default:
			return nil, bad
		}
		if start < low || end > high || start > end {
			return nil, herr("invalid", "The %s part of the schedule must be between %d and %d.", label, low, high)
		}
		for v := start; v <= end; v += step {
			found[v] = true
		}
	}
	return found, nil
}

func cronCheckSchedule(schedule string) (string, error) {
	parts := strings.Fields(schedule)
	if len(parts) != 5 {
		return "", herr("invalid", "A schedule has five parts: minute, hour, day of the month, month and day of the week. Pick a common schedule or write them like 0 2 * * *.")
	}
	var minutes []int
	for i, p := range parts {
		set, err := cronValues(p, cronFields[i].low, cronFields[i].high, cronFields[i].label)
		if err != nil {
			return "", err
		}
		if i == 0 {
			for m := range set {
				minutes = append(minutes, m)
			}
			sort.Ints(minutes)
		}
	}
	if len(minutes) > 1 {
		gaps := []int{60 - minutes[len(minutes)-1] + minutes[0]}
		for i := 1; i < len(minutes); i++ {
			gaps = append(gaps, minutes[i]-minutes[i-1])
		}
		for _, g := range gaps {
			if g < cronMinInterval {
				return "", herr("invalid", "A job may not run more often than every %d minutes.", cronMinInterval)
			}
		}
	}
	return strings.Join(parts, " "), nil
}

func cronCheckCommand(command string) (string, error) {
	command = strings.Join(strings.Fields(command), " ")
	if command == "" || len(command) > cronMaxCommand {
		return "", herr("invalid", "Enter the PHP script to run, for example: php public_html/cron.php")
	}
	words := strings.Split(command, " ")
	if words[0] != "php" || len(words) < 2 {
		return "", herr("invalid", "Only PHP scripts can be scheduled here. Start the command with php, for example: php public_html/cron.php")
	}
	script := words[1]
	segments := strings.Split(script, "/")
	for _, s := range segments {
		if s == ".." {
			return "", herr("invalid", "The script must be a .php file inside your hosting folder, written without a leading slash or '..'.")
		}
	}
	if strings.HasPrefix(script, "/") || !cronScript.MatchString(script) {
		return "", herr("invalid", "The script must be a .php file inside your hosting folder, written without a leading slash or '..'.")
	}
	for _, arg := range words[2:] {
		if !cronArgument.MatchString(arg) {
			return "", herr("invalid", "Arguments may only contain letters, numbers and . _ = : / - (no shell symbols).")
		}
	}
	return command, nil
}

func cronStatePath(account string) string { return "/var/lib/whp/cron/" + account + ".json" }
func cronDPath(account string) string     { return "/etc/cron.d/whp-" + account }

func (a *Agent) cronJobs(account string) []*cronJob {
	raw, ok := a.Sys.Read(cronStatePath(account))
	if !ok {
		return nil
	}
	var doc struct {
		Jobs []*cronJob `json:"jobs"`
	}
	if json.Unmarshal([]byte(raw), &doc) != nil {
		return nil
	}
	return doc.Jobs
}

func (a *Agent) cronWrite(account string, jobs []*cronJob) error {
	raw, _ := json.Marshal(map[string]any{"jobs": jobs})
	return a.Sys.Write(cronStatePath(account), string(raw), 0o600, "root", "")
}

func (a *Agent) cronSave(account string, jobs []*cronJob) error {
	if err := a.cronWrite(account, jobs); err != nil {
		return err
	}
	var lines []string
	for _, j := range jobs {
		if j.Enabled {
			lines = append(lines, fmt.Sprintf("%s root /usr/local/bin/wh-agent cron-run %s %s >/dev/null 2>&1", j.Schedule, account, j.ID))
		}
	}
	if len(lines) == 0 {
		return a.Sys.Remove(cronDPath(account))
	}
	head := "# managed by WH Agent - do not edit by hand\nSHELL=/bin/sh\nPATH=/usr/sbin:/usr/bin:/sbin:/bin\nMAILTO=\"\"\n"
	return a.Sys.Write(cronDPath(account), head+strings.Join(lines, "\n")+"\n", 0o644, "root", "")
}

func (a *Agent) cronNeed(account string) (State, error) {
	st := a.stateRead(pathsOfState(account))
	if st == nil {
		return nil, herr("not_found", "No such account on this node.")
	}
	return st, nil
}

func cronInfo(j *cronJob) map[string]any {
	var last any
	if j.LastRunAt != nil {
		last = *j.LastRunAt
	}
	return map[string]any{"job_id": j.ID, "schedule": j.Schedule, "command": j.Command, "enabled": j.Enabled, "last_run_at": last, "last_status": j.LastState, "last_output": j.LastOut}
}

func cronFind(jobs []*cronJob, id string) (*cronJob, error) {
	for _, j := range jobs {
		if j.ID == id {
			return j, nil
		}
	}
	return nil, herr("not_found", "That scheduled job no longer exists.")
}

func (a *Agent) cronList(p map[string]any) (Result, error) {
	account := str(p, "account")
	if _, err := a.cronNeed(account); err != nil {
		return Result{}, err
	}
	rows := []map[string]any{}
	for _, j := range a.cronJobs(account) {
		rows = append(rows, cronInfo(j))
	}
	presets := [][]string{}
	for _, x := range cronPresets {
		presets = append(presets, []string{x[0], x[1]})
	}
	return Result{"listed", map[string]any{"max_jobs": cronMaxJobs, "min_interval_minutes": cronMinInterval, "presets": presets, "jobs": rows}}, nil
}

func (a *Agent) cronCreate(p map[string]any) (Result, error) {
	account := str(p, "account")
	if _, err := a.cronNeed(account); err != nil {
		return Result{}, err
	}
	schedule, err := cronCheckSchedule(str(p, "schedule"))
	if err != nil {
		return Result{}, err
	}
	command, err := cronCheckCommand(str(p, "command"))
	if err != nil {
		return Result{}, err
	}
	jobs := a.cronJobs(account)
	wanted := cronJobID(schedule, command)
	for _, j := range jobs {
		if j.ID == wanted {
			return Result{"exists", map[string]any{"job_id": wanted}}, nil
		}
	}
	if len(jobs) >= cronMaxJobs {
		return Result{}, herr("limit_reached", "Your plan allows %d scheduled jobs. Delete one to add another.", cronMaxJobs)
	}
	if err := a.cronSave(account, append(jobs, &cronJob{ID: wanted, Schedule: schedule, Command: command, Enabled: true})); err != nil {
		return Result{}, err
	}
	return Result{"created", map[string]any{"job_id": wanted}}, nil
}

func (a *Agent) cronUpdate(p map[string]any) (Result, error) {
	account := str(p, "account")
	if _, err := a.cronNeed(account); err != nil {
		return Result{}, err
	}
	jobs := a.cronJobs(account)
	job, err := cronFind(jobs, str(p, "job_id"))
	if err != nil {
		return Result{}, err
	}
	schedule, command := job.Schedule, job.Command
	if v, ok := p["schedule"].(string); ok {
		schedule = v
	}
	if v, ok := p["command"].(string); ok {
		command = v
	}
	if schedule, err = cronCheckSchedule(schedule); err != nil {
		return Result{}, err
	}
	if command, err = cronCheckCommand(command); err != nil {
		return Result{}, err
	}
	enabled := job.Enabled
	if v, ok := p["enabled"].(string); ok {
		enabled = v == "yes"
	}
	newID := cronJobID(schedule, command)
	if newID != job.ID {
		for _, j := range jobs {
			if j.ID == newID {
				return Result{}, herr("exists", "That job already exists.")
			}
		}
	}
	job.ID, job.Schedule, job.Command, job.Enabled = newID, schedule, command, enabled
	if err := a.cronSave(account, jobs); err != nil {
		return Result{}, err
	}
	return Result{"updated", map[string]any{"job_id": newID}}, nil
}

func (a *Agent) cronDelete(p map[string]any) (Result, error) {
	account := str(p, "account")
	if _, err := a.cronNeed(account); err != nil {
		return Result{}, err
	}
	jobs := a.cronJobs(account)
	if _, err := cronFind(jobs, str(p, "job_id")); err != nil {
		return Result{}, err
	}
	kept := []*cronJob{}
	for _, j := range jobs {
		if j.ID != str(p, "job_id") {
			kept = append(kept, j)
		}
	}
	if err := a.cronSave(account, kept); err != nil {
		return Result{}, err
	}
	return Result{"deleted", map[string]any{}}, nil
}

func (a *Agent) cronExecute(account string, st State, job *cronJob) (string, string) {
	words := strings.Split(job.Command, " ")
	script, args := words[1], words[2:]
	domain := st.text("domain")
	php := fmt.Sprintf("/opt/remi/php%s/root/usr/bin/php", strings.ReplaceAll(st.text("php_version"), ".", ""))
	argv := []string{"systemd-run", "--quiet", "--collect", "--wait", "--pipe", "--uid", account, "--gid", account, "--slice", "whp-" + account + ".slice",
		"--property=NoNewPrivileges=yes", "--property=PrivateTmp=yes", "--property=ProtectSystem=strict", "--property=ProtectHome=tmpfs",
		"--property=BindPaths=/home/" + account, fmt.Sprintf("--property=RuntimeMaxSec=%d", cronRunLimit), "--working-directory=/home/" + account + "/sites/" + domain, php, script}
	argv = append(argv, args...)
	out, err := a.Sys.Run(argv, "")
	status := "ok"
	if err != nil {
		out, status = err.Error(), "failed"
	}
	if r := []rune(out); len(r) > cronMaxOutput {
		out = string(r[len(r)-cronMaxOutput:])
	}
	return status, out
}

func (a *Agent) cronRecord(account string, st State, jobs []*cronJob, job *cronJob) (*cronJob, error) {
	if st.flag("suspended") {
		return nil, herr("suspended", "This website is suspended, so its scheduled jobs do not run.")
	}
	status, out := a.cronExecute(account, st, job)
	when := a.Now().UTC().Format("2006-01-02T15:04:05") + "+00:00"
	job.LastRunAt, job.LastState, job.LastOut = &when, status, out
	if err := a.cronWrite(account, jobs); err != nil {
		return nil, err
	}
	return job, nil
}

func (a *Agent) cronRunNow(p map[string]any) (Result, error) {
	account := str(p, "account")
	st, err := a.cronNeed(account)
	if err != nil {
		return Result{}, err
	}
	jobs := a.cronJobs(account)
	job, err := cronFind(jobs, str(p, "job_id"))
	if err != nil {
		return Result{}, err
	}
	done, err := a.cronRecord(account, st, jobs, job)
	if err != nil {
		return Result{}, err
	}
	return Result{"ran", map[string]any{"job": cronInfo(done)}}, nil
}

// CronRun is what "wh-agent cron-run" does when cron calls it: run one job if the account, the job and the website all allow it. It is quiet when they do not.
func (a *Agent) CronRun(account, wanted string) (bool, error) {
	st := a.stateRead(pathsOfState(account))
	if st == nil || st.flag("suspended") {
		return false, nil
	}
	jobs := a.cronJobs(account)
	for _, j := range jobs {
		if j.ID == wanted && j.Enabled {
			if _, err := a.cronRecord(account, st, jobs, j); err != nil {
				return false, err
			}
			return true, nil
		}
	}
	return false, nil
}

