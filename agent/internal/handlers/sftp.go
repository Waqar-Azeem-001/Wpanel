package handlers

import (
	"fmt"
	"os"
	"sort"

	"whagent/internal/render"
	"whagent/internal/sandbox"
	"whagent/internal/system"
)

// SFTP logins (ADR 09): every login is an SFTP-only system user locked into one folder of its account. OpenSSH does the locking with ChrootDirectory; the folder shown inside
// is a bind mount of the account's chosen directory. The login shares the account's uid so it can read and write the account's files, and is in the group whp-sftp, to which
// one Match Group block applies. The password goes to chpasswd on standard input, never on a command line. Never run on a real sshd.

const (
	sshdConf = "/etc/ssh/sshd_config.d/60-whp-sftp.conf"
	sshdText = "# managed by WH Agent - do not edit by hand\nMatch Group whp-sftp\n    ChrootDirectory /srv/whp/sftp/%u\n    ForceCommand internal-sftp\n" +
		"    AllowTcpForwarding no\n    X11Forwarding no\n    PermitTTY no\n    PasswordAuthentication yes\n"
)

func init() {
	extraHandlers = append(extraHandlers, func(a *Agent) map[string]func(map[string]any) (Result, error) {
		return map[string]func(map[string]any) (Result, error){"SFTP_LIST": a.sftpList, "SFTP_CREATE": a.sftpCreate, "SFTP_SET_PASSWORD": a.sftpSetPassword, "SFTP_DELETE": a.sftpDelete}
	})
}

func loginOf(account, name string) string { return account + "_" + name }

func mountUnit(login string) string { return "srv-whp-sftp-" + login + "-files.mount" }

func logins(st State) map[string]any {
	m, _ := st["sftp"].(map[string]any)
	if m == nil {
		m = map[string]any{}
	}
	return m
}

func (a *Agent) ensureSSHD() error {
	if current, _ := a.Sys.Read(sshdConf); current == sshdText {
		return nil
	}
	if err := a.Sys.Write(sshdConf, sshdText, 0o644, "root", ""); err != nil {
		return err
	}
	if _, err := a.Sys.Run([]string{"sshd", "-t"}, ""); err != nil { // never reload a configuration that does not parse
		return err
	}
	_, err := a.Sys.Run([]string{"systemctl", "reload", "sshd"}, "")
	return err
}

// folderFor is the real folder a login will see, checked with the file sandbox (no links, inside the account's area).
func (a *Agent) folderFor(account, directory string) (string, error) {
	root := ""
	if a.RootFor != nil {
		root = a.RootFor(account)
	}
	if root == "" {
		return "", herr("not_found", "No such account on this node.")
	}
	box, err := sandbox.New(root)
	if err != nil {
		return "", herr("not_found", "No such account on this node.")
	}
	target, err := box.Resolve(directory, true)
	if err != nil {
		return "", herr("file_error", "%s", err.Error())
	}
	if info, serr := os.Stat(target); serr != nil || !info.IsDir() {
		return "", herr("file_error", "That folder does not exist in your hosting space. Create it in the file manager first.")
	}
	if directory == "" {
		return "/home/" + account + "/sites", nil
	}
	cleaned, _ := sandbox.CleanRelative(directory)
	return "/home/" + account + "/sites/" + cleaned, nil
}

func (a *Agent) sftpList(p map[string]any) (Result, error) {
	account := str(p, "account")
	st, err := a.need(account, "")
	if err != nil {
		return Result{}, err
	}
	limit := render.Defaults["sftp_accounts"]
	if v, ok := st.Limits()["sftp_accounts"]; ok {
		limit = v
	}
	rows := []any{}
	all := logins(st)
	for _, name := range sortedKeys(all) {
		dir, _ := all[name].(map[string]any)["directory"].(string)
		rows = append(rows, map[string]any{"name": name, "login": loginOf(account, name), "directory": dir})
	}
	return Result{"listed", map[string]any{"limit": limit, "logins": rows}}, nil
}

func (a *Agent) sftpCreate(p map[string]any) (res Result, err error) {
	account, name := str(p, "account"), str(p, "name")
	st, err := a.need(account, "")
	if err != nil {
		return Result{}, err
	}
	all := logins(st)
	if _, dup := all[name]; dup {
		return Result{}, herr("exists", "That SFTP login already exists.")
	}
	limit := render.Defaults["sftp_accounts"]
	if v, ok := st.Limits()["sftp_accounts"]; ok {
		limit = v
	}
	if int64(len(all)) >= limit {
		return Result{}, herr("limit_reached", "Your plan's limit of SFTP logins has been reached.")
	}
	source, err := a.folderFor(account, str(p, "directory"))
	if err != nil {
		return Result{}, err
	}
	login := loginOf(account, name)
	base, unit := "/srv/whp/sftp/"+login, "/etc/systemd/system/"+mountUnit(login)
	var undo []func() error
	defer func() {
		if err != nil {
			for i := len(undo) - 1; i >= 0; i-- {
				_ = undo[i]()
			}
		}
	}()
	if err = a.ensureSSHD(); err != nil {
		return Result{}, err
	}
	uid := st.integer("uid")
	if _, err = a.Sys.Run([]string{"useradd", "-o", "-u", fmt.Sprint(uid), "-g", account, "-G", "whp-sftp", "-M", "-d", base, "-s", "/usr/sbin/nologin", login}, ""); err != nil {
		return Result{}, err
	}
	undo = append(undo, func() error { _, e := a.Sys.Run([]string{"userdel", login}, ""); return e })
	if _, err = a.Sys.Run([]string{"chpasswd"}, login+":"+str(p, "password")+"\n"); err != nil {
		return Result{}, err
	}
	if err = a.Sys.Mkdir(base, 0o755, "root", ""); err != nil { // root-owned: OpenSSH refuses to chroot into anything else
		return Result{}, err
	}
	if err = a.Sys.Mkdir(base+"/files", 0o755, "root", ""); err != nil {
		return Result{}, err
	}
	undo = append(undo, func() error { return a.Sys.Remove(base) })
	text := "# managed by WH Agent\n[Unit]\nDescription=SFTP folder for " + login + "\n\n[Mount]\nWhat=" + source + "\nWhere=" + base + "/files\nType=none\nOptions=bind\n\n[Install]\nWantedBy=multi-user.target\n"
	if err = a.Sys.Write(unit, text, 0o644, "root", ""); err != nil {
		return Result{}, err
	}
	undo = append(undo, func() error { return a.Sys.Remove(unit) })
	if _, err = a.Sys.Run([]string{"systemctl", "daemon-reload"}, ""); err != nil {
		return Result{}, err
	}
	if _, err = a.Sys.Run([]string{"systemctl", "enable", "--now", mountUnit(login)}, ""); err != nil {
		return Result{}, err
	}
	all[name] = map[string]any{"directory": str(p, "directory")}
	next := st.copy()
	next["sftp"] = all
	if err = a.stateWrite(render.PathsOf(account, "").State, next); err != nil {
		return Result{}, err
	}
	return Result{"created", map[string]any{"login": login}}, nil
}

func (a *Agent) sftpSetPassword(p map[string]any) (Result, error) {
	account, name := str(p, "account"), str(p, "name")
	st, err := a.need(account, "")
	if err != nil {
		return Result{}, err
	}
	if _, ok := logins(st)[name]; !ok {
		return Result{}, herr("not_found", "That SFTP login no longer exists.")
	}
	if _, err := a.Sys.Run([]string{"chpasswd"}, loginOf(account, name)+":"+str(p, "password")+"\n"); err != nil {
		return Result{}, err
	}
	return Result{"changed", nil}, nil
}

func (a *Agent) removeLogin(account, name string) error {
	login := loginOf(account, name)
	if _, err := a.Sys.Run([]string{"systemctl", "disable", "--now", mountUnit(login)}, ""); err != nil {
		return err
	}
	if err := a.Sys.Remove("/etc/systemd/system/" + mountUnit(login)); err != nil {
		return err
	}
	if _, err := a.Sys.Run([]string{"userdel", login}, ""); err != nil {
		return err
	}
	return a.Sys.Remove("/srv/whp/sftp/" + login)
}

func (a *Agent) sftpDelete(p map[string]any) (Result, error) {
	account, name := str(p, "account"), str(p, "name")
	st, err := a.need(account, "")
	if err != nil {
		return Result{}, err
	}
	all := logins(st)
	if _, ok := all[name]; !ok {
		return Result{"absent", nil}, nil
	}
	if err := a.removeLogin(account, name); err != nil {
		return Result{}, err
	}
	if _, err := a.Sys.Run([]string{"systemctl", "daemon-reload"}, ""); err != nil {
		return Result{}, err
	}
	rest := map[string]any{}
	for k, v := range all {
		if k != name {
			rest[k] = v
		}
	}
	next := st.copy()
	next["sftp"] = rest
	return Result{"deleted", nil}, a.stateWrite(render.PathsOf(account, "").State, next)
}

// removeLogins and lockLogins are used by the website commands.
func (a *Agent) removeLogins(account string, st State) error {
	for _, name := range sortedKeys(logins(st)) {
		if err := a.removeLogin(account, name); err != nil {
			return err
		}
	}
	return nil
}

func (a *Agent) lockLogins(account string, st State, lock bool) error {
	flag := "--unlock"
	if lock {
		flag = "--lock"
	}
	names := sortedKeys(logins(st))
	sort.Strings(names)
	for _, name := range names {
		if _, err := a.Sys.Run([]string{"usermod", flag, loginOf(account, name)}, ""); err != nil {
			return err
		}
	}
	return nil
}

var _ system.System
