"""
Staging for a WordPress website: a private copy on its own subdomain (``staging.<domain>``) with its own folder and database, to try changes on and then publish.

The copy itself is done by one PHP file written into the live website's folder and called once (``wp_clone``), the same pattern as the installer and the scanner, so it works on any
server that gives WHPanel file, database and domain control. Creating a staging site never touches the live one. Publishing replaces the live files and tables with the staging
copy, so it first takes a backup where the server can make one and otherwise needs the customer to say they have their own.
"""
import json
import posixpath
import secrets
from datetime import timedelta

from django.utils import timezone

from apps.audit import services as audit
from apps.core.exceptions import ServiceError

from . import care, site_apps, wp_clone
from .adapters.capabilities import Capability
from .models import AppState, StagingSite, WebsiteApp

TIMEOUT = timedelta(minutes=12)
PRIVILEGES = ["select", "insert", "update", "delete", "create", "alter", "drop", "index"]


def current(account):
    return StagingSite.objects.filter(account=account).first()


def staging_domain(account):
    return "staging." + account.domain.lower()


def unavailable_reason(account):
    """Why this website cannot have a staging copy, or "" when it can."""
    app = WebsiteApp.objects.filter(account=account).first()
    if app is None or app.kind != "wordpress" or app.state != AppState.READY:
        return "Staging is for WordPress websites that are installed and ready."
    try:
        from . import control

        have = set(control.capabilities_for(account))
    except ServiceError:
        have = set()
    needed = {Capability.FILES.value, Capability.DATABASES.value, Capability.DOMAINS.value}
    if not needed <= have:
        return "This server cannot make a staging copy yet."
    return ""


def _scheme(user, account):
    return "https" if site_apps._ssl_active(user, account) else "http"


def _url(scheme, host):
    return scheme + "://" + host


def _cleanup(io, account, staging, folder_made):
    """Put the account back as it was after a failed or removed staging site: the files, the database and its user, the subdomain. Each step is best effort."""
    steps = []
    if staging.script_ref:
        steps.append(lambda: io.call(Capability.FILES, "delete_path", f"{site_apps.docroot_path(account)}/{staging.script_ref}"))
    if staging.result_ref:
        steps.append(lambda: io.call(Capability.FILES, "delete_path", f"{site_apps.docroot_path(account)}/{staging.result_ref}"))
    if staging.made_domain:
        steps.append(lambda: io.call(Capability.DOMAINS, "remove_domain", staging.domain))
    if folder_made and staging.docroot:
        steps.append(lambda: io.call(Capability.FILES, "delete_path", staging.docroot))
    if staging.db_user:
        steps.append(lambda: io.call(Capability.DATABASES, "delete_database_user", staging.db_user))
    if staging.db_name:
        steps.append(lambda: io.call(Capability.DATABASES, "delete_database", staging.db_name))
    for step in steps:
        try:
            step()
        except ServiceError:
            pass


def create(user, account, request=None):
    reason = unavailable_reason(account)
    if reason:
        raise ServiceError(reason, code="not_available")
    existing = current(account)
    if existing is not None and existing.state != "failed":
        raise ServiceError("This website already has a staging copy.", code="exists")
    if existing is not None:
        existing.delete()
    io = care._Io(user, account)
    live = site_apps.docroot_path(account)
    sub = staging_domain(account)
    if sub in {d.name.lower() for d in io.call(Capability.DOMAINS, "list_domains")}:
        raise ServiceError(f"{sub} already exists on this hosting account, so a staging site was not created.", code="domain_exists")
    staging = StagingSite.objects.create(account=account, domain=sub, state="creating")
    root = "public_html/staging"
    try:
        io.call(Capability.DOMAINS, "add_domain", sub, "subdomain", root, audit_metadata={"domain": sub, "purpose": "staging"})
        staging.made_domain = True
        for info in io.call(Capability.DOMAINS, "list_domains"):
            if info.name.lower() == sub and info.document_root:
                root = info.document_root.strip("/") or root
        staging.docroot = root
        staging.save()
        dbs_before = {d.name for d in io.call(Capability.DATABASES, "list_databases")}
        users_before = {u.name for u in io.call(Capability.DATABASES, "list_database_users")}
        suffix = next((s for s in ("stg", "stg2", "stg3") if not any(n.endswith("_" + s) or n == s for n in dbs_before | users_before)), None)
        if suffix is None:
            raise ServiceError("There are already several staging databases on this account.", code="limit_reached")
        password = site_apps.database_password()
        io.call(Capability.DATABASES, "create_database", suffix)
        staging.db_name = suffix
        staging.save()
        io.call(Capability.DATABASES, "create_database_user", suffix, password)
        staging.db_user = suffix
        staging.save()
        db_name = next((d.name for d in io.call(Capability.DATABASES, "list_databases") if d.name not in dbs_before), "")
        db_user = next((u.name for u in io.call(Capability.DATABASES, "list_database_users") if u.name not in users_before), "")
        if not db_name or not db_user:
            raise ServiceError("The server did not report the new database, so the staging website was not created.", code="database_missing")
        staging.db_name, staging.db_user = db_name, db_user
        staging.save()
        io.call(Capability.DATABASES, "grant_database_access", db_name, db_user, PRIVILEGES)
        scheme = _scheme(user, account)
        file_name, result_name, token, source = wp_clone.build(
            action="create", src=".", dst=posixpath.relpath(root, live), from_url=_url(scheme, account.domain.lower()), to_url=_url(scheme, sub),
            exclude=[posixpath.basename(root)] if root.startswith(live + "/") else [], dst_db={"name": db_name, "user": db_user, "password": password})
        path = f"{live}/{file_name}"
        io.call(Capability.FILES, "write_file", path, source.encode(), overwrite=False, audit_metadata={"path": path, "purpose": "one-time staging copy"})
        staging.script_ref, staging.result_ref = file_name, result_name
        staging.save()
        audit.record("hosting.staging_started", actor=user, target=account, metadata={"domain": sub}, request=request)
        outcome = site_apps.reach(account, file_name, token, purpose="to create the staging website")
    except ServiceError as exc:
        _cleanup(io, account, staging, folder_made=True)
        staging.delete()
        raise exc
    if outcome is not None:
        _apply(user, account, staging, outcome)
    return staging


def _apply(user, account, staging, data):
    io = care._Io(user, account)
    live = site_apps.docroot_path(account)
    was_push = staging.state == "pushing"
    if data.get("ok"):
        staging.state, staging.error = "ready", ""
        if was_push:
            staging.pushed_at = timezone.now()
        audit.record("hosting.staging_pushed" if was_push else "hosting.staging_ready", target=account, metadata={"domain": staging.domain, "rows": data.get("rows")})
    elif was_push:
        staging.state, staging.error = "ready", "Publishing did not finish: " + str(data.get("error") or "unknown error")[:300]
        audit.record("hosting.staging_push_failed", target=account, metadata={"error": staging.error})
    else:
        staging.error = str(data.get("error") or "The staging website could not be created.")[:300]
        audit.record("hosting.staging_failed", target=account, metadata={"error": staging.error})
        staging.state = "failed"
    for leftover in (staging.result_ref, staging.script_ref):  # the script removes itself when it runs; this is for the one that never did
        if leftover:
            try:
                io.call(Capability.FILES, "delete_path", f"{live}/{leftover}")
            except ServiceError:
                pass
    staging.result_ref = staging.script_ref = ""
    if staging.state == "failed":
        _cleanup(io, account, staging, folder_made=True)
        staging.made_domain, staging.db_name, staging.db_user, staging.script_ref = False, "", "", ""
    staging.save()


def poll(user, account):
    staging = current(account)
    if staging is None or staging.state not in ("creating", "pushing") or not staging.result_ref:
        return staging
    io = care._Io(user, account)
    live = site_apps.docroot_path(account)
    try:
        raw = io.call(Capability.FILES, "read_file", f"{live}/{staging.result_ref}", max_bytes=20_000)
        data = json.loads(raw.decode("utf-8", "replace") if isinstance(raw, (bytes, bytearray)) else str(raw))
    except (ServiceError, ValueError):
        if timezone.now() - staging.updated_at > TIMEOUT:
            _apply(user, account, staging, {"ok": False, "error": "The copy did not report back in twelve minutes."})
        return staging
    _apply(user, account, staging, data if isinstance(data, dict) else {"ok": False})
    return staging


def backup_before_push(user, account):
    """Take a backup of the live website before it is replaced. Returns "made", or "unsupported" when the server cannot make one; raises when it could not."""
    io = care._Io(user, account)
    if not io.supports(Capability.BACKUPS):
        return "unsupported"
    options = io.call(Capability.BACKUPS, "backup_options")
    kind = options.kinds[0][0] if options.kinds else "full"
    io.call(Capability.BACKUPS, "create_backup", kind, audit_metadata={"kind": kind, "purpose": "before publishing staging"})
    return "made"


def push(user, account, acknowledged=False, request=None):
    staging = current(account)
    if staging is None or staging.state != "ready":
        raise ServiceError("There is no staging website ready to publish.", code="not_ready")
    try:
        outcome = backup_before_push(user, account)
    except ServiceError as exc:
        raise ServiceError("The safety backup could not be made, so nothing was published: " + exc.message, code="backup_failed") from exc
    if outcome == "unsupported" and not acknowledged:
        raise ServiceError("This server cannot make a backup for you. Confirm that you have your own backup to publish.", code="needs_acknowledgement")
    io = care._Io(user, account)
    live = site_apps.docroot_path(account)
    scheme = _scheme(user, account)
    file_name, result_name, token, source = wp_clone.build(
        action="push", src=posixpath.relpath(staging.docroot, live), dst=".", from_url=_url(scheme, staging.domain), to_url=_url(scheme, account.domain.lower()),
        exclude=[posixpath.basename(staging.docroot)] if staging.docroot.startswith(live + "/") else [])
    path = f"{live}/{file_name}"
    io.call(Capability.FILES, "write_file", path, source.encode(), overwrite=False, audit_metadata={"path": path, "purpose": "one-time publish from staging"})
    staging.state, staging.script_ref, staging.result_ref, staging.error = "pushing", file_name, result_name, ""
    staging.save()
    audit.record("hosting.staging_push_started", actor=user, target=account, metadata={"domain": staging.domain, "backup": outcome}, request=request)
    try:
        answer = site_apps.reach(account, file_name, token, purpose="to publish the staging website")
    except ServiceError as exc:
        try:
            io.call(Capability.FILES, "delete_path", path)
        except ServiceError:
            pass
        staging.state, staging.error = "ready", exc.message
        staging.save()
        raise
    if answer is not None:
        _apply(user, account, staging, answer)
    return staging


def delete(user, account, request=None):
    staging = current(account)
    if staging is None:
        return
    io = care._Io(user, account)
    _cleanup(io, account, staging, folder_made=True)
    audit.record("hosting.staging_deleted", actor=user, target=account, metadata={"domain": staging.domain}, request=request)
    staging.delete()
