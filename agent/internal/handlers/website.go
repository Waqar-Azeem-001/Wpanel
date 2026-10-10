package handlers

import (
	"fmt"
	"sort"

	"whagent/internal/render"
	"whagent/internal/system"
)

const indexPage = "<!doctype html><title>%[1]s</title><h1>%[1]s</h1><p>This website is ready. Upload your files to replace this page.</p>\n"

func (a *Agent) need(account, domain string) (State, error) {
	st := a.stateRead(render.PathsOf(account, "").State)
	if st == nil || (domain != "" && st.text("domain") != domain) {
		return nil, herr("not_found", "No such website on this node.")
	}
	return st, nil
}

func (a *Agent) reloadWeb() error {
	if _, err := a.Sys.Run([]string{"httpd", "-t"}, ""); err != nil { // a bad configuration is caught before it can take every site down
		return err
	}
	_, err := a.Sys.Run([]string{"systemctl", "reload", "httpd"}, "")
	return err
}

// quota sets disk space and number of files for the whole account in one project quota. The file limit stops a tenant filling the disk with tiny files.
func (a *Agent) quota(account string, diskMB, inodes int64) error {
	uid, _ := a.Sys.UID(account)
	if _, err := a.Sys.Run([]string{"xfs_quota", "-x", "-c", fmt.Sprintf("project -s -p /home/%s %d", account, uid), "/home"}, ""); err != nil {
		return err
	}
	_, err := a.Sys.Run([]string{"xfs_quota", "-x", "-c", fmt.Sprintf("limit -p bsoft=%dm bhard=%dm isoft=%d ihard=%d %d", int64(float64(diskMB)*0.9), diskMB, int64(float64(inodes)*0.9), inodes, uid), "/home"}, "")
	return err
}

func limitsAsAny(l map[string]int64) map[string]any {
	out := map[string]any{}
	for k, v := range l {
		out[k] = v
	}
	return out
}

func (a *Agent) createWebsite(p map[string]any) (res Result, err error) {
	account, domain := str(p, "account"), str(p, "domain")
	if existing := a.stateRead(render.PathsOf(account, "").State); existing != nil {
		if existing.text("domain") == domain {
			return Result{"exists", map[string]any{"root": render.PathsOf(account, domain).Docroot, "uid": existing.integer("uid")}}, nil
		}
		return Result{}, herr("conflict", "That account already hosts another website.")
	}
	if _, found := a.Sys.UID(account); found {
		return Result{}, herr("conflict", "A system user with that name already exists.")
	}
	limits, paths := render.LimitsOf(p), render.PathsOf(account, domain)
	var undo []func() error
	defer func() {
		if err == nil {
			return
		}
		for i := len(undo) - 1; i >= 0; i-- { // best effort: a failing undo must not hide the original failure
			_ = undo[i]()
		}
		if len(undo) > 0 {
			_, _ = a.Sys.Run([]string{"systemctl", "daemon-reload"}, "")
		}
	}()
	run := func(argv ...string) error { _, e := a.Sys.Run(argv, ""); return e }
	if err = run("useradd", "--create-home", "--home-dir", paths.Home, "--shell", "/sbin/nologin", "--user-group", account); err != nil {
		return Result{}, err
	}
	undo = append(undo, func() error { return run("userdel", "--remove", account) })
	uid, _ := a.Sys.UID(account)
	// Isolation: the home folder is root's (OpenSSH needs that to chroot) and only the web server's group may pass through it, so no other tenant can look inside. The account's own
	// folders are group "apache" with the setgid bit: whatever PHP creates inherits that group, so the web server can read it and nobody else can.
	if err = a.Sys.Mkdir(paths.Home, system.FileMode(0o710), "root", "apache"); err != nil {
		return Result{}, err
	}
	for _, folder := range []string{paths.Home + "/sites", paths.Site, paths.Docroot} {
		if err = a.Sys.Mkdir(folder, system.FileMode(0o2750), account, "apache"); err != nil {
			return Result{}, err
		}
	}
	if err = a.Sys.Mkdir(paths.Logs, system.FileMode(0o750), account, ""); err != nil {
		return Result{}, err
	}
	if err = a.Sys.Mkdir(paths.Tmp, system.FileMode(0o700), account, ""); err != nil {
		return Result{}, err
	}
	if err = a.Sys.Write(paths.Docroot+"/index.html", fmt.Sprintf(indexPage, domain), 0o640, account, "apache"); err != nil {
		return Result{}, err
	}
	if err = a.quota(account, limits["disk_mb"], limits["inodes"]); err != nil {
		return Result{}, err
	}
	undo = append(undo, func() error {
		return run("xfs_quota", "-x", "-c", fmt.Sprintf("limit -p bsoft=0 bhard=0 %d", uid), "/home")
	})
	slice, _ := render.SliceUnit(account, limits)
	fpm, _ := render.FpmConf(account, limits)
	unit, e := render.Unit(account, str(p, "php_version"))
	if e != nil {
		return Result{}, e
	}
	for _, f := range [][2]string{{paths.Slice, slice}, {paths.FpmConf, fpm}, {paths.Unit, unit}} {
		if err = a.Sys.Write(f[0], f[1], 0o644, "root", ""); err != nil {
			return Result{}, err
		}
	}
	undo = append(undo, func() error {
		for _, p := range []string{paths.Slice, paths.FpmConf, paths.Unit} {
			_ = a.Sys.Remove(p)
		}
		return nil
	})
	if err = run("systemctl", "daemon-reload"); err != nil {
		return Result{}, err
	}
	if err = run("systemctl", "enable", "--now", "whp-php-"+account+".service"); err != nil {
		return Result{}, err
	}
	undo = append(undo, func() error { return run("systemctl", "disable", "--now", "whp-php-"+account+".service") })
	vhost, e := render.Vhost(account, domain, false, nil)
	if e != nil {
		return Result{}, e
	}
	if err = a.Sys.Write(paths.Vhost, vhost, 0o644, "root", ""); err != nil {
		return Result{}, err
	}
	undo = append(undo, func() error { return a.Sys.Remove(paths.Vhost) })
	if err = a.reloadWeb(); err != nil {
		return Result{}, err
	}
	if err = a.stateWrite(paths.State, State{"domain": domain, "php_version": str(p, "php_version"), "limits": limitsAsAny(limits), "suspended": false, "uid": uid}); err != nil {
		return Result{}, err
	}
	return Result{"created", map[string]any{"root": paths.Docroot, "uid": uid}}, nil
}

func (a *Agent) suspendWebsite(p map[string]any) (Result, error) {
	account := str(p, "account")
	st, err := a.need(account, "")
	if err != nil {
		return Result{}, err
	}
	if st.flag("suspended") {
		return Result{"suspended", nil}, nil
	}
	paths := render.PathsOf(account, st.text("domain"))
	vhost, _ := render.Vhost(account, st.text("domain"), true, nil)
	if err := a.Sys.Write(paths.Vhost, vhost, 0o644, "root", ""); err != nil {
		return Result{}, err
	}
	if _, err := a.Sys.Run([]string{"systemctl", "stop", "whp-php-" + account + ".service"}, ""); err != nil {
		return Result{}, err
	}
	if err := a.lockLogins(account, st, true); err != nil { // sessions already open end on their own; no new login is accepted
		return Result{}, err
	}
	if err := a.reloadWeb(); err != nil {
		return Result{}, err
	}
	next := st.copy()
	next["suspended"] = true
	return Result{"suspended", nil}, a.stateWrite(paths.State, next)
}

func (a *Agent) unsuspendWebsite(p map[string]any) (Result, error) {
	account := str(p, "account")
	st, err := a.need(account, "")
	if err != nil {
		return Result{}, err
	}
	if !st.flag("suspended") {
		return Result{"active", nil}, nil
	}
	paths := render.PathsOf(account, st.text("domain"))
	if _, err := a.Sys.Run([]string{"systemctl", "start", "whp-php-" + account + ".service"}, ""); err != nil {
		return Result{}, err
	}
	if err := a.lockLogins(account, st, false); err != nil {
		return Result{}, err
	}
	vhost, _ := render.Vhost(account, st.text("domain"), false, sslOf(st))
	if err := a.Sys.Write(paths.Vhost, vhost, 0o644, "root", ""); err != nil {
		return Result{}, err
	}
	if err := a.reloadWeb(); err != nil {
		return Result{}, err
	}
	next := st.copy()
	next["suspended"] = false
	return Result{"active", nil}, a.stateWrite(paths.State, next)
}

func (a *Agent) changePHP(p map[string]any) (Result, error) {
	account := str(p, "account")
	st, err := a.need(account, str(p, "domain"))
	if err != nil {
		return Result{}, err
	}
	if st.text("php_version") == str(p, "php_version") {
		return Result{"unchanged", nil}, nil
	}
	unit, e := render.Unit(account, str(p, "php_version"))
	if e != nil {
		return Result{}, e
	}
	if err := a.Sys.Write(render.PathsOf(account, "").Unit, unit, 0o644, "root", ""); err != nil {
		return Result{}, err
	}
	if _, err := a.Sys.Run([]string{"systemctl", "daemon-reload"}, ""); err != nil {
		return Result{}, err
	}
	if !st.flag("suspended") {
		if _, err := a.Sys.Run([]string{"systemctl", "restart", "whp-php-" + account + ".service"}, ""); err != nil {
			return Result{}, err
		}
	}
	next := st.copy()
	next["php_version"] = str(p, "php_version")
	return Result{"changed", nil}, a.stateWrite(render.PathsOf(account, "").State, next)
}

func sameLimits(a, b map[string]int64) bool {
	if len(a) != len(b) {
		return false
	}
	for k, v := range a {
		if b[k] != v {
			return false
		}
	}
	return true
}

func (a *Agent) setLimits(p map[string]any) (Result, error) {
	account := str(p, "account")
	st, err := a.need(account, "")
	if err != nil {
		return Result{}, err
	}
	limits := render.LimitsOf(p)
	if sameLimits(limits, st.Limits()) {
		return Result{"unchanged", nil}, nil
	}
	paths := render.PathsOf(account, "")
	if err := a.quota(account, limits["disk_mb"], limits["inodes"]); err != nil {
		return Result{}, err
	}
	slice, _ := render.SliceUnit(account, limits)
	fpm, _ := render.FpmConf(account, limits)
	if err := a.Sys.Write(paths.Slice, slice, 0o644, "root", ""); err != nil {
		return Result{}, err
	}
	if err := a.Sys.Write(paths.FpmConf, fpm, 0o644, "root", ""); err != nil {
		return Result{}, err
	}
	if _, err := a.Sys.Run([]string{"systemctl", "daemon-reload"}, ""); err != nil {
		return Result{}, err
	}
	if !st.flag("suspended") {
		if _, err := a.Sys.Run([]string{"systemctl", "restart", "whp-php-" + account + ".service"}, ""); err != nil {
			return Result{}, err
		}
	}
	next := st.copy()
	next["limits"] = limitsAsAny(limits)
	return Result{"changed", nil}, a.stateWrite(paths.State, next)
}

func (a *Agent) deleteWebsite(p map[string]any) (Result, error) {
	account := str(p, "account")
	st := a.stateRead(render.PathsOf(account, "").State)
	if st == nil {
		return Result{"absent", nil}, nil // already gone: deleting twice is not an error
	}
	if st.text("domain") != str(p, "domain") {
		return Result{}, herr("conflict", "That account hosts a different website.")
	}
	paths := render.PathsOf(account, st.text("domain"))
	data := map[string]any{}
	if str(p, "keep_archive") == "yes" { // first, and a failure here stops everything before anything is removed
		archive := "/var/backups/whp/archive-" + account + ".tar.zst"
		if _, err := a.Sys.Run([]string{"tar", "--zstd", "-cf", archive, "-C", "/home", account}, ""); err != nil {
			return Result{}, err
		}
		data["archive"] = archive
	}
	if _, err := a.Sys.Run([]string{"systemctl", "disable", "--now", "whp-php-" + account + ".service"}, ""); err != nil {
		return Result{}, err
	}
	for _, f := range []string{paths.Vhost, paths.Unit, paths.Slice, paths.FpmConf} {
		if err := a.Sys.Remove(f); err != nil {
			return Result{}, err
		}
	}
	if _, err := a.Sys.Run([]string{"systemctl", "daemon-reload"}, ""); err != nil {
		return Result{}, err
	}
	if err := a.reloadWeb(); err != nil {
		return Result{}, err
	}
	if err := a.removeLogins(account, st); err != nil {
		return Result{}, err
	}
	if _, err := a.Sys.Run([]string{"xfs_quota", "-x", "-c", fmt.Sprintf("limit -p bsoft=0 bhard=0 %d", st.integer("uid")), "/home"}, ""); err != nil {
		return Result{}, err
	}
	if _, err := a.Sys.Run([]string{"userdel", "--remove", account}, ""); err != nil {
		return Result{}, err
	}
	if err := a.Sys.Remove(paths.State); err != nil {
		return Result{}, err
	}
	return Result{"deleted", data}, nil
}

// sslOf is the account's HTTPS settings for the virtual host, or nil when it has no certificate.
func sslOf(st State) *render.SSL {
	m, ok := st["ssl"].(map[string]any)
	if !ok || len(m) == 0 {
		return nil
	}
	redirect, _ := m["https_redirect"].(bool)
	return &render.SSL{HTTPSRedirect: redirect}
}

func sortedKeys(m map[string]any) []string {
	keys := make([]string, 0, len(m))
	for k := range m {
		keys = append(keys, k)
	}
	sort.Strings(keys)
	return keys
}
