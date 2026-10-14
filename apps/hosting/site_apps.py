"""
The "Create website" step of a hosting plan: what kind of website this is (WordPress or a custom one), and the work that follows.

WordPress is installed with everything automatic; the customer only gives the site title and the administrator's name, email and password. Two ways, chosen by what the account's server can do:

* **A server whose agent can do it** (native nodes): one signed command; the node makes the database and user, downloads and checks WordPress and installs it. WHPanel is told when it ends.
* **A server that cannot** (cPanel/WHM): WHPanel makes the database and user through the same hosting-control layer the Databases screen uses, writes ONE small installer file into the
  website's folder (``wp_installer``), calls it once, and reads the result file it leaves. The server needs PHP with curl or allow_url_fopen and zip or phar, which every cPanel server has.

The administrator's password goes to the provider and is not stored: not in WHPanel's database, not in the audit trail, not in the job record (it is hidden there as soon as the job is stored).
"""
import ipaddress
import json
import secrets
import socket
from datetime import timedelta

import requests
from django.utils import timezone

from apps.audit import services as audit
from apps.core.exceptions import ServiceError

from . import control, wp_installer
from .adapters.capabilities import Capability
from .models import AppState, HostingStatus, WebsiteApp

KINDS = ("wordpress", "custom")
TIMEOUT = timedelta(minutes=10)  # an installation that has not reported back by then is called failed
PRIVILEGES = ["select", "insert", "update", "delete", "create", "alter", "drop", "index"]
STARTER = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>{domain}</title>
<style>body{{font:16px/1.6 system-ui,sans-serif;margin:0;min-height:100vh;display:grid;place-items:center;background:#f5f5f7;color:#1d1d1f}}main{{text-align:center;padding:2rem}}h1{{font-weight:600;margin:0 0 .5rem}}p{{color:#6e6e73;margin:0}}</style>
</head><body><main><h1>{domain}</h1><p>This website is ready. Its owner has not added any content yet.</p></main></body></html>
"""


# --- what this account's server can do ---------------------------------------------------------------------------------------------------------

def _capabilities(account):
    try:
        return set(control.capabilities_for(account))
    except ServiceError:
        return set()


def installs_by_agent(account):
    return Capability.APPS.value in _capabilities(account)


def installs_by_script(account):
    have = _capabilities(account)
    return Capability.APPS.value not in have and {Capability.FILES.value, Capability.DATABASES.value} <= have


def can_install_wordpress(account):
    """Whether this account's server can install WordPress for the customer (a server that cannot simply does not offer the choice)."""
    return installs_by_agent(account) or installs_by_script(account)


def docroot_path(account):
    """The website's folder as the files capability names it."""
    server = account.server
    return f"{account.domain.lower()}/public_html" if server is not None and server.kind == "native" else "public_html"


def current(account):
    return WebsiteApp.objects.filter(account=account).first()


def database_password():
    """A database password every provider accepts: long, with letters and digits, no symbols or spaces (a random token alone sometimes has no digit)."""
    alphabet = "abcdefghijkmnpqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    while True:
        value = "".join(secrets.choice(alphabet) for _ in range(24))
        if any(c.isdigit() for c in value) and any(c.islower() for c in value) and any(c.isupper() for c in value):
            return value


def needs_setup(account):
    """A live website that has not been set up yet: the overview and the list invite the customer to do it."""
    return account.status == HostingStatus.ACTIVE and current(account) is None


def _require_open(account):
    if account.status != HostingStatus.ACTIVE:
        raise ServiceError("This website is not active yet, so it cannot be set up.", code="not_active")
    existing = current(account)
    if existing is not None and existing.state != AppState.FAILED:
        raise ServiceError("This website has already been set up.", code="already_set_up")
    return existing


# --- custom ------------------------------------------------------------------------------------------------------------------------------------

def start_custom(user, account, *, request=None):
    existing = _require_open(account)
    app = existing or WebsiteApp(account=account)
    app.kind, app.state, app.error = "custom", AppState.READY, ""
    app.save()
    audit.record("hosting.site_set_up", actor=user, target=account, metadata={"kind": "custom"}, request=request)
    _starter_page(user, account)
    return app


def _starter_page(user, account):
    """A page that says the site is ready, so the first visit shows something. Never replaces anything and never stops the set-up if it cannot be written."""
    if Capability.FILES.value not in _capabilities(account):
        return
    folder = docroot_path(account)
    try:
        names = {entry.name.lower() for entry in control.perform(user, account, Capability.FILES, "list_files", folder)}
        if any(n.startswith("index.") for n in names):
            return
        control.perform(user, account, Capability.FILES, "write_file", f"{folder}/index.html", STARTER.format(domain=account.domain).encode(), overwrite=False,
                        audit_metadata={"path": f"{folder}/index.html", "purpose": "starter page"})
    except Exception:  # noqa: BLE001 - a nicety, never a reason to fail the set-up
        return


# --- WordPress ---------------------------------------------------------------------------------------------------------------------------------

def start_wordpress(user, account, *, title, admin_user, admin_email, admin_password, request=None):
    existing = _require_open(account)
    if not can_install_wordpress(account):
        raise ServiceError("WordPress cannot be installed on this server yet. Choose a custom website and upload your own files.", code="not_supported")
    app = existing or WebsiteApp(account=account)
    app.kind, app.state, app.error = "wordpress", AppState.INSTALLING, ""
    app.title, app.admin_user, app.admin_email, app.admin_url, app.version = title, admin_user, admin_email, "", ""
    app.job_ref = app.installer_ref = app.db_name = app.db_user = ""
    app.save()
    audit.record("hosting.site_set_up", actor=user, target=account, metadata={"kind": "wordpress", "title": title, "admin_user": admin_user}, request=request)
    try:
        if installs_by_agent(account):
            _start_by_agent(user, account, app, title, admin_user, admin_email, admin_password, request)
        else:
            _start_by_script(user, account, app, title, admin_user, admin_email, admin_password, request)
    except ServiceError as exc:
        app.state, app.error = AppState.FAILED, exc.message[:500]
        app.save(update_fields=["state", "error", "updated_at"])
        raise
    return app


def _start_by_agent(user, account, app, title, admin_user, admin_email, admin_password, request):
    result = control.perform(user, account, Capability.APPS, "install_wordpress", account.domain, title, admin_user, admin_password, admin_email,
                             audit_metadata={"app": "wordpress", "title": title, "admin_user": admin_user}, request=request)
    if result.get("state") == "ready":
        app.state, app.admin_url, app.version = AppState.READY, result.get("admin_url", ""), result.get("version", "")
        app.save(update_fields=["state", "admin_url", "version", "updated_at"])


def _perform(user, account, capability, operation, *args, **kwargs):
    return control.perform(user, account, capability, operation, *args, **kwargs)


def _new_names(before, after, getter):
    return [getter(i) for i in after if getter(i) not in before]


def _start_by_script(user, account, app, title, admin_user, admin_email, admin_password, request):
    """Make the database, put the installer in the folder, call it once. The result is read later (``poll``): installing can take a minute or more."""
    folder = docroot_path(account)
    keep = set(wp_installer.HARMLESS) | set(wp_installer.PLACEHOLDERS)
    names = {e.name for e in _perform(user, account, Capability.FILES, "list_files", folder)}
    found = sorted(n for n in names - keep if not n.startswith((".cpanel", "whp-install-")))
    if found:
        raise ServiceError("The website folder already holds files (" + ", ".join(found[:6]) + "), so WordPress was not installed over them. Move them away in the File manager, or choose a custom website.",
                           code="not_empty")

    dbs_before = {d.name for d in _perform(user, account, Capability.DATABASES, "list_databases")}
    users_before = {u.name for u in _perform(user, account, Capability.DATABASES, "list_database_users")}
    suffix = next((s for s in ("wp", "wp2", "wp3", "wp4", "wp5") if not any(n.endswith("_" + s) or n == s for n in dbs_before | users_before)), None)
    if suffix is None:
        raise ServiceError("There are already several WordPress databases on this account.", code="limit_reached")
    password = database_password()
    made_db = made_user = False
    installer_path = ""
    try:
        _perform(user, account, Capability.DATABASES, "create_database", suffix)
        made_db = True
        _perform(user, account, Capability.DATABASES, "create_database_user", suffix, password)
        made_user = True
        db_name = (_new_names(dbs_before, _perform(user, account, Capability.DATABASES, "list_databases"), lambda d: d.name) or [""])[0]
        db_user = (_new_names(users_before, _perform(user, account, Capability.DATABASES, "list_database_users"), lambda u: u.name) or [""])[0]
        if not db_name or not db_user:
            raise ServiceError("The server did not report the new database, so WordPress was not installed.", code="database_missing")
        _perform(user, account, Capability.DATABASES, "grant_database_access", db_name, db_user, PRIVILEGES)
        scheme = "https" if _ssl_active(user, account) else "http"
        file_name, result_name, token, source = wp_installer.build(domain=account.domain.lower(), scheme=scheme, db=db_name, db_user=db_user, db_password=password,
                                                                    title=title, admin_user=admin_user, admin_email=admin_email, admin_password=admin_password)
        installer_path = f"{folder}/{file_name}"
        _perform(user, account, Capability.FILES, "write_file", installer_path, source.encode(), overwrite=False,
                 audit_metadata={"path": installer_path, "purpose": "one-time WordPress installer"})
        app.job_ref, app.installer_ref, app.db_name, app.db_user = result_name, file_name, db_name, db_user
        app.save(update_fields=["job_ref", "installer_ref", "db_name", "db_user", "updated_at"])
        outcome = reach(account, file_name, token)
    except Exception:
        _cleanup(user, account, folder, installer_path.rsplit("/", 1)[-1] if installer_path else "", app.db_name or "", app.db_user or "", made_db, made_user, suffix)
        raise
    if outcome is not None:  # it finished within the wait
        _apply(user, account, app, outcome)


def _ssl_active(user, account):
    try:
        status = control.perform(user, account, Capability.SSL, "get_ssl_status", account.domain)
        return bool(getattr(status, "active", False))
    except Exception:  # noqa: BLE001
        return False


def _public(host):
    """Only addresses on the public internet: the panel never calls into a network of its own because a customer's domain says so."""
    try:
        infos = socket.getaddrinfo(host, None)
    except OSError:
        return False
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast or ip.is_unspecified:
            return False
    return bool(infos)


def reach(account, path, token, purpose="to finish the installation"):
    """
    Call the installer once. Tries the website's own address first, then its server's address with the right Host header (the domain may not point there yet).
    Returns the installer's answer as a dict if it came back within the wait, or None if it is still working (read the result file later).
    Raises ServiceError when no address reached the website at all.
    """
    domain = account.domain.lower()
    secure, plain = "https" + "://", "http" + "://"
    candidates = []
    if _public(domain):
        candidates += [(secure + domain, {}), (plain + domain, {})]
    ip = getattr(account.server, "ip_address", "") if account.server is not None else ""
    if ip:
        candidates.append((plain + str(ip), {"Host": domain}))
    last = "no address to try"
    for base, headers in candidates:
        try:
            reply = requests.get(f"{base}/{path}", params={"t": token}, headers=headers, timeout=(6, 25), allow_redirects=True)
        except requests.exceptions.ReadTimeout:
            return None  # it is running: the answer comes through the result file
        except requests.exceptions.RequestException as exc:
            last = type(exc).__name__
            continue
        if reply.status_code == 200:
            try:
                data = reply.json()
            except ValueError:
                last = "the address answered with something that is not the installer"
                continue
            return data if isinstance(data, dict) else None
        last = f"status {reply.status_code}"
    raise ServiceError(f"WHPanel could not reach your website {purpose} (" + last + "). Check that the domain points to your hosting, then try again.",
                       code="unreachable")


def _cleanup(user, account, folder, installer_name, db_name, db_user, made_db, made_user, suffix):
    """Put the account back as it was after a failed installation: the installer file, the database and the user. Each step is best effort."""
    steps = []
    if installer_name:
        steps.append(lambda: _perform(user, account, Capability.FILES, "delete_path", f"{folder}/{installer_name}"))
    if made_user:
        steps.append(lambda: _perform(user, account, Capability.DATABASES, "delete_database_user", db_user or suffix))
    if made_db:
        steps.append(lambda: _perform(user, account, Capability.DATABASES, "delete_database", db_name or suffix))
    for step in steps:
        try:
            step()
        except Exception:  # noqa: BLE001 - a failing clean-up must not hide the first failure
            pass


def _apply(user, account, app, data):
    """Take the installer's answer: ready, or failed with its reason (and everything it made removed)."""
    folder = docroot_path(account)
    if data.get("ok"):
        app.state, app.admin_url, app.version, app.error = AppState.READY, data.get("admin_url", ""), data.get("version", ""), ""
        audit.record("hosting.site_ready", target=account, metadata={"kind": "wordpress", "version": app.version})
    else:
        app.state, app.error = AppState.FAILED, str(data.get("error") or "WordPress could not be installed.")[:500]
        audit.record("hosting.site_failed", target=account, metadata={"kind": "wordpress", "error": app.error})
        _cleanup(user, account, folder, app.installer_ref, app.db_name, app.db_user, bool(app.db_name), bool(app.db_user), "")
    for leftover in (app.job_ref,):
        if leftover:
            try:
                _perform(user, account, Capability.FILES, "delete_path", f"{folder}/{leftover}")
            except Exception:  # noqa: BLE001
                pass
    app.save(update_fields=["state", "admin_url", "version", "error", "updated_at"])


def poll(user, account):
    """For an installation WHPanel started itself: has the installer left its result? Cheap (one file read); safe to call on every page view."""
    app = current(account)
    if app is None or app.state != AppState.INSTALLING or not app.job_ref:
        return app
    folder = docroot_path(account)
    try:
        raw = control.perform(user, account, Capability.FILES, "read_file", f"{folder}/{app.job_ref}", max_bytes=20000)
        data = json.loads(raw.decode("utf-8", "replace") if isinstance(raw, (bytes, bytearray)) else str(raw))
    except Exception:  # noqa: BLE001 - not there yet
        if timezone.now() - app.updated_at > TIMEOUT:
            _apply(user, account, app, {"ok": False, "error": "The installation did not report back in ten minutes. Nothing was left half-done; try again."})
        return app
    _apply(user, account, app, data if isinstance(data, dict) else {"ok": False})
    return app


def job_finished(job):
    """The node has finished (or failed) the installation WordPress was waiting for."""
    try:
        app = WebsiteApp.objects.select_related("account").get(account_id=int(job.subject.split(":", 1)[1]))
    except (ValueError, WebsiteApp.DoesNotExist):
        return
    if job.command != "INSTALL_WORDPRESS" or app.state != AppState.INSTALLING:
        return
    if job.result_ok is True:
        data = job.result_data or {}
        app.state, app.admin_url, app.version, app.error = AppState.READY, data.get("admin_url", ""), data.get("version", ""), ""
        audit.record("hosting.site_ready", target=app.account, metadata={"kind": "wordpress", "version": app.version, "job": job.pk})
    else:
        app.state, app.error = AppState.FAILED, (job.result_message or "The server could not install WordPress.")[:500]
        audit.record("hosting.site_failed", target=app.account, metadata={"kind": "wordpress", "error": app.error, "job": job.pk})
    app.save(update_fields=["state", "admin_url", "version", "error", "updated_at"])


# --- Deleting a website (the hosting plan stays) ------------------------------------------------------------------------------------------------

KEEP_ON_DELETE = {"cgi-bin", ".well-known"}


def folder_entries(user, account):
    """What is in the website's folder that deleting would remove (names only), or [] when it cannot be listed."""
    try:
        return sorted(e.name for e in control.perform(user, account, Capability.FILES, "list_files", docroot_path(account)) if e.name not in KEEP_ON_DELETE)
    except ServiceError:
        return []


def delete_website(user, account, confirm_domain, request=None):
    """
    Remove the website from its hosting plan: its files, the staging copy, and the database and user WHPanel itself made for it. The plan, the domain, email and every other
    database are left alone, and the plan goes back to "create your website". Returns a short report of what was removed and what was left.
    """
    from . import care, staging

    if (confirm_domain or "").strip().lower() != account.domain.lower():
        raise ServiceError("Type the website's address exactly to confirm.", code="confirm_mismatch")
    app = current(account)
    if app is None and not folder_entries(user, account):
        raise ServiceError("There is nothing to delete: the website folder is empty.", code="nothing")
    if app is not None and app.state == AppState.INSTALLING:
        raise ServiceError("This website is still being installed. Wait for it to finish, then delete it.", code="busy")
    report = {"files": 0, "staging": False, "database": False, "left": []}
    if staging.current(account) is not None:
        staging.delete(user, account, request=request)
        report["staging"] = True
    io = care._Io(user, account)
    folder = docroot_path(account)
    if Capability.FILES.value in _capabilities(account):
        for entry in io.call(Capability.FILES, "list_files", folder):
            if entry.name in KEEP_ON_DELETE:
                continue
            io.call(Capability.FILES, "delete_path", f"{folder}/{entry.name}", audit_metadata={"path": f"{folder}/{entry.name}", "purpose": "delete website"})
            report["files"] += 1
    if app is not None and app.db_user:
        try:
            io.call(Capability.DATABASES, "delete_database_user", app.db_user)
        except ServiceError:
            report["left"].append("the database user " + app.db_user)
    if app is not None and app.db_name:
        try:
            io.call(Capability.DATABASES, "delete_database", app.db_name)
            report["database"] = True
        except ServiceError:
            report["left"].append("the database " + app.db_name)
    elif app is not None and app.kind == "wordpress":
        report["left"].append("its database (remove it under MySQL databases if you no longer need it)")
    from .models import SiteCare, SiteScan, SpeedTest

    SiteScan.objects.filter(account=account).delete()
    SpeedTest.objects.filter(account=account).delete()
    SiteCare.objects.filter(account=account).delete()
    kind = app.kind if app is not None else "unmanaged"
    if app is not None:
        app.delete()
    audit.record("hosting.site_deleted", actor=user, target=account, metadata={"kind": kind, "files": report["files"], "database": report["database"]}, request=request)
    return report
