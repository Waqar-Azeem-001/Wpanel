"""
Demo adapter: a provider that answers every hosting capability it supports with fixed SAMPLE data, so the customer
screens (Websites dashboard, File Manager now; DNS, SSL, PHP... as they are built) can be seen working, designed and
tested before any real server is connected. It makes no network calls. It is for development and demonstration only -
never a production server kind - and what it reports says so ("Sample data").

Usage, SSL and PHP are sample values derived from the account's username, so they are stable between page loads and
differ between accounts. Files are REAL files on this machine, inside one folder per account (``DEMO_FILES_ROOT``),
worked on through the same sandbox a native provider's agent will use, so the File Manager is exercised for real.
"""
from pathlib import Path

from django.conf import settings

from .base import HostingError
from .capabilities import (DatabaseAdminAccess, DatabasesCapability, FilesCapability, PhpCapability, PhpSettings, SslCapability,
                           SslStatus)
from .demo_databases import DemoDatabases, browse as browse_demo_table, tables as demo_tables
from .manual import ManualAdapter
from .sandbox import Sandbox

PHP_VERSIONS = ["7.4", "8.0", "8.1", "8.2", "8.3"]

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


def sandbox_for(username):
    """The account's folder, created with a few sample files the first time it is used."""
    root = files_root() / username
    fresh = not root.exists()
    box = Sandbox(root)
    if fresh:
        for rel, text in SAMPLE_FILES.items():
            target = root / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(text, encoding="utf-8")
    return box


class DemoAdapter(DatabasesCapability, FilesCapability, SslCapability, PhpCapability, ManualAdapter):
    def get_usage(self, username):
        seed = _seed(username)
        disk_limit = 20480
        bandwidth_limit = 102400
        return {"disk_used_mb": (seed * 37) % disk_limit, "disk_limit_mb": disk_limit,
                "bandwidth_used_mb": (seed * 91) % bandwidth_limit, "bandwidth_limit_mb": bandwidth_limit}

    # --- Files (real, sandboxed) ---------------------------------------------------------------------------------

    def list_files(self, username, path):
        return sandbox_for(username).list_files(path)

    def read_file(self, username, path, *, max_bytes):
        return sandbox_for(username).read_file(path, max_bytes=max_bytes)

    def write_file(self, username, path, content, *, overwrite=False):
        sandbox_for(username).write_file(path, content, overwrite=overwrite)

    def make_directory(self, username, path):
        sandbox_for(username).make_directory(path)

    def rename_path(self, username, path, new_path):
        sandbox_for(username).rename_path(path, new_path)

    def copy_path(self, username, path, new_path):
        sandbox_for(username).copy_path(path, new_path)

    def delete_path(self, username, path):
        sandbox_for(username).delete_path(path)

    def search_files(self, username, path, query):
        return sandbox_for(username).search_files(path, query)

    def archive_paths(self, username, paths, archive_path):
        return sandbox_for(username).archive_paths(paths, archive_path)

    def extract_archive(self, username, path, destination):
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
        self._require_database(username, database)
        return DatabaseAdminAccess(kind="embedded")  # no phpMyAdmin behind a demo account: WHPanel's own browser serves it

    def list_tables(self, username, database):
        self._require_database(username, database)
        return demo_tables(database)

    def browse_table(self, username, database, table, offset, limit):
        self._require_database(username, database)
        return browse_demo_table(database, table, offset, limit)

    def _require_database(self, username, database):
        if database not in [d.name for d in databases_for(username).databases()]:
            raise HostingError("That database does not exist.")

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

    # --- SSL ---------------------------------------------------------------------------------------------------

    def get_ssl_status(self, username, domain):
        active = _seed(username) % 5 != 0  # about one account in five shows "not active"
        return SslStatus(domain=domain, active=active, issuer="Sample data" if active else "",
                         expires_at="2027-01-15T00:00:00Z" if active else None, auto_renew=active,
                         https_redirect=active)

    def request_ssl(self, username, domain):
        pass

    def set_https_redirect(self, username, domain, enabled):
        pass

    # --- PHP ---------------------------------------------------------------------------------------------------

    def get_php_settings(self, username, domain):
        version = PHP_VERSIONS[_seed(username) % len(PHP_VERSIONS)]
        return PhpSettings(version=version, memory_limit="256M", upload_max_filesize="64M", max_execution_time=60,
                           display_errors=False, available_versions=PHP_VERSIONS)

    def set_php_settings(self, username, domain, settings):
        pass
