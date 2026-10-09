"""
Hosting business logic. Staff API, customer API, staff web pages and customer
web pages all call these functions; none of them talks to a hosting adapter
directly (see apps.hosting.adapters for why).

Reliability: provisioning is safe to retry - it checks the account's current
status before calling the adapter, so a repeated call never provisions twice.

A note on ``@transaction.atomic``: functions that call an adapter and, on
failure, must persist a FAILED status/last_error and an audit record before
re-raising are deliberately NOT wrapped in ``@transaction.atomic`` - Django
rolls back an entire atomic block when an exception escapes it, which would
silently discard exactly the failure record staff need to see and retry from.
This is the same bug (and fix) as apps.domains.services; see the Phase 04 gap
report for how it was found.
"""
import secrets
import string

from django.db import transaction
from django.db.models import ProtectedError
from django.db.models import Q
from django.utils import timezone
from rest_framework import status

from apps.accounts.roles import perm
from apps.audit import services as audit
from apps.clients.services import contact_role
from apps.core.exceptions import ServiceError
from apps.notifications import services as notifications
from apps.products.models import ServerStatus

from .adapters.base import HostingError
from .adapters.registry import build_adapter
from .models import HostingAccount, HostingStatus


def _denied(message="You do not have permission to perform this action."):
    return ServiceError(message, code="permission_denied", status_code=status.HTTP_403_FORBIDDEN)


def _require(actor, codename):
    if not actor.has_perm(perm(codename)):
        raise _denied()


def can_self_service(user, account):
    """Any contact of the account's client - mirrors apps.domains.services.can_self_service."""
    if user and user.is_authenticated and user.has_perm(perm("manage_hosting")):
        return True
    return bool(user and user.is_authenticated and contact_role(user, account.client) is not None)


def _announce(account, event, title, **context):
    """Tell the account's owner and billing contacts what happened to their hosting."""
    from django.urls import reverse

    link = reverse("hosting_customer:detail", args=[account.pk])
    notifications.dispatch_client(event, account.client, title=title, link=link,
                                  context={"account": account, "link": link, **context})


def get_adapter(server):
    return build_adapter(server)


# cPanel/WHM refuses these as account usernames ("... is a reserved username on this system"): anything beginning with test or one of
# cPanel's own prefixes, and the names of system and mail accounts.
RESERVED_PREFIXES = ("test", "cpanel", "cphulk", "cpses", "cptmp", "cpmove", "virtfs")
RESERVED_USERNAMES = frozenset("""root daemon bin sys sync games man lp mail news uucp proxy www wwwdata backup list irc gnats nobody mysql postgres named
dovecot dovenull exim mailman apache nginx ftp sshd ssh admin administrator operator system user users adm shell whm webmaster support info sales
postmaster abuse mailnull cpanel nagios munin redis memcached clamav tomcat git svn oracle""".split())


def is_reserved_username(username):
    username = (username or "").lower()
    return username in RESERVED_USERNAMES or username.startswith(RESERVED_PREFIXES)


USERNAME_LENGTH = 8  # a letter and seven letters or digits: valid on every cPanel version, even those that still limit names to eight characters
USERNAME_CHARS = string.ascii_lowercase + string.digits


def _generate_username(domain=None):
    """
    A random cPanel account username (``domain`` is accepted only so callers need not change). Random names cannot clash with an account
    that already exists on the server (a leftover from an earlier try, or one made by hand), never look like a reserved name, and give
    nothing away about the customer. Unique among every account WHPanel has ever recorded, not only the live ones.
    """
    while True:
        candidate = secrets.choice(string.ascii_lowercase) + "".join(secrets.choice(USERNAME_CHARS) for _ in range(USERNAME_LENGTH - 1))
        if not is_reserved_username(candidate) and not HostingAccount.objects.filter(username=candidate).exists():
            return candidate


def visible_hosting_accounts_for_user(user):
    queryset = HostingAccount.objects.select_related("client", "product", "server")
    if user and user.is_authenticated and user.has_perm(perm("view_hosting")):
        return queryset
    if user and user.is_authenticated:
        return queryset.filter(client__contacts__user=user).distinct()
    return queryset.none()


def search_hosting_accounts(queryset, term):
    term = (term or "").strip()
    if not term:
        return queryset
    return queryset.filter(Q(domain__icontains=term) | Q(username__icontains=term)
                           | Q(client__company_name__icontains=term) | Q(client__email__icontains=term))


# --- Request & provisioning ----------------------------------------------------------------

@transaction.atomic
def request_hosting(actor, client, product, domain, *, server=None, request=None):
    # Customers buy hosting through the cart; only staff (or the system, fulfilling a paid order) create accounts.
    _require(actor, "manage_hosting")
    if not product.whm_package_name:
        raise ServiceError("This product has no WHM package configured.", code="no_package_configured")

    mapped_servers = list(product.servers.all())
    if server is None and len(mapped_servers) == 1:
        server = mapped_servers[0]
    elif server is None and len(mapped_servers) > 1 and all(s.kind == "native" for s in mapped_servers):
        from apps.agent.placement import pick_node

        server = pick_node(among=mapped_servers)  # several native nodes are mapped: the least full healthy one gets the website
    elif server is not None and mapped_servers and server not in mapped_servers:
        raise ServiceError("This server is not mapped to the selected product.", code="server_not_mapped")

    account = HostingAccount(client=client, product=product, server=server, domain=domain,
                             username=_generate_username(domain), package_name=product.whm_package_name,
                             status=HostingStatus.PENDING)
    account.full_clean()
    account.save()
    audit.record("hosting.requested", actor=actor, target=account,
                 metadata={"client_id": client.pk, "product_id": product.pk}, request=request)
    return account


@transaction.atomic
def assign_server(actor, account, server, *, request=None):
    _require(actor, "manage_hosting")
    if account.status != HostingStatus.PENDING:
        raise ServiceError("A server can only be assigned before provisioning.", code="invalid_status")
    mapped_servers = list(account.product.servers.all())
    if mapped_servers and server not in mapped_servers:
        raise ServiceError("This server is not mapped to the product.", code="server_not_mapped")
    account.server = server
    account.save(update_fields=["server", "updated_at"])
    audit.record("hosting.server_assigned", actor=actor, target=account, metadata={"server": server.name},
                 request=request)
    return account


def complete_provisioning(actor, account, *, request=None):
    """Staff: provision the account against its assigned server. Idempotent."""
    _require(actor, "manage_hosting")
    if account.status == HostingStatus.ACTIVE:
        return account
    if account.status not in (HostingStatus.PENDING, HostingStatus.FAILED):
        raise ServiceError("This account is not pending provisioning.", code="invalid_status")
    if account.server is None:
        raise ServiceError("Assign a server before provisioning.", code="no_server_assigned")
    if account.server.status != ServerStatus.ACTIVE:
        raise ServiceError("The assigned server is not active.", code="server_not_active")

    if is_reserved_username(account.username):  # an older request (or a hand-typed name) WHM would refuse: pick a safe one and say so
        old = account.username
        account.username = _generate_username(account.domain)
        account.save(update_fields=["username", "updated_at"])
        audit.record("hosting.username_changed", actor=actor, target=account, metadata={"from": old, "to": account.username,
                                                                                      "reason": "reserved on the server"}, request=request)
    adapter = get_adapter(account.server)
    raw_password = secrets.token_urlsafe(16)
    try:
        result = adapter.create_account(username=account.username, domain=account.domain,
                                        package=account.package_name, contact_email=account.client.email,
                                        password=raw_password)
    except HostingError as exc:
        account.status, account.last_error = HostingStatus.FAILED, str(exc)
        account.save(update_fields=["status", "last_error", "updated_at"])
        audit.record("hosting.provisioning_failed", actor=actor, target=account, metadata={"error": str(exc)},
                     request=request)
        raise ServiceError(f"Provisioning failed: {exc}", code="adapter_error") from exc

    if result.get("pending"):  # a provider that works through a queue (the native one): the account becomes active when the node reports back
        account.last_error = ""
        account.save(update_fields=["last_error", "updated_at"])
        audit.record("hosting.provisioning_started", actor=actor, target=account, metadata={"provider_ref": result.get("provider_ref", "")}, request=request)
        return account

    account.status = HostingStatus.ACTIVE
    account.last_error = ""
    account.save(update_fields=["status", "last_error", "updated_at"])
    audit.record("hosting.provisioned", actor=actor, target=account,
                 metadata={"provider_ref": result.get("provider_ref", "")}, request=request)
    # The account's password lives only in WHM from here on and in this one-time
    # email - it is never written to our own database (see the module docstring
    # in apps.hosting.adapters.whm_api and roadmap section 23, "safe credentials").
    notifications.dispatch("hosting.welcome", email=account.client.email,
                           context={"account": account, "username": account.username, "password": raw_password})
    return account


@transaction.atomic
def cancel_request(actor, account, *, reason="", request=None):
    _require(actor, "manage_hosting")
    if account.status not in (HostingStatus.PENDING, HostingStatus.FAILED):
        raise ServiceError("Only a pending or failed request can be cancelled.", code="invalid_status")
    account.status = HostingStatus.CANCELLED
    account.save(update_fields=["status", "updated_at"])
    audit.record("hosting.cancelled", actor=actor, target=account, metadata={"reason": reason[:500]},
                 request=request)
    return account


# --- The control panel password ------------------------------------------------------------------------------------

PASSWORD_MIN, PASSWORD_MAX = 12, 64
PASSWORD_SYMBOLS = "!@#%^*-_=+"


def generate_password(length=20):
    """A strong random password with every kind of character (some servers refuse one that is not strong enough)."""
    pools = [string.ascii_lowercase, string.ascii_uppercase, string.digits, PASSWORD_SYMBOLS]
    chars = [secrets.choice(pool) for pool in pools] + [secrets.choice("".join(pools)) for _ in range(length - len(pools))]
    secrets.SystemRandom().shuffle(chars)
    return "".join(chars)


def check_password(password, username=""):
    if not PASSWORD_MIN <= len(password or "") <= PASSWORD_MAX:
        raise ServiceError(f"Use a password of {PASSWORD_MIN} to {PASSWORD_MAX} characters.", code="weak_password")
    if not (any(c.islower() for c in password) and any(c.isupper() for c in password) and any(c.isdigit() for c in password)):
        raise ServiceError("Mix upper and lower case letters and numbers.", code="weak_password")
    if username and username.lower() in password.lower():
        raise ServiceError("The password must not contain the username.", code="weak_password")
    return password


def change_account_password(actor, account, password=None, *, request=None):
    """
    Give the account a new control-panel password: a strong random one when ``password`` is None, otherwise the one the person chose (checked).
    Returns it so the caller can show it ONCE; it is sent to the server and nowhere else: never stored, logged, audited or emailed here.
    """
    if not can_self_service(actor, account):
        raise ServiceError("You do not have permission to perform this action.", code="permission_denied", status_code=403)
    if account.status not in (HostingStatus.ACTIVE, HostingStatus.SUSPENDED) or account.server is None:
        raise ServiceError("The password can be changed once the hosting account is active.", code="invalid_status")
    chosen = password is not None
    password = check_password(password, account.username) if chosen else generate_password()
    try:
        get_adapter(account.server).change_password(account.username, password)
    except HostingError as exc:
        audit.record("hosting.password_change_failed", actor=actor, target=account, metadata={"error": str(exc)[:200]}, request=request)
        raise ServiceError(f"The server could not change the password: {exc}", code="adapter_error") from exc
    audit.record("hosting.password_changed", actor=actor, target=account, metadata={"chosen": chosen, "username": account.username},
                 request=request)
    return password


# --- Removing a dead record ---------------------------------------------------------------------------------

DEAD_STATUSES = (HostingStatus.FAILED, HostingStatus.CANCELLED, HostingStatus.TERMINATED)


@transaction.atomic
def delete_dead_record(actor, account, *, request=None):
    """
    Remove the record of a hosting account that is already finished with: a failed provisioning, a cancelled request or a terminated
    account. Only WHPanel's own record goes; nothing is sent to the server, so nothing there is removed. An account that is pending,
    active or suspended is refused (terminate or cancel it first), and so is one that something else (an order, a renewal, a cancellation
    request) still points at, so history is never left dangling.
    """
    _require(actor, "manage_hosting")
    if account.status not in DEAD_STATUSES:
        raise ServiceError("Only a failed, cancelled or terminated account's record can be deleted. Cancel or terminate it first.", code="invalid_status")
    details = {"domain": account.domain, "username": account.username, "status": account.status, "client_id": account.client_id,
               "server": account.server.name if account.server else ""}
    try:
        account.delete()
    except ProtectedError as exc:
        raise ServiceError("This record is still linked to an order, renewal or cancellation, so it was kept.", code="record_in_use") from exc
    audit.record("hosting.record_deleted", actor=actor, target=None, metadata=details, request=request)


# --- Lifecycle: suspend, unsuspend, terminate, change package -------------------------------

def suspend_account(actor, account, *, reason="", for_nonpayment=False, request=None):
    _require(actor, "manage_hosting")
    if account.status != HostingStatus.ACTIVE:
        raise ServiceError("Only an active account can be suspended.", code="invalid_status")
    adapter = get_adapter(account.server)
    try:
        adapter.suspend_account(account.username, reason)
    except HostingError as exc:
        account.last_error = str(exc)
        account.save(update_fields=["last_error", "updated_at"])
        audit.record("hosting.suspend_failed", actor=actor, target=account, metadata={"error": str(exc)},
                     request=request)
        raise ServiceError(f"Suspend failed: {exc}", code="adapter_error") from exc

    account.status, account.suspend_reason, account.last_error = HostingStatus.SUSPENDED, reason, ""
    account.suspended_for_nonpayment = for_nonpayment
    account.save(update_fields=["status", "suspend_reason", "suspended_for_nonpayment", "last_error", "updated_at"])
    audit.record("hosting.suspended", actor=actor, target=account,
                 metadata={"reason": reason[:500], "for_nonpayment": for_nonpayment}, request=request)
    _announce(account, "hosting.suspended", f"{account.domain} has been suspended", reason=reason)
    return account


def unsuspend_account(actor, account, *, request=None):
    _require(actor, "manage_hosting")
    if account.status != HostingStatus.SUSPENDED:
        raise ServiceError("Only a suspended account can be unsuspended.", code="invalid_status")
    adapter = get_adapter(account.server)
    try:
        adapter.unsuspend_account(account.username)
    except HostingError as exc:
        account.last_error = str(exc)
        account.save(update_fields=["last_error", "updated_at"])
        audit.record("hosting.unsuspend_failed", actor=actor, target=account, metadata={"error": str(exc)},
                     request=request)
        raise ServiceError(f"Unsuspend failed: {exc}", code="adapter_error") from exc

    account.status, account.suspend_reason, account.last_error = HostingStatus.ACTIVE, "", ""
    account.suspended_for_nonpayment = False
    account.save(update_fields=["status", "suspend_reason", "suspended_for_nonpayment", "last_error", "updated_at"])
    audit.record("hosting.unsuspended", actor=actor, target=account, request=request)
    _announce(account, "hosting.unsuspended", f"{account.domain} is active again")
    return account


def backup_account(actor, account, *, request=None):
    """Request a backup/snapshot of the account's data from the server - a safety net, not a restore mechanism:
    this project has no backup storage or restore flow of its own, only the server's own reference. Staff may
    request one at any time; the lifecycle job also requests one automatically before an auto-termination goes
    ahead (``LifecycleSettings.backup_before_terminate``)."""
    _require(actor, "manage_hosting")
    if not account.server:
        raise ServiceError("This account has no server assigned.", code="no_server_assigned")
    adapter = get_adapter(account.server)
    try:
        result = adapter.backup_account(account.username)
    except HostingError as exc:
        raise ServiceError(f"Could not request a backup: {exc}", code="adapter_error") from exc
    account.backup_requested_at = timezone.now()
    account.backup_ref = result.get("backup_ref", "")
    account.save(update_fields=["backup_requested_at", "backup_ref", "updated_at"])
    audit.record("hosting.backup_requested", actor=actor, target=account,
                 metadata={"backup_ref": account.backup_ref}, request=request)
    return account


def terminate_account(actor, account, *, keep_dns=False, request=None):
    _require(actor, "manage_hosting")
    if account.status not in (HostingStatus.ACTIVE, HostingStatus.SUSPENDED):
        raise ServiceError("Only an active or suspended account can be terminated.", code="invalid_status")
    adapter = get_adapter(account.server)
    try:
        adapter.terminate_account(account.username, keep_dns=keep_dns)
    except HostingError as exc:
        account.last_error = str(exc)
        account.save(update_fields=["last_error", "updated_at"])
        audit.record("hosting.terminate_failed", actor=actor, target=account, metadata={"error": str(exc)},
                     request=request)
        raise ServiceError(f"Termination failed: {exc}", code="adapter_error") from exc

    account.status, account.last_error = HostingStatus.TERMINATED, ""
    account.save(update_fields=["status", "last_error", "updated_at"])
    audit.record("hosting.terminated", actor=actor, target=account, metadata={"keep_dns": keep_dns},
                 request=request)
    _announce(account, "hosting.terminated", f"{account.domain} has been terminated")
    return account


def change_package(actor, account, new_product, *, request=None):
    _require(actor, "manage_hosting")
    return _change_package(actor, account, new_product, request=request)


def change_package_as_system(account, new_product):
    """Change the package on behalf of the system (a paid upgrade): no user, audited with no actor."""
    return _change_package(None, account, new_product)


def _change_package(actor, account, new_product, *, request=None):
    if account.status != HostingStatus.ACTIVE:
        raise ServiceError("Only an active account can change package.", code="invalid_status")
    if not new_product.whm_package_name:
        raise ServiceError("The new product has no WHM package configured.", code="no_package_configured")
    adapter = get_adapter(account.server)
    try:
        adapter.change_package(account.username, new_product.whm_package_name)
    except HostingError as exc:
        account.last_error = str(exc)
        account.save(update_fields=["last_error", "updated_at"])
        audit.record("hosting.change_package_failed", actor=actor, target=account, metadata={"error": str(exc)},
                     request=request)
        raise ServiceError(f"Package change failed: {exc}", code="adapter_error") from exc

    previous = account.package_name
    account.product, account.package_name, account.last_error = new_product, new_product.whm_package_name, ""
    account.save(update_fields=["product", "package_name", "last_error", "updated_at"])
    audit.record("hosting.package_changed", actor=actor, target=account,
                 metadata={"from": previous, "to": account.package_name}, request=request)
    return account


# --- Sync (staff) --------------------------------------------------------------------------

def sync_status(actor, account, *, request=None):
    _require(actor, "manage_hosting")
    if not account.server:
        raise ServiceError("This account has no server assigned.", code="no_server_assigned")
    adapter = get_adapter(account.server)
    try:
        result = adapter.get_status(account.username)
    except HostingError as exc:
        raise ServiceError(f"Could not sync with the server: {exc}", code="adapter_error") from exc
    account.status = HostingStatus.SUSPENDED if result.get("suspended") else HostingStatus.ACTIVE
    account.last_synced_at = timezone.now()
    account.save(update_fields=["status", "last_synced_at", "updated_at"])
    audit.record("hosting.synced", actor=actor, target=account, request=request)
    return account


def _refresh_usage(account):
    """Fetch and store one account's disk and bandwidth figures. No permission check: callers decide who may do this."""
    adapter = get_adapter(account.server)
    try:
        usage = adapter.get_usage(account.username)
    except HostingError as exc:
        raise ServiceError(f"Could not fetch usage: {exc}", code="adapter_error") from exc
    account.disk_used_mb = usage.get("disk_used_mb")
    account.disk_limit_mb = usage.get("disk_limit_mb")
    account.bandwidth_used_mb = usage.get("bandwidth_used_mb")
    account.bandwidth_limit_mb = usage.get("bandwidth_limit_mb")
    account.last_synced_at = timezone.now()
    account.save(update_fields=["disk_used_mb", "disk_limit_mb", "bandwidth_used_mb", "bandwidth_limit_mb",
                                "last_synced_at", "updated_at"])
    return account


def sync_usage(actor, account, *, request=None):
    _require(actor, "manage_hosting")
    if not account.server:
        raise ServiceError("This account has no server assigned.", code="no_server_assigned")
    return _refresh_usage(account)


def check_server(actor, server, *, request=None):
    """
    "Test connection" for a server: reach it with the saved details (read-only), and compare the packages it has with the
    package name each product mapped to it creates. Returns ``{"version", "packages", "missing", "details"}`` (``details`` says what each package includes); ``missing`` lists the
    products whose package name the server does not have (an order for one of those would fail).
    """
    _require(actor, "manage_hosting")
    try:
        result = build_adapter(server).check_connection()
    except HostingError as exc:
        audit.record("server.connection_failed", actor=actor, target=server, metadata={"reason": str(exc)[:300]}, request=request)
        raise ServiceError(str(exc), code="connection_failed") from exc
    packages = result.get("packages") or []
    missing = []
    if packages:
        missing = [{"product": p.name, "package": p.whm_package_name} for p in server.products.all()
                   if p.whm_package_name and p.whm_package_name not in packages]
    audit.record("server.connection_tested", actor=actor, target=server,
                 metadata={"version": result.get("version", ""), "packages": len(packages), "missing": len(missing)},
                 request=request)
    return {"version": result.get("version", ""), "packages": packages, "missing": missing, "details": result.get("details") or {}}


def check_infrastructure(actor, server, *, request=None):
    """
    Sign in to the company that rents us the machine (Contabo) with the server's saved provider login and list the machines
    that login can see (read-only). Says which one has this server's IP address, or - when no IP is saved and there is only
    one machine - what IP to enter. The provider's secrets are never in the result, a message or an audit record.
    """
    from . import infra

    _require(actor, "manage_hosting")
    try:
        found = infra.instances_for(server)
    except infra.InfraError as exc:
        audit.record("server.infra_failed", actor=actor, target=server,
                     metadata={"provider": server.infra_provider, "reason": str(exc)[:300]}, request=request)
        raise ServiceError(str(exc), code="infra_failed") from exc
    ip = str(server.ip_address or "")
    match = next((i for i in found if ip and i["ip"] == ip), None)
    suggested = found[0]["ip"] if (not ip and len(found) == 1) else ""
    audit.record("server.infra_tested", actor=actor, target=server,
                 metadata={"provider": server.infra_provider, "machines": len(found), "matched": bool(match)}, request=request)
    return {"provider": server.get_infra_provider_display(), "instances": found, "match": match, "suggested_ip": suggested}


def sync_all_usage():
    """
    The scheduled job: refresh usage for every active account that has a server. One account failing (a server down,
    a username WHM no longer knows) is counted and skipped, never allowed to stop the rest.
    """
    done = failed = 0
    for account in HostingAccount.objects.filter(status=HostingStatus.ACTIVE, server__isnull=False
                                                 ).exclude(username="").select_related("server"):
        try:
            _refresh_usage(account)
            done += 1
        except ServiceError:
            failed += 1
    return {"synced": done, "failed": failed}
