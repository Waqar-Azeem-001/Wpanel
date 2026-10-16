"""
Copy Website: make a new WordPress website on another domain of the plan from one that is already there (its files and its database, with the addresses changed).

The copy is done by the same one-file tool as staging (``wp_clone``), written into the new website's folder and called once. The new website then looks like any other
being set up: the progress page, then ready. Nothing is changed on the website that is copied from.
"""
import posixpath

from apps.audit import services as audit
from apps.core.exceptions import ServiceError

from . import care, plan_sites, site_apps, staging, wp_clone
from .adapters.capabilities import Capability
from .models import AppState, WebsiteApp


def _own(account, domain):
    return staging._own(account, domain)


def unavailable_reason(account, domain=""):
    """Why this website cannot be copied, or "" when it can."""
    app = WebsiteApp.objects.filter(account=account, domain=_own(account, domain)).first()
    if app is None or app.kind != "wordpress" or app.state != AppState.READY:
        return "Only a WordPress website that is installed and ready can be copied."
    try:
        from . import control

        have = set(control.capabilities_for(account))
    except ServiceError:
        have = set()
    if not {Capability.FILES.value, Capability.DATABASES.value} <= have:
        return "This server cannot copy a website yet."
    return ""


def targets(user, account, domain=""):
    """The domains of the plan that can receive a copy: added domains that have no website yet."""
    source = site_apps.site_domain(account, domain)
    return [s["domain"] for s in plan_sites.summary(user, account)["sites"] if not s["primary"] and s["app"] is None and s["domain"] != source]


def copy(user, account, target, request=None, domain=""):
    """Start the copy. Returns the new website (installing, or already ready when the tool answered within the wait)."""
    domain = _own(account, domain)
    reason = unavailable_reason(account, domain)
    if reason:
        raise ServiceError(reason, code="not_available")
    target = (target or "").strip().lower()
    if target not in targets(user, account, domain):
        raise ServiceError("Choose a domain of this plan that has no website yet.", code="bad_target")
    source_app = WebsiteApp.objects.get(account=account, domain=domain)
    from_folder, to_folder = site_apps.docroot_path(account, domain), site_apps.docroot_path(account, target)

    from . import wp_installer

    keep = set(wp_installer.HARMLESS) | set(wp_installer.PLACEHOLDERS)
    names = {e.name for e in site_apps._perform(user, account, Capability.FILES, "list_files", to_folder)}
    found = sorted(n for n in names - keep if not n.startswith((".cpanel", "whp-")))
    if found:
        raise ServiceError("The folder of " + target + " already holds files (" + ", ".join(found[:6]) + "), so nothing was copied over them.", code="not_empty")

    dbs_before = {d.name for d in site_apps._perform(user, account, Capability.DATABASES, "list_databases")}
    users_before = {u.name for u in site_apps._perform(user, account, Capability.DATABASES, "list_database_users")}
    suffix = next((s for s in ("cp", "cp2", "cp3", "cp4", "cp5") if not any(n.endswith("_" + s) or n == s for n in dbs_before | users_before)), None)
    if suffix is None:
        raise ServiceError("There are already several copied databases on this account.", code="limit_reached")

    password = site_apps.database_password()
    app = WebsiteApp.objects.create(account=account, domain=target, kind="wordpress", state=AppState.INSTALLING, title=(source_app.title + " copy")[:100], admin_user=source_app.admin_user,
                                    admin_email=source_app.admin_email, progress_step="copy")
    made_db = made_user = False
    script_name = ""
    try:
        site_apps._perform(user, account, Capability.DATABASES, "create_database", suffix)
        made_db = True
        site_apps._perform(user, account, Capability.DATABASES, "create_database_user", suffix, password)
        made_user = True
        db_name = (site_apps._new_names(dbs_before, site_apps._perform(user, account, Capability.DATABASES, "list_databases"), lambda d: d.name) or [""])[0]
        db_user = (site_apps._new_names(users_before, site_apps._perform(user, account, Capability.DATABASES, "list_database_users"), lambda u: u.name) or [""])[0]
        if not db_name or not db_user:
            raise ServiceError("The server did not report the new database, so nothing was copied.", code="database_missing")
        site_apps._perform(user, account, Capability.DATABASES, "grant_database_access", db_name, db_user, site_apps.PRIVILEGES)
        scheme_from, scheme_to = staging._scheme(user, account, domain), staging._scheme(user, account, target)
        file_name, result_name, token, source = wp_clone.build(
            action="create", src=posixpath.relpath(from_folder, to_folder), dst=".", from_url=staging._url(scheme_from, site_apps.site_domain(account, domain)),
            to_url=staging._url(scheme_to, target), exclude=staging._others(account, user, from_folder, to_folder, domain),
            dst_db={"name": db_name, "user": db_user, "password": password}, index=True)
        script_name = file_name
        path = f"{to_folder}/{file_name}"
        site_apps._perform(user, account, Capability.FILES, "write_file", path, source.encode(), overwrite=False, audit_metadata={"path": path, "purpose": "one-time website copy"})
        app.job_ref, app.installer_ref, app.db_name, app.db_user = result_name, file_name, db_name, db_user
        app.save(update_fields=["job_ref", "installer_ref", "db_name", "db_user", "updated_at"])
        audit.record("hosting.site_copy_started", actor=user, target=account, metadata={"from": site_apps.site_domain(account, domain), "to": target}, request=request)
        outcome = site_apps.reach(account, file_name, token, purpose="to copy the website", domain=target)
    except Exception:
        site_apps._cleanup(user, account, to_folder, script_name, app.db_name or "", app.db_user or "", made_db, made_user, suffix)
        app.delete()
        raise
    if outcome is not None:
        site_apps._apply(user, account, app, outcome)
    return app
