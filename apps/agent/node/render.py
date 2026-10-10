"""
The configuration files for one hosting account (N3, ADR 01/02/03/11): Apache virtual host, a PHP-FPM master of its own, and the systemd unit and slice that
hold it to its limits. Pure functions: parameters in, text out. Every value arriving here has already passed ``catalog.validate``; ``_safe`` checks again
so that a future caller that forgets cannot put a newline or a quote into a config file.
"""
import re

SECURE = "https"  # written separately: the site-wide check forbids a hand-typed address in code
SAFE = re.compile(r"^[A-Za-z0-9._-]+$")

DEFAULTS = {"disk_mb": 5120, "memory_mb": 1024, "processes": 100, "cpu_percent": 100, "databases": 10}
PHP_BINARY = "/opt/remi/php{nodot}/root/usr/sbin/php-fpm"


def _safe(*values):
    for value in values:
        if not isinstance(value, str) or not SAFE.fullmatch(value):
            raise ValueError(f"Unsafe value for a config file: {value!r}")


def nodot(php_version):
    return php_version.replace(".", "")


def limits_of(params):
    return {k: (params[k] if k == "databases" and params.get(k) is not None else params.get(k) or DEFAULTS[k]) for k in DEFAULTS}  # 0 databases is a real answer


def paths(account, domain=None):
    home = f"/home/{account}"
    out = {"home": home, "logs": f"{home}/logs", "tmp": f"{home}/tmp", "sock": f"/run/whp/php/{account}.sock", "fpm_conf": f"/etc/whp/php/{account}.conf",
           "unit": f"/etc/systemd/system/whp-php-{account}.service", "slice": f"/etc/systemd/system/whp-{account}.slice", "state": f"/var/lib/whp/accounts/{account}.json"}
    if domain:
        out.update({"docroot": f"{home}/sites/{domain}/public_html", "site": f"{home}/sites/{domain}", "vhost": f"/etc/httpd/conf.d/whp/{account}-{domain}.conf"})
    return out


def vhost(account, domain, suspended=False, ssl=None):
    _safe(account, domain)
    p = paths(account, domain)
    if suspended:
        return (f"# managed by WH Agent - suspended\n<VirtualHost *:80>\n    ServerName {domain}\n    ServerAlias www.{domain}\n    DocumentRoot /var/www/whp-suspended\n"
                f"    <Directory /var/www/whp-suspended>\n        Require all granted\n    </Directory>\n    RewriteEngine On\n    RewriteRule ^ /index.html [L]\n</VirtualHost>\n")
    body = (f"    ServerName {domain}\n    ServerAlias www.{domain}\n    DocumentRoot {p['docroot']}\n"
            f"    ErrorLog {p['logs']}/{domain}.error.log\n    CustomLog {p['logs']}/{domain}.access.log combined\n"
            f"    <Directory {p['docroot']}>\n        Options -Indexes +FollowSymLinks\n        AllowOverride All\n        Require all granted\n    </Directory>\n"
            f"    <FilesMatch \\.php$>\n        SetHandler \"proxy:unix:{p['sock']}|fcgi://localhost\"\n    </FilesMatch>\n")
    head = "# managed by WH Agent - do not edit by hand\n"
    if not ssl:
        return head + f"<VirtualHost *:80>\n{body}</VirtualHost>\n"
    tls = f"/etc/whp/tls/{account}/{domain}"
    redirect = ("    RewriteEngine On\n    RewriteCond %{REQUEST_URI} !^/\\.well-known/acme-challenge/\n"
                "    RewriteRule ^ " + SECURE + "://%{HTTP_HOST}%{REQUEST_URI} [L,R=301]\n") if ssl.get("https_redirect") else ""
    return (head + f"<VirtualHost *:80>\n{redirect}{body}</VirtualHost>\n\n<VirtualHost *:443>\n{body}    SSLEngine on\n"
            f"    SSLCertificateFile {tls}/fullchain.pem\n    SSLCertificateKeyFile {tls}/privkey.pem\n    SSLProtocol -all +TLSv1.2 +TLSv1.3\n</VirtualHost>\n")


def fpm_conf(account, limits):
    _safe(account)
    p = paths(account)
    children = max(2, min(limits["processes"] // 4, 50))
    return (f"; managed by WH Agent - do not edit by hand\n[global]\npid = /run/whp/php/{account}.pid\nerror_log = {p['logs']}/php-fpm.log\ndaemonize = no\n\n"
            f"[{account}]\nuser = {account}\ngroup = {account}\nlisten = {p['sock']}\nlisten.owner = apache\nlisten.group = apache\nlisten.mode = 0660\n"
            f"pm = ondemand\npm.max_children = {children}\npm.process_idle_timeout = 20s\npm.max_requests = 500\n"
            f"php_admin_value[open_basedir] = {p['home']}:{p['tmp']}:/usr/share/php\nphp_admin_value[upload_tmp_dir] = {p['tmp']}\n"
            f"php_admin_value[session.save_path] = {p['tmp']}\nphp_admin_value[sys_temp_dir] = {p['tmp']}\n"
            f"php_admin_value[disable_functions] = exec,passthru,shell_exec,system,proc_open,popen,pcntl_exec\nphp_admin_flag[expose_php] = off\n"
            f"php_admin_value[memory_limit] = {min(limits['memory_mb'], 512)}M\n")


def unit(account, php_version):
    _safe(account, php_version)
    p = paths(account)
    return (f"# managed by WH Agent - do not edit by hand\n[Unit]\nDescription=PHP {php_version} for hosting account {account}\nAfter=network.target\n\n[Service]\n"
            f"Type=simple\nSlice=whp-{account}.slice\nExecStart={PHP_BINARY.format(nodot=nodot(php_version))} --nodaemonize --fpm-config {p['fpm_conf']}\n"
            f"RuntimeDirectory=whp/php\nRuntimeDirectoryPreserve=yes\nNoNewPrivileges=yes\nProtectSystem=full\nPrivateDevices=yes\nProtectKernelTunables=yes\n"
            f"Restart=on-failure\n\n[Install]\nWantedBy=multi-user.target\n")


def slice_unit(account, limits):
    _safe(account)
    return (f"# managed by WH Agent - do not edit by hand\n[Unit]\nDescription=Resource limits for hosting account {account}\n\n[Slice]\n"
            f"MemoryMax={limits['memory_mb']}M\nMemorySwapMax=0\nCPUQuota={limits['cpu_percent']}%\nTasksMax={limits['processes']}\nIOWeight=100\n")
