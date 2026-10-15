"""
Demo adapter: a provider that answers every hosting capability it supports with fixed SAMPLE data, so the customer
screens (Websites dashboard, File Manager now; DNS, SSL, PHP... as they are built) can be seen working, designed and
tested before any real server is connected. It makes no network calls. It is for development and demonstration only -
never a production server kind - and what it reports says so ("Sample data").

Usage, SSL and PHP are sample values derived from the account's username, so they are stable between page loads and
differ between accounts. Files are REAL files on this machine, inside one folder per account (``DEMO_FILES_ROOT``),
worked on through the same sandbox as a real provider, so the File Manager is exercised for real.
"""
from pathlib import Path

from django.conf import settings

from .base import HostingError
from .capabilities import (DatabaseAdminAccess, DatabasesCapability, DnsCapability, DomainsCapability, EmailCapability,
                           AccessCapability, AppsCapability, BackupsCapability, InsightsCapability, CpanelAccessCapability, CpanelAccessSession, NodeCapability, CronCapability, SecurityCapability, SshCapability, FilesCapability, FtpCapability, LogsCapability, PhpCapability, SslCapability, WebmailAccess)
from .demo_databases import DemoDatabases
from .demo_backups import DemoBackups
from .demo_cron import DemoCron
from .demo_domains import SERVER_IP, DemoDomains
from .demo_email import DemoEmail
from .demo_insights import DemoInsights
from .demo_cpanel_access import create_token
from .demo_node import demo_node_status
from .demo_php import DemoPhp
from .demo_security import DemoSecurity
from .demo_tools import DemoAccess, DemoFtp, DemoLogs
from .demo_ssl import DemoSsl
from .manual import ManualAdapter
from .sandbox import Sandbox

SAMPLE_FILES = {
    "public_html/index.html": "<!doctype html>\n<html>\n<head><title>Welcome</title><link rel=\"stylesheet\" href=\"css/style.css\"></head>\n"
                              "<body><h1>Your website is ready</h1><p>Edit this file in the File Manager.</p></body>\n</html>\n",
    "public_html/css/style.css": "body { font-family: sans-serif; margin: 2rem; }\nh1 { color: #5b2e8f; }\n",
    "public_html/robots.txt": "User-agent: *\nAllow: /\n",
    "logs/README.txt": "Access and error logs appear here on a real server.\n",
}


def files_root():
    return Path(getattr(settings, "DEMO_FILES_ROOT", settings.BASE_DIR / "demo_hosting_files"))


def _seed(username):
    return sum(ord(c) for c in username)


def databases_for(username):
    """The account's demo database service. Its state lives beside the website folder, never inside it."""
    return DemoDatabases(files_root() / f"{username}.state", username)


def email_for(username):
    """The account's demo mail service, for the account's own domain. State lives beside the website folder."""
    from apps.hosting.models import HostingAccount

    account = HostingAccount.objects.filter(username=username, server__kind="demo").first()
    return DemoEmail(files_root() / f"{username}.state", account.domain if account else "")


def domains_for(username):
    """The account's demo domain and DNS service. A new domain gets a website folder (and a placeholder page) in the file manager."""
    from apps.hosting.models import HostingAccount

    account = HostingAccount.objects.filter(username=username, server__kind="demo").first()

    def make_folder(path):
        box = sandbox_for(username)
        walked = ""
        for part in [p for p in path.split("/") if p]:
            walked = f"{walked}/{part}" if walked else part
            try:
                box.make_directory(walked)
            except HostingError:
                pass  # it already exists
        try:
            box.write_file(f"{path}/index.html", b"<!doctype html><title>Coming soon</title><h1>Coming soon</h1>")
        except HostingError:
            pass  # a page is already there: leave it alone

    return DemoDomains(files_root() / f"{username}.state", account.domain if account else "", make_folder)


def _domain_names(username):
    return [d.name for d in domains_for(username).domains()]


def _points_here(username, name):
    """Does this name's DNS lead to the demo server? An A record with the server's address, or a CNAME to a name that does."""
    service = domains_for(username)
    zones = sorted({d.name for d in service.domains() if d.kind != "subdomain"}, key=len, reverse=True)

    def lookup(host, depth=0):
        zone = next((z for z in zones if host == z or host.endswith("." + z)), None)
        if zone is None or depth > 3:
            return False
        label = "@" if host == zone else host[: -len(zone) - 1]
        try:
            records = service.records(zone)
        except HostingError:
            return False
        for r in records:
            if r.name == label and r.type == "A" and r.value == SERVER_IP:
                return True
        return any(r.name == label and r.type == "CNAME" and lookup(r.value.rstrip(".").lower(), depth + 1) for r in records)

    return lookup(name)


def ssl_for(username):
    """The account's demo certificate service. State lives beside the website folder."""
    return DemoSsl(files_root() / f"{username}.state", lambda: _domain_names(username), lambda n: _points_here(username, n))


def _account_domain(username):
    from apps.hosting.models import HostingAccount

    account = HostingAccount.objects.filter(username=username, server__kind="demo").first()
    return account.domain if account else ""


def security_for(username):
    """The account's demo security tools, working on its real (sandbox) website files."""
    sandbox_for(username)
    return DemoSecurity(files_root() / f"{username}.state", files_root() / username, _account_domain(username))


def backups_for(username):
    """The account's demo backups: zip archives kept beside the website folder."""
    sandbox_for(username)  # the website folder exists (with its sample files) before the first backup
    return DemoBackups(files_root() / f"{username}.state", files_root() / username, files_root() / "_offsite" / username)


def logs_for(username):
    """The account's demo logs: real files in its logs folder."""
    return DemoLogs(sandbox_for(username), _account_domain(username))


def ftp_for(username):
    """The account's demo FTP accounts. A folder must exist in the website space."""
    box = sandbox_for(username)

    def folder_exists(rel):
        try:
            return box.resolve(rel).is_dir()
        except HostingError:
            return False

    return DemoFtp(files_root() / f"{username}.state", _account_domain(username), folder_exists)


def access_for(username):
    """The account's demo IP block list."""
    return DemoAccess(files_root() / f"{username}.state")


def cron_for(username):
    """The account's demo scheduled jobs."""
    return DemoCron(files_root() / f"{username}.state")


def php_for(username):
    """The account's demo PHP settings service."""
    return DemoPhp(files_root() / f"{username}.state", lambda: _domain_names(username))


def sandbox_for(username):
    """The account's folder, created with a few sample files the first time it is used."""
    root = files_root() / username
    fresh = not root.exists()
    box = Sandbox(root, meta_file=files_root() / f"{username}.state" / "permissions.json")
    if fresh:
        for rel, text in SAMPLE_FILES.items():
            target = root / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(text, encoding="utf-8")
    return box


class DemoAdapter(AppsCapability, DomainsCapability, DnsCapability, EmailCapability, DatabasesCapability, FilesCapability, SslCapability,
                  PhpCapability, CronCapability, LogsCapability, FtpCapability, AccessCapability, BackupsCapability, SecurityCapability, InsightsCapability, CpanelAccessCapability, SshCapability, NodeCapability, ManualAdapter):
    def get_usage(self, username):
        seed = _seed(username)
        disk_limit = 20480
        bandwidth_limit = 102400
        return {"disk_used_mb": (seed * 37) % disk_limit, "disk_limit_mb": disk_limit,
                "bandwidth_used_mb": (seed * 91) % bandwidth_limit, "bandwidth_limit_mb": bandwidth_limit}

    # --- Apps (sample) -------------------------------------------------------------------------------------------------

    def install_wordpress(self, username, domain, title, admin_user, admin_password, admin_email):
        """Nothing is downloaded or installed here: the demo provider answers as a real one would when the installation has finished."""
        return {"state": "ready", "version": "6.7.1 (sample)", "admin_url": "https" + f"://{domain}/wp-admin/"}

    # --- Files (real, sandboxed) ---------------------------------------------------------------------------------

    def home_path(self, username):
        return f"/home/{username}"

    def list_files(self, username, path):
        return sandbox_for(username).list_files(path)

    def read_file(self, username, path, *, max_bytes):
        return sandbox_for(username).read_file(path, max_bytes=max_bytes)

    def _require_room(self, username, extra_bytes):
        """The plan's disk limit is enforced here, not on the screen: a write that would go over it is refused."""
        usage = self.get_usage(username)
        if (usage["disk_used_mb"] * 1048576) + extra_bytes >= usage["disk_limit_mb"] * 1048576:
            raise HostingError("Your disk space is full, so this could not be saved. Delete files you do not need, or upgrade your plan.")

    def write_file(self, username, path, content, *, overwrite=False):
        self._require_room(username, len(content))
        sandbox_for(username).write_file(path, content, overwrite=overwrite)

    def make_directory(self, username, path):
        sandbox_for(username).make_directory(path)

    def rename_path(self, username, path, new_path):
        sandbox_for(username).rename_path(path, new_path)

    def copy_path(self, username, path, new_path):
        self._require_room(username, 0)
        sandbox_for(username).copy_path(path, new_path)

    def set_permissions(self, username, path, mode):
        sandbox_for(username).set_permissions(path, mode)

    def delete_path(self, username, path):
        sandbox_for(username).delete_path(path)

    def search_files(self, username, path, query):
        return sandbox_for(username).search_files(path, query)

    def archive_paths(self, username, paths, archive_path):
        self._require_room(username, 0)
        return sandbox_for(username).archive_paths(paths, archive_path)

    def extract_archive(self, username, path, destination):
        self._require_room(username, 0)
        return sandbox_for(username).extract_archive(path, destination)

    def trash_path(self, username, path):
        return sandbox_for(username).trash_path(path)

    def list_trash(self, username):
        return sandbox_for(username).list_trash()

    def restore_trash(self, username, item_id):
        return sandbox_for(username).restore_trash(item_id)

    def purge_trash(self, username, item_id):
        sandbox_for(username).purge_trash(item_id)

    # --- Databases (simulated service with real rules) -----------------------------------------------------------

    def database_limits(self, username):
        return databases_for(username).limits()

    def database_admin_access(self, username, database):
        databases_for(username).sql(database)  # raises if it does not exist
        return DatabaseAdminAccess(kind="embedded")  # no phpMyAdmin behind a demo account: WHPanel's own browser serves it

    def list_tables(self, username, database):
        return databases_for(username).sql(database).tables()

    def browse_table(self, username, database, table, offset, limit):
        return databases_for(username).sql(database).browse(table, offset, limit)

    def describe_table(self, username, database, table):
        return databases_for(username).sql(database).describe(table)

    def run_query(self, username, database, sql):
        return databases_for(username).sql(database).run(sql)

    def insert_row(self, username, database, table, values):
        databases_for(username).sql(database).insert_row(table, values)

    def update_row(self, username, database, table, key, values):
        databases_for(username).sql(database).update_row(table, key, values)

    def delete_row(self, username, database, table, key):
        databases_for(username).sql(database).delete_row(table, key)

    def list_database_grants(self, username):
        return databases_for(username).grants()

    def list_databases(self, username):
        return databases_for(username).databases()

    def create_database(self, username, name):
        databases_for(username).create_database(name)

    def delete_database(self, username, name):
        databases_for(username).delete_database(name)

    def list_database_users(self, username):
        return databases_for(username).users()

    def create_database_user(self, username, name, password):
        databases_for(username).create_user(name, password)

    def set_database_user_password(self, username, name, password):
        databases_for(username).set_password(name, password)

    def delete_database_user(self, username, name):
        databases_for(username).delete_user(name)

    def grant_database_access(self, username, database, user, privileges):
        databases_for(username).grant(database, user, privileges)

    # --- Email (simulated service with real rules) ----------------------------------------------------------------

    def email_limits(self, username):
        return email_for(username).limits()

    def list_mailboxes(self, username):
        return email_for(username).mailboxes()

    def create_mailbox(self, username, address, password, *, quota_mb=None):
        email_for(username).create_mailbox(address, password, quota_mb)

    def delete_mailbox(self, username, address):
        email_for(username).delete_mailbox(address)

    def set_mailbox_password(self, username, address, password):
        email_for(username).set_password(address, password)

    def set_mailbox_quota(self, username, address, quota_mb):
        email_for(username).set_quota(address, quota_mb)

    def list_forwarders(self, username):
        return email_for(username).forwarders()

    def create_forwarder(self, username, source, destination):
        email_for(username).create_forwarder(source, destination)

    def delete_forwarder(self, username, source, destination):
        email_for(username).delete_forwarder(source, destination)

    def list_autoresponders(self, username):
        return email_for(username).autoresponders()

    def set_autoresponder(self, username, address, subject, body, enabled):
        email_for(username).set_autoresponder(address, subject, body, enabled)

    def delete_autoresponder(self, username, address):
        email_for(username).delete_autoresponder(address)

    def get_mail_routing(self, username):
        return email_for(username).routing()

    def set_mail_routing(self, username, mode):
        email_for(username).set_routing(mode)

    def get_spam_settings(self, username):
        return email_for(username).spam()

    def set_spam_settings(self, username, settings):
        email_for(username).set_spam(settings)

    def webmail_access(self, username, address):
        email_for(username).messages(address)  # raises if the mailbox does not exist
        return WebmailAccess(kind="embedded")  # no Roundcube behind a demo account: WHPanel's own inbox viewer serves it

    def message_folders(self, username, address):
        return email_for(username).folders(address)

    def list_messages(self, username, address, folder="inbox"):
        return email_for(username).messages(address, folder)

    def move_message(self, username, address, message_id, folder):
        email_for(username).move_message(address, message_id, folder)

    def delete_message(self, username, address, message_id):
        email_for(username).delete_message(address, message_id)

    def send_message(self, username, address, to, subject, body, html="", draft_id=""):
        email_for(username).send_message(address, to, subject, body, html, draft_id)

    def set_message_starred(self, username, address, message_id, starred):
        email_for(username).set_starred(address, message_id, starred)

    def save_draft(self, username, address, to, subject, body, html="", draft_id=""):
        return email_for(username).save_draft(address, to, subject, body, html, draft_id)

    def get_mail_profile(self, username, address):
        return email_for(username).profile(address)

    def set_mail_profile(self, username, address, profile):
        email_for(username).set_profile(address, profile)

    def read_message(self, username, address, message_id):
        return email_for(username).read_message(address, message_id)

    # --- Domains and DNS (simulated service with real rules) -------------------------------------------------------

    def domain_limits(self, username):
        return domains_for(username).limits()

    def list_domains(self, username):
        return domains_for(username).domains()

    def add_domain(self, username, name, kind, document_root=""):
        domains_for(username).add_domain(name, kind, document_root)

    def remove_domain(self, username, name):
        domains_for(username).remove_domain(name)

    def set_redirect(self, username, name, redirect_to, permanent=True):
        domains_for(username).set_redirect(name, redirect_to, permanent)

    def list_dns_records(self, username, domain):
        return domains_for(username).records(domain)

    def add_dns_record(self, username, domain, record):
        return domains_for(username).add_record(domain, record)

    def update_dns_record(self, username, domain, record):
        domains_for(username).update_record(domain, record)

    def delete_dns_record(self, username, domain, record_id):
        domains_for(username).delete_record(domain, record_id)

    # --- SSL ---------------------------------------------------------------------------------------------------

    def list_ssl_status(self, username):
        return ssl_for(username).list_status()

    def get_ssl_status(self, username, domain):
        return ssl_for(username).status(domain)

    def request_ssl(self, username, domain):
        ssl_for(username).request(domain)

    def remove_ssl(self, username, domain):
        ssl_for(username).remove(domain)

    def set_ssl_auto_renew(self, username, domain, enabled):
        ssl_for(username).set_auto_renew(domain, enabled)

    def set_https_redirect(self, username, domain, enabled):
        ssl_for(username).set_redirect(domain, enabled)

    # --- PHP ---------------------------------------------------------------------------------------------------

    def php_options(self, username):
        return php_for(username).options()

    def list_php_extensions(self, username, domain):
        return php_for(username).extensions(domain)

    def set_php_extension(self, username, domain, name, enabled):
        php_for(username).set_extension(domain, name, enabled)

    def get_php_settings(self, username, domain):
        return php_for(username).settings(domain)

    def set_php_settings(self, username, domain, settings):
        php_for(username).set_settings(domain, settings)

    # --- Cron --------------------------------------------------------------------------------------------------

    def cron_options(self, username):
        return cron_for(username).options()

    def list_cron_jobs(self, username):
        return cron_for(username).jobs()

    def create_cron_job(self, username, schedule, command):
        return cron_for(username).create(schedule, command)

    def update_cron_job(self, username, job_id, *, schedule=None, command=None, enabled=None):
        cron_for(username).update(job_id, schedule=schedule, command=command, enabled=enabled)

    def delete_cron_job(self, username, job_id):
        cron_for(username).delete(job_id)

    def run_cron_job(self, username, job_id):
        return cron_for(username).run(job_id)

    # --- Logs, FTP and IP blocking ---------------------------------------------------------------------------

    def read_log(self, username, kind, lines, query):
        return logs_for(username).read(kind, lines, query)

    def clear_log(self, username, kind):
        logs_for(username).clear(kind)

    def ftp_options(self, username):
        return ftp_for(username).options()

    def list_ftp_accounts(self, username):
        return ftp_for(username).accounts()

    def create_ftp_account(self, username, name, password, directory):
        return ftp_for(username).create(name, password, directory)

    def set_ftp_password(self, username, login, password):
        ftp_for(username).set_password(login, password)

    def delete_ftp_account(self, username, login):
        ftp_for(username).delete(login)

    def list_ip_rules(self, username):
        return access_for(username).rules()

    def add_ip_rule(self, username, network, note):
        return access_for(username).add(network, note)

    def delete_ip_rule(self, username, rule_id):
        access_for(username).delete(rule_id)

    # --- Backups -----------------------------------------------------------------------------------------------

    def backup_options(self, username):
        return backups_for(username).options()

    def list_backups(self, username):
        return backups_for(username).list()

    def create_backup(self, username, kind):
        return backups_for(username).create(kind)

    def verify_backup(self, username, backup_id):
        return backups_for(username).verify(backup_id)

    def restore_backup(self, username, backup_id):
        backups_for(username).restore(backup_id)

    def delete_backup(self, username, backup_id):
        backups_for(username).delete(backup_id)

    def set_backup_schedule(self, username, schedule):
        backups_for(username).set_schedule(schedule)

    def set_backup_offsite(self, username, enabled):
        backups_for(username).set_offsite(enabled)

    # --- Security ----------------------------------------------------------------------------------------------

    def get_scan_result(self, username):
        return security_for(username).scan_result()

    def run_malware_scan(self, username):
        return security_for(username).scan()

    def quarantine_file(self, username, path):
        return security_for(username).quarantine(path)

    def list_quarantine(self, username):
        return security_for(username).quarantine_list()

    def restore_quarantined(self, username, item_id):
        security_for(username).restore(item_id)

    def list_protected_dirs(self, username):
        return security_for(username).protected()

    def protect_directory(self, username, path, login, password):
        security_for(username).protect(path, login, password)

    def unprotect_directory(self, username, path):
        security_for(username).unprotect(path)

    def ssh_status(self, username):
        return security_for(username).ssh_status()

    def set_ssh_enabled(self, username, enabled):
        security_for(username).set_ssh(enabled)

    def add_ssh_key(self, username, label, public_key):
        return security_for(username).add_key(label, public_key)

    def delete_ssh_key(self, username, fingerprint):
        security_for(username).delete_key(fingerprint)

    # --- SSH access (the same sandbox state the Security page shows) -----------------------------------------------

    def ssh_access_status(self, username):
        return security_for(username).ssh_status()

    def set_ssh_access(self, username, enabled):
        security_for(username).set_ssh(enabled)

    def add_ssh_access_key(self, username, label, public_key):
        return security_for(username).add_key(label, public_key)

    def remove_ssh_access_key(self, username, key_id):
        security_for(username).delete_key(key_id)

    def integrity_status(self, username):
        return security_for(username).integrity_status()

    def create_integrity_baseline(self, username):
        return security_for(username).baseline()

    def check_integrity(self, username):
        return security_for(username).check()

    # --- Usage history and performance -------------------------------------------------------------------------

    def usage_history(self, username, days):
        return DemoInsights(_seed(username), lambda: self.get_usage(username)).history(days)

    def performance_stats(self, username):
        return DemoInsights(_seed(username), lambda: self.get_usage(username)).performance()

    def statistics_access(self, username, domain):
        raise HostingError("A demo server has no visitor statistics. A real server's own statistics open here.")

    # --- The machine itself --------------------------------------------------------------------------------------

    def node_status(self):
        return demo_node_status(self.host)

    # --- "Open cPanel" (transitional, demo stand-in) -----------------------------------------------------------------

    def open_cpanel_access(self, username):
        from datetime import datetime, timezone

        from django.urls import reverse

        from apps.hosting.models import HostingAccount

        account = HostingAccount.objects.filter(username=username, server__kind="demo").first()
        if account is None:
            raise HostingError("This account has no sample cPanel.")
        token, expires = create_token(files_root() / f"{username}.state")
        return CpanelAccessSession(url=reverse("hosting_customer:cpanel_access_demo", args=[account.pk]) + f"?session={token}",
                                  expires_at=datetime.fromtimestamp(expires, tz=timezone.utc).isoformat())
