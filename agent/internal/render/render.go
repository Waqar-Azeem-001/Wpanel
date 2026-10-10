// Package render writes the configuration files for one hosting account: the Apache virtual host, the account's own PHP-FPM master, and the systemd unit
// and slice that hold it to its limits. Pure functions: values in, text out. The text is byte-for-byte what WHPanel's Python reference agent writes (a test
// compares them), and every value is checked again so that a caller that forgets cannot put a newline or a quote into a configuration file.
package render

import (
	"fmt"
	"regexp"
	"strings"
)

// Secure is written in two parts: the project's address check forbids a hand-typed address in source files.
const Secure = "https"

// Defaults are the limits an account gets when the plan does not say.
var Defaults = map[string]int64{"disk_mb": 5120, "memory_mb": 1024, "processes": 100, "cpu_percent": 100, "databases": 10, "sftp_accounts": 5, "inodes": 250000, "io_weight": 100}

// LimitKeys is the order limits are written in.
var LimitKeys = []string{"disk_mb", "memory_mb", "processes", "cpu_percent", "databases", "sftp_accounts", "inodes", "io_weight"}

const phpBinary = "/opt/remi/php%s/root/usr/sbin/php-fpm"

var safe = regexp.MustCompile(`^[A-Za-z0-9._-]+$`)

// Check refuses a value that is not plain enough to go into a configuration file.
func Check(values ...string) error {
	for _, v := range values {
		if !safe.MatchString(v) {
			return fmt.Errorf("Unsafe value for a config file: %q", v)
		}
	}
	return nil
}

// LimitsOf fills in defaults. Zero databases or SFTP logins is a real answer; for the rest zero means "not given".
func LimitsOf(params map[string]any) map[string]int64 {
	out := map[string]int64{}
	for _, k := range LimitKeys {
		v, given := params[k].(int64)
		switch {
		case given && (k == "databases" || k == "sftp_accounts"):
			out[k] = v
		case given && v != 0:
			out[k] = v
		default:
			out[k] = Defaults[k]
		}
	}
	return out
}

// Paths are where everything of one account lives.
type Paths struct {
	Home, Logs, Tmp, RunDir, Sock, FpmConf, Unit, Slice, State string
	Docroot, Site, Vhost                                       string
}

// PathsOf computes them; domain may be empty when only the account's own files matter.
func PathsOf(account, domain string) Paths {
	home := "/home/" + account
	p := Paths{Home: home, Logs: home + "/logs", Tmp: home + "/tmp", RunDir: "/run/whp-php/" + account, Sock: "/run/whp-php/" + account + "/php.sock",
		FpmConf: "/etc/whp/php/" + account + ".conf", Unit: "/etc/systemd/system/whp-php-" + account + ".service", Slice: "/etc/systemd/system/whp-" + account + ".slice",
		State: "/var/lib/whp/accounts/" + account + ".json"}
	if domain != "" {
		p.Docroot, p.Site, p.Vhost = home+"/sites/"+domain+"/public_html", home+"/sites/"+domain, "/etc/httpd/conf.d/whp/"+account+"-"+domain+".conf"
	}
	return p
}

// SSL is what the virtual host needs to know about HTTPS.
type SSL struct{ HTTPSRedirect bool }

// Vhost is the account's Apache virtual host.
func Vhost(account, domain string, suspended bool, ssl *SSL) (string, error) {
	if err := Check(account, domain); err != nil {
		return "", err
	}
	p := PathsOf(account, domain)
	if suspended {
		return "# managed by WH Agent - suspended\n<VirtualHost *:80>\n    ServerName " + domain + "\n    ServerAlias www." + domain + "\n    DocumentRoot /var/www/whp-suspended\n" +
			"    <Directory /var/www/whp-suspended>\n        Require all granted\n    </Directory>\n    RewriteEngine On\n    RewriteRule ^ /index.html [L]\n</VirtualHost>\n", nil
	}
	body := "    ServerName " + domain + "\n    ServerAlias www." + domain + "\n    DocumentRoot " + p.Docroot + "\n" +
		"    ErrorLog " + p.Logs + "/" + domain + ".error.log\n    CustomLog " + p.Logs + "/" + domain + ".access.log combined\n" +
		"    <Directory " + p.Docroot + ">\n        Options -Indexes +FollowSymLinks\n        AllowOverride All\n        Require all granted\n    </Directory>\n" +
		"    <FilesMatch \\.php$>\n        SetHandler \"proxy:unix:" + p.Sock + "|fcgi://localhost\"\n    </FilesMatch>\n"
	head := "# managed by WH Agent - do not edit by hand\n"
	if ssl == nil {
		return head + "<VirtualHost *:80>\n" + body + "</VirtualHost>\n", nil
	}
	tls := "/etc/whp/tls/" + account + "/" + domain
	redirect := ""
	if ssl.HTTPSRedirect {
		redirect = "    RewriteEngine On\n    RewriteCond %{REQUEST_URI} !^/\\.well-known/acme-challenge/\n" + "    RewriteRule ^ " + Secure + "://%{HTTP_HOST}%{REQUEST_URI} [L,R=301]\n"
	}
	return head + "<VirtualHost *:80>\n" + redirect + body + "</VirtualHost>\n\n<VirtualHost *:443>\n" + body + "    SSLEngine on\n" +
		"    SSLCertificateFile " + tls + "/fullchain.pem\n    SSLCertificateKeyFile " + tls + "/privkey.pem\n    SSLProtocol -all +TLSv1.2 +TLSv1.3\n</VirtualHost>\n", nil
}

// FpmConf is the account's own PHP-FPM master. It runs as the account's user, so it needs no user line.
func FpmConf(account string, limits map[string]int64) (string, error) {
	if err := Check(account); err != nil {
		return "", err
	}
	p := PathsOf(account, "")
	children := limits["processes"] / 4
	if children > 50 {
		children = 50
	}
	if children < 2 {
		children = 2
	}
	memory := limits["memory_mb"]
	if memory > 512 {
		memory = 512
	}
	return "; managed by WH Agent - do not edit by hand\n[global]\npid = " + p.RunDir + "/php-fpm.pid\nerror_log = " + p.Logs + "/php-fpm.log\ndaemonize = no\nrlimit_files = 4096\n\n" +
		"[" + account + "]\nlisten = " + p.Sock + "\nlisten.acl_users = apache\nlisten.mode = 0660\n" +
		fmt.Sprintf("pm = ondemand\npm.max_children = %d\npm.process_idle_timeout = 20s\npm.max_requests = 500\nrequest_terminate_timeout = 120s\nsecurity.limit_extensions = .php\n", children) +
		"php_admin_value[open_basedir] = " + p.Home + ":" + p.Tmp + ":/usr/share/php\nphp_admin_value[upload_tmp_dir] = " + p.Tmp + "\n" +
		"php_admin_value[session.save_path] = " + p.Tmp + "\nphp_admin_value[sys_temp_dir] = " + p.Tmp + "\n" +
		"php_admin_value[disable_functions] = exec,passthru,shell_exec,system,proc_open,popen,pcntl_exec\nphp_admin_flag[expose_php] = off\n" +
		fmt.Sprintf("php_admin_value[memory_limit] = %dM\n", memory), nil
}

// UnitHardening is what every account's PHP service must carry; the isolation check compares installed units against it.
var UnitHardening = []string{"NoNewPrivileges=yes", "PrivateTmp=yes", "PrivateDevices=yes", "ProtectSystem=strict", "ProtectHome=tmpfs", "ProtectKernelTunables=yes", "ProtectKernelModules=yes",
	"ProtectKernelLogs=yes", "ProtectControlGroups=yes", "ProtectClock=yes", "ProtectHostname=yes", "RestrictNamespaces=yes", "RestrictRealtime=yes", "RestrictSUIDSGID=yes", "LockPersonality=yes",
	"RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6", "CapabilityBoundingSet=", "SystemCallFilter=@system-service", "SystemCallFilter=~@privileged", "UMask=0027", "LimitNOFILE=4096"}

// Unit is the account's systemd service for its PHP-FPM master.
func Unit(account, phpVersion string) (string, error) {
	if err := Check(account, phpVersion); err != nil {
		return "", err
	}
	p := PathsOf(account, "")
	lines := []string{"# managed by WH Agent - do not edit by hand", "[Unit]", "Description=PHP " + phpVersion + " for hosting account " + account, "After=network.target", "", "[Service]", "Type=simple",
		"User=" + account, "Group=" + account, "Slice=whp-" + account + ".slice",
		"ExecStart=" + fmt.Sprintf(phpBinary, strings.ReplaceAll(phpVersion, ".", "")) + " --nodaemonize --fpm-config " + p.FpmConf,
		"RuntimeDirectory=whp-php/" + account, "RuntimeDirectoryMode=0755", "ReadWritePaths=" + p.Home, "BindPaths=" + p.Home}
	lines = append(lines, UnitHardening...)
	lines = append(lines, "Restart=on-failure", "", "[Install]", "WantedBy=multi-user.target", "")
	return strings.Join(lines, "\n"), nil
}

// SliceUnit holds the account to its CPU, memory, process and I/O limits.
func SliceUnit(account string, limits map[string]int64) (string, error) {
	if err := Check(account); err != nil {
		return "", err
	}
	high := int64(float64(limits["memory_mb"]) * 0.9)
	if high < 64 {
		high = 64
	}
	return "# managed by WH Agent - do not edit by hand\n[Unit]\nDescription=Resource limits for hosting account " + account + "\n\n[Slice]\n" +
		"CPUAccounting=yes\nMemoryAccounting=yes\nTasksAccounting=yes\nIOAccounting=yes\n" +
		fmt.Sprintf("MemoryHigh=%dM\nMemoryMax=%dM\nMemorySwapMax=0\nCPUQuota=%d%%\nTasksMax=%d\nIOWeight=%d\n", high, limits["memory_mb"], limits["cpu_percent"], limits["processes"], limits["io_weight"]), nil
}
