"""
Installing WordPress on a node (the "Create website" step): everything a person would do by hand, done by the agent, so the customer only names the site and its administrator.

What it does, in order, and what it refuses:
1. The account's website must exist and its folder must hold nothing but the placeholder page the node put there (WordPress never lands on top of someone's files).
2. A database and a database user of its own (``<account>_wp``) are made through the same handlers the Databases screen uses, so the plan's limits and the name prefix rule apply. The
   database password is made here, written only into ``wp-config.php`` (mode 0640, the account's own group) and never sent back to WHPanel.
3. WordPress is downloaded over HTTPS from wordpress.org and **checked against the checksum wordpress.org publishes** before a single file is unpacked. The archive is unpacked with the same
   safe reader as the cPanel importer (no absolute paths, no links, nothing outside the folder).
4. ``wp-config.php`` is written with fresh random keys.
5. WP-CLI (downloaded once and checked the same way) completes the installation as the account's own user inside its slice, with the administrator's password sent to it on standard input,
   never on a command line.
6. If anything fails after the database was made, the database, the user and the files this run put there are removed again, so the account is left as it was.

Never run on a real node: the downloads, systemd-run and WP-CLI behave as documented but are untested here. Where the download addresses came from is stated in docs/p4-wordpress.md.
"""
import hashlib
import os
import re
import secrets
import shutil
import urllib.request
from pathlib import Path

from .. import catalog
from . import render
from .cpanel_import import Archive
from .system import SystemError_
from .website import HandlerError

SCHEME = "https"
WP_ARCHIVE = SCHEME + "://wordpress.org/latest.tar.gz"
WP_CHECKSUM = WP_ARCHIVE + ".sha1"
CLI_PHAR = SCHEME + "://raw.githubusercontent.com/wp-cli/builds/gh-pages/phar/wp-cli.phar"
CLI_CHECKSUM = CLI_PHAR + ".sha512"
TOOLS_DIR = "/var/lib/whp/tools"
MAX_DOWNLOAD = 120 << 20
PLACEHOLDER = "index.html"
RUN_LIMIT = 900
PHP_BIN = "/opt/remi/php{nodot}/root/usr/bin/php"

CONFIG = """<?php
// managed by WH Agent when WordPress was installed. Keep this file private.
define( 'DB_NAME', '{db}' );
define( 'DB_USER', '{user}' );
define( 'DB_PASSWORD', '{password}' );
define( 'DB_HOST', 'localhost' );
define( 'DB_CHARSET', 'utf8mb4' );
define( 'DB_COLLATE', '' );

{salts}

$table_prefix = 'wp_';
define( 'WP_DEBUG', false );
define( 'FS_METHOD', 'direct' );

if ( ! defined( 'ABSPATH' ) ) {{
	define( 'ABSPATH', __DIR__ . '/' );
}}
require_once ABSPATH . 'wp-settings.php';
"""
SALT_NAMES = ("AUTH_KEY", "SECURE_AUTH_KEY", "LOGGED_IN_KEY", "NONCE_KEY", "AUTH_SALT", "SECURE_AUTH_SALT", "LOGGED_IN_SALT", "NONCE_SALT")
ALPHABET = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"


def real_token(length):
    return "".join(secrets.choice(ALPHABET) for _ in range(length))


def real_fetch(url):
    """One HTTPS download, size-limited. Only the fixed addresses above are ever asked for."""
    request = urllib.request.Request(url, headers={"User-Agent": "wh-agent"})
    with urllib.request.urlopen(request, timeout=120) as reply:  # noqa: S310 - fixed https addresses
        data = reply.read(MAX_DOWNLOAD + 1)
    if len(data) > MAX_DOWNLOAD:
        raise SystemError_("The download is larger than expected.")
    return data


def _first_word(text):
    return (text or "").strip().split()[0].lower() if (text or "").strip() else ""


class WordPress:
    def __init__(self, system, websites, databases, root_for, stage_root, fetch=real_fetch, token=real_token, tools_dir=TOOLS_DIR):
        self.s, self.web, self.db, self.root_for, self.stage_root, self.fetch, self.token = system, websites, databases, root_for, Path(stage_root), fetch, token
        self.tools_dir = Path(tools_dir)

    def run(self, command, params):
        try:
            p = catalog.validate(command, params)
            if command != "INSTALL_WORDPRESS":
                raise HandlerError("not_implemented", f"{command} is not implemented on this node yet.")
            return self.install(p)
        except catalog.InvalidCommand as exc:
            raise HandlerError("invalid", str(exc)) from None
        except SystemError_ as exc:
            raise HandlerError("system_error", str(exc)[:300]) from None

    # --- helpers -------------------------------------------------------------------------------------------------------------------------
    def _verified(self, url, checksum_url, algorithm):
        try:
            data = self.fetch(url)
            wanted = _first_word(self.fetch(checksum_url).decode("utf-8", "replace"))
        except (OSError, SystemError_):
            raise HandlerError("download_failed", "Could not download WordPress. Try again in a few minutes.") from None
        if not wanted or hashlib.new(algorithm, data).hexdigest() != wanted:
            raise HandlerError("checksum_mismatch", "A download did not match its published checksum, so nothing was installed.")
        return data

    def _cli(self):
        path = self.tools_dir / "wp-cli.phar"
        if not path.exists():
            data = self._verified(CLI_PHAR, CLI_CHECKSUM, "sha512")
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
            os.chmod(path, 0o755)
        return str(path)

    def _free_name(self, account, existing):
        for suffix in ("wp", "wp2", "wp3", "wp4", "wp5"):
            if f"{account}_{suffix}" not in existing:
                return f"{account}_{suffix}"
        raise HandlerError("limit_reached", "There are already several WordPress databases on this account.")

    # --- the installation ------------------------------------------------------------------------------------------------------------------
    def install(self, p):
        account, domain = p["account"], p["domain"]
        state = self.web._need(account, domain)
        if state.get("suspended"):
            raise HandlerError("suspended", "This website is suspended.")
        root = self.root_for(account)
        if not root:
            raise HandlerError("not_found", "No such website on this node.")
        docroot = Path(root) / domain / "public_html"
        if (docroot / "wp-config.php").exists():
            return "exists", {"admin_url": self._admin_url(state, domain), "version": self._version(docroot)}
        others = [e.name for e in docroot.iterdir() if e.name != PLACEHOLDER] if docroot.is_dir() else []
        if others:
            raise HandlerError("not_empty", "The website folder already holds files, so WordPress was not installed over them.")

        # download and check first: nothing is changed on the machine until both downloads are known to be good
        archive_bytes = self._verified(WP_ARCHIVE, WP_CHECKSUM, "sha1")
        cli = self._cli()

        existing = set(self.db.db.databases(account + "_"))
        name = self._free_name(account, existing)
        password = self.token(24)
        made_db = made_user = False
        stage = self.stage_root / f"wp-{self.token(8)}"
        try:
            stage.mkdir(parents=True)
            self.db.run("CREATE_DATABASE", {"account": account, "name": name})
            made_db = True
            self.db.run("CREATE_DB_USER", {"account": account, "name": name, "password": password})
            made_user = True
            self.db.run("GRANT_DB_ACCESS", {"account": account, "database": name, "user": name, "privileges": list(catalog.DB_PRIVILEGES)})

            tar_path = stage / "wordpress.tar.gz"
            tar_path.write_bytes(archive_bytes)
            archive = Archive(tar_path)
            (docroot / PLACEHOLDER).unlink(missing_ok=True)
            archive.extract("", docroot, False)

            salts = "\n".join(f"define( '{key}', '{self.token(64)}' );" for key in SALT_NAMES)
            config = docroot / "wp-config.php"
            config.write_bytes(CONFIG.format(db=name, user=name, password=password, salts=salts).encode("utf-8"))  # bytes: the same file on every platform
            site = Path(root) / domain
            self.s.run(["chown", "-R", f"{account}:apache", str(site)])
            self.s.run(["chmod", "-R", "u+rwX,g+rX,o-rwx", str(site)])
            self.s.run(["chmod", "0640", str(config)])

            scheme = "https" if state.get("ssl") else "http"
            argv = ["systemd-run", "--quiet", "--collect", "--wait", "--pipe", "--uid", account, "--gid", account, "--slice", f"whp-{account}.slice",
                    "--property=NoNewPrivileges=yes", "--property=PrivateTmp=yes", f"--property=BindPaths=/home/{account}", f"--property=RuntimeMaxSec={RUN_LIMIT}",
                    PHP_BIN.format(nodot=render.nodot(state["php_version"])), cli, "core", "install", f"--path={docroot}", f"--url={scheme}://{domain}",
                    f"--title={p['site_title']}", f"--admin_user={p['admin_user']}", f"--admin_email={p['admin_email']}", "--prompt=admin_password", "--skip-email"]
            self.s.run(argv, input=p["admin_password"] + "\n")
        except Exception:
            self._undo(account, name, made_db, made_user, docroot)
            raise
        finally:
            shutil.rmtree(stage, ignore_errors=True)
        return "installed", {"admin_url": self._admin_url(state, domain), "version": self._version(docroot), "database": name}

    def _undo(self, account, name, made_db, made_user, docroot):
        """Put the account back as it was: the database and user this run made, everything it put in the folder (which held only the placeholder when it started), and the placeholder."""
        for step in (lambda: made_user and self.db.run("DELETE_DB_USER", {"account": account, "name": name}),
                     lambda: made_db and self.db.run("DELETE_DATABASE", {"account": account, "name": name})):
            try:
                step()
            except Exception:  # noqa: BLE001 - a failing undo must not hide the first failure
                pass
        try:
            for entry in list(docroot.iterdir()):
                shutil.rmtree(entry) if entry.is_dir() and not entry.is_symlink() else entry.unlink()
        except OSError:
            pass
        try:
            (docroot / PLACEHOLDER).write_bytes(b"<!doctype html><title>Welcome</title>")
        except OSError:
            pass

    @staticmethod
    def _admin_url(state, domain):
        return f"{'https' if state.get('ssl') else 'http'}://{domain}/wp-admin/"

    @staticmethod
    def _version(docroot):
        try:
            text = (docroot / "wp-includes" / "version.php").read_text(encoding="utf-8", errors="replace")
        except OSError:
            return ""
        found = re.search(r"\$wp_version\s*=\s*'([^']+)'", text)
        return found.group(1) if found else ""
