"""
The provider-neutral hosting API (roadmap section 03): ``/api/v1/hosting/services/{id}/<resource>/``. Nothing here names cPanel or any other
provider; every call goes through the same hosting-control gateway as the screens, so permissions, the live-account check, the
allowlist, plan limits, error mapping and the audit trail are identical. Responses never contain passwords, keys or tokens.

Staff (not contacts of the client) call it the way they use the screens: with their own sign-in, a role that allows it, and a written
reason in the ``X-Admin-Access-Reason`` header, which is recorded with every change they make.
"""
from dataclasses import asdict, is_dataclass

from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.exceptions import ServiceError

from . import admin_access, control, services
from .adapters.capabilities import DnsRecord

REASON_HEADER = "X-Admin-Access-Reason"


def plain(value):
    """Dataclasses, lists and tuples as plain JSON values."""
    if is_dataclass(value) and not isinstance(value, type):
        return {k: plain(v) for k, v in asdict(value).items()}
    if isinstance(value, (list, tuple)):
        return [plain(v) for v in value]
    if isinstance(value, dict):
        return {k: plain(v) for k, v in value.items()}
    return value


class ServiceResource(APIView):
    """Base: finds the service the caller may see, asks for a reason when staff look at a customer's service, and runs gateway calls."""

    permission_classes = [IsAuthenticated]

    def setup_service(self, request, pk):
        account = get_object_or_404(services.visible_hosting_accounts_for_user(request.user), pk=pk)
        self.reason = ""
        if admin_access.is_admin_actor(request.user, account):
            self.reason = (request.headers.get(REASON_HEADER) or "").strip()
            if not admin_access.MIN_REASON <= len(self.reason) <= admin_access.MAX_REASON:
                raise ServiceError(f"Staff must give a reason in the {REASON_HEADER} header ({admin_access.MIN_REASON} to {admin_access.MAX_REASON} "
                                   "characters), for example a ticket number.", code="admin_reason_required", status_code=status.HTTP_403_FORBIDDEN)
        return account

    def run(self, request, account, capability, operation, *args, meta=None, **kwargs):
        extra = {**({"reason": self.reason} if self.reason else {}), **(meta or {})}
        return control.perform(request.user, account, capability, operation, *args, audit_metadata=extra, request=request, **kwargs)

    def get(self, request, pk, *rest, **kw):
        account = self.setup_service(request, pk)
        return Response(plain(self.read(request, account, *rest, **kw)))

    def read(self, request, account, *rest, **kw):  # pragma: no cover - overridden
        raise ServiceError("Not available.", code="not_available", status_code=status.HTTP_405_METHOD_NOT_ALLOWED)


def created(data=None):
    return Response(plain(data) if data is not None else None, status=status.HTTP_201_CREATED)


def gone():
    return Response(status=status.HTTP_204_NO_CONTENT)


def need(request, *keys):
    missing = [k for k in keys if request.data.get(k) in (None, "")]
    if missing:
        raise ServiceError("Missing: " + ", ".join(missing) + ".", code="missing_field", status_code=status.HTTP_400_BAD_REQUEST)
    return [request.data.get(k) for k in keys]


# --- Files -----------------------------------------------------------------------------------------------------------------

class FilesView(ServiceResource):
    def read(self, request, account):
        return self.run(request, account, "files", "list_files", request.query_params.get("path", ""))

    def post(self, request, pk):
        account = self.setup_service(request, pk)
        (path,) = need(request, "path")
        self.run(request, account, "files", "make_directory", path, meta={"path": path})
        return created({"path": path})

    def delete(self, request, pk):
        account = self.setup_service(request, pk)
        path = request.query_params.get("path", "")
        self.run(request, account, "files", "trash_path", path, meta={"path": path})
        return gone()


# --- Databases -------------------------------------------------------------------------------------------------------------

class DatabasesView(ServiceResource):
    def read(self, request, account):
        return {"limits": self.run(request, account, "databases", "database_limits"),
                "databases": self.run(request, account, "databases", "list_databases"),
                "users": self.run(request, account, "databases", "list_database_users")}

    def post(self, request, pk):
        account = self.setup_service(request, pk)
        (name,) = need(request, "name")
        self.run(request, account, "databases", "create_database", name, meta={"database": name})
        return created({"name": name})


class DatabaseView(ServiceResource):
    def delete(self, request, pk, name):
        account = self.setup_service(request, pk)
        self.run(request, account, "databases", "delete_database", name, meta={"database": name})
        return gone()


# --- Email -----------------------------------------------------------------------------------------------------------------

class EmailsView(ServiceResource):
    def read(self, request, account):
        return {"limits": self.run(request, account, "email", "email_limits"), "mailboxes": self.run(request, account, "email", "list_mailboxes"),
                "forwarders": self.run(request, account, "email", "list_forwarders")}

    def post(self, request, pk):
        account = self.setup_service(request, pk)
        address, password = need(request, "address", "password")
        quota = request.data.get("quota_mb")
        self.run(request, account, "email", "create_mailbox", address, password, quota_mb=int(quota) if quota else None, meta={"address": address})
        return created({"address": address})


class EmailView(ServiceResource):
    def patch(self, request, pk, address):
        account = self.setup_service(request, pk)
        if "password" in request.data:
            self.run(request, account, "email", "set_mailbox_password", address, request.data["password"], meta={"address": address})
        if "quota_mb" in request.data:
            quota = request.data["quota_mb"]
            self.run(request, account, "email", "set_mailbox_quota", address, int(quota) if quota else None, meta={"address": address})
        return Response({"address": address})

    def delete(self, request, pk, address):
        account = self.setup_service(request, pk)
        self.run(request, account, "email", "delete_mailbox", address, meta={"address": address})
        return gone()


# --- Domains and DNS -------------------------------------------------------------------------------------------------------

class DomainsView(ServiceResource):
    def read(self, request, account):
        return {"limits": self.run(request, account, "domains", "domain_limits"), "domains": self.run(request, account, "domains", "list_domains")}

    def post(self, request, pk):
        account = self.setup_service(request, pk)
        name, kind = need(request, "name", "kind")
        self.run(request, account, "domains", "add_domain", name, kind, request.data.get("document_root", ""), meta={"domain": name, "kind": kind})
        return created({"name": name})


class DomainView(ServiceResource):
    def delete(self, request, pk, name):
        account = self.setup_service(request, pk)
        self.run(request, account, "domains", "remove_domain", name, meta={"domain": name})
        return gone()

    def patch(self, request, pk, name):
        account = self.setup_service(request, pk)
        target = request.data.get("redirect_to", "")
        self.run(request, account, "domains", "set_redirect", name, target, request.data.get("permanent", True) not in (False, "false", "0", 0),
                 meta={"domain": name})
        return Response({"name": name, "redirect_to": target})


def _record(request, record_id=""):
    data = request.data
    priority = data.get("priority")
    return DnsRecord(record_id=record_id, type=str(data.get("type", "")), name=str(data.get("name", "")), value=str(data.get("value", "")),
                     ttl=data.get("ttl") or 14400, priority=int(priority) if priority not in (None, "") else None)


class DnsView(ServiceResource):
    def read(self, request, account):
        domain = request.query_params.get("domain") or account.domain
        return self.run(request, account, "dns", "list_dns_records", domain)

    def post(self, request, pk):
        account = self.setup_service(request, pk)
        domain = request.data.get("domain") or account.domain
        record = _record(request)
        record_id = self.run(request, account, "dns", "add_dns_record", domain, record,
                             meta={"domain": domain, "type": record.type.upper(), "name": record.name or "@"})
        return created({"record_id": record_id})


class DnsRecordView(ServiceResource):
    def put(self, request, pk, record_id):
        account = self.setup_service(request, pk)
        domain = request.data.get("domain") or account.domain
        record = _record(request, record_id)
        self.run(request, account, "dns", "update_dns_record", domain, record, meta={"domain": domain, "type": record.type.upper(), "name": record.name or "@"})
        return Response({"record_id": record_id})

    def delete(self, request, pk, record_id):
        account = self.setup_service(request, pk)
        domain = request.query_params.get("domain") or account.domain
        self.run(request, account, "dns", "delete_dns_record", domain, record_id, meta={"domain": domain, "record": record_id})
        return gone()


# --- SSL, PHP --------------------------------------------------------------------------------------------------------------

class SslView(ServiceResource):
    def read(self, request, account):
        return self.run(request, account, "ssl", "list_ssl_status")

    def post(self, request, pk):
        account = self.setup_service(request, pk)
        (domain,) = need(request, "domain")
        self.run(request, account, "ssl", "request_ssl", domain, meta={"domain": domain})
        return created({"domain": domain})


class SslDomainView(ServiceResource):
    def read(self, request, account, domain):
        return self.run(request, account, "ssl", "get_ssl_status", domain)

    def patch(self, request, pk, domain):
        account = self.setup_service(request, pk)
        if "auto_renew" in request.data:
            self.run(request, account, "ssl", "set_ssl_auto_renew", domain, bool(request.data["auto_renew"]), meta={"domain": domain})
        if "https_redirect" in request.data:
            self.run(request, account, "ssl", "set_https_redirect", domain, bool(request.data["https_redirect"]), meta={"domain": domain})
        return Response({"domain": domain})

    def delete(self, request, pk, domain):
        account = self.setup_service(request, pk)
        self.run(request, account, "ssl", "remove_ssl", domain, meta={"domain": domain})
        return gone()


class PhpView(ServiceResource):
    def read(self, request, account):
        domain = request.query_params.get("domain") or account.domain
        return {"options": self.run(request, account, "php", "php_options"), "settings": self.run(request, account, "php", "get_php_settings", domain),
                "domain": domain}

    def patch(self, request, pk):
        account = self.setup_service(request, pk)
        domain = request.data.get("domain") or account.domain
        changes = {k: v for k, v in request.data.items() if k != "domain"}
        self.run(request, account, "php", "set_php_settings", domain, changes, meta={"domain": domain})
        return Response({"domain": domain})


# --- Cron, backups, usage --------------------------------------------------------------------------------------------------

class CronView(ServiceResource):
    def read(self, request, account):
        return {"options": self.run(request, account, "cron", "cron_options"), "jobs": self.run(request, account, "cron", "list_cron_jobs")}

    def post(self, request, pk):
        account = self.setup_service(request, pk)
        schedule, command = need(request, "schedule", "command")
        job_id = self.run(request, account, "cron", "create_cron_job", schedule, command,
                          meta={"script": command.split(" ")[1] if len(command.split(" ")) > 1 else ""})
        return created({"job_id": job_id})


class CronJobView(ServiceResource):
    def patch(self, request, pk, job_id):
        account = self.setup_service(request, pk)
        changes = {k: request.data[k] for k in ("schedule", "command", "enabled") if k in request.data}
        self.run(request, account, "cron", "update_cron_job", job_id, meta={"job": job_id}, **changes)
        return Response({"job_id": job_id})

    def delete(self, request, pk, job_id):
        account = self.setup_service(request, pk)
        self.run(request, account, "cron", "delete_cron_job", job_id, meta={"job": job_id})
        return gone()


class CronRunView(ServiceResource):
    def post(self, request, pk, job_id):
        account = self.setup_service(request, pk)
        return Response(plain(self.run(request, account, "cron", "run_cron_job", job_id, meta={"job": job_id})))


class BackupsView(ServiceResource):
    def read(self, request, account):
        return {"options": self.run(request, account, "backups", "backup_options"), "backups": self.run(request, account, "backups", "list_backups")}

    def post(self, request, pk):
        account = self.setup_service(request, pk)
        (kind,) = need(request, "kind")
        backup_id = self.run(request, account, "backups", "create_backup", kind, meta={"kind": kind})
        return created({"backup_id": backup_id})


class BackupView(ServiceResource):
    def delete(self, request, pk, backup_id):
        account = self.setup_service(request, pk)
        self.run(request, account, "backups", "delete_backup", backup_id, meta={"backup": backup_id})
        return gone()


class BackupActionView(ServiceResource):
    """``verify`` (the test restore) and ``restore`` of one backup."""

    def post(self, request, pk, backup_id, action):
        account = self.setup_service(request, pk)
        if action == "verify":
            return Response(plain(self.run(request, account, "backups", "verify_backup", backup_id, meta={"backup": backup_id})))
        if action == "restore":
            self.run(request, account, "backups", "restore_backup", backup_id, meta={"backup": backup_id})
            return Response({"backup_id": backup_id, "restored": True})
        raise ServiceError("That action is not available.", code="unknown_action", status_code=status.HTTP_404_NOT_FOUND)


class UsageView(ServiceResource):
    def read(self, request, account):
        usage = self.run(request, account, "usage", "get_usage")
        try:
            days = int(request.query_params.get("days", 0))
        except ValueError:
            days = 0
        history = None
        if days:
            try:
                history = self.run(request, account, "insights", "usage_history", days)
            except ServiceError:
                history = None  # a provider without history simply has none to give
        return {"usage": usage, **({"history": history} if history is not None else {})}
