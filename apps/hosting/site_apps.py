"""
The "Create website" step of a hosting plan: what kind of website this is (WordPress or a custom one), and the work that follows.

WordPress is installed by the provider with everything automatic; the customer only gives the site title and the administrator's name, email and password. The password goes straight to the
provider and is not stored: not in WHPanel's database, not in the audit trail, not in the job record (it is hidden there as soon as the job is stored).
"""
from apps.audit import services as audit
from apps.core.exceptions import ServiceError

from . import control
from .adapters.capabilities import Capability
from .models import AppState, HostingStatus, WebsiteApp

KINDS = ("wordpress", "custom")


def can_install_wordpress(account):
    """Whether this account's server can install WordPress for the customer (a server that cannot simply does not offer the choice)."""
    try:
        return Capability.APPS.value in control.capabilities_for(account)
    except ServiceError:
        return False


def current(account):
    return WebsiteApp.objects.filter(account=account).first()


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


def start_custom(user, account, *, request=None):
    existing = _require_open(account)
    app = existing or WebsiteApp(account=account)
    app.kind, app.state, app.error = "custom", AppState.READY, ""
    app.save()
    audit.record("hosting.site_set_up", actor=user, target=account, metadata={"kind": "custom"}, request=request)
    return app


def start_wordpress(user, account, *, title, admin_user, admin_email, admin_password, request=None):
    existing = _require_open(account)
    if not can_install_wordpress(account):
        raise ServiceError("WordPress cannot be installed on this server yet. Choose a custom website and upload your own files.", code="not_supported")
    app = existing or WebsiteApp(account=account)
    app.kind, app.state, app.error = "wordpress", AppState.INSTALLING, ""
    app.title, app.admin_user, app.admin_email, app.admin_url, app.version = title, admin_user, admin_email, "", ""
    app.save()
    audit.record("hosting.site_set_up", actor=user, target=account, metadata={"kind": "wordpress", "title": title, "admin_user": admin_user}, request=request)
    try:
        result = control.perform(user, account, Capability.APPS, "install_wordpress", account.domain, title, admin_user, admin_password, admin_email,
                                 audit_metadata={"app": "wordpress", "title": title, "admin_user": admin_user}, request=request)
    except ServiceError as exc:
        app.state, app.error = AppState.FAILED, exc.message[:500]
        app.save(update_fields=["state", "error", "updated_at"])
        raise
    if result.get("state") == "ready":
        app.state, app.admin_url, app.version = AppState.READY, result.get("admin_url", ""), result.get("version", "")
        app.save(update_fields=["state", "admin_url", "version", "updated_at"])
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
