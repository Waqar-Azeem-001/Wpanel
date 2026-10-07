"""
Admin Access: how a staff member manages a customer's hosting (Client -> Services -> Manage Hosting).

It is the customer's own hosting engine - same screens, same hosting-control gateway, same provider - reached with the staff member's
own sign-in, so no customer password is ever needed or shown. What makes it Admin Access and not a back door:

* it has to be started for one account, with a written reason, from the staff service page; the start is audited;
* it lasts an hour, shows a banner on every screen (who the customer is, which service, how long is left, a way back, a way to end it);
* the gateway still decides what the staff member's role may do (looking needs the view-hosting permission, changing needs
  manage-hosting, and reading a customer's private content - mail and database rows - needs manage-hosting and is audited);
* every change made during it is audited with ``admin_access`` set, so the customer's own activity list can say it was support.
"""
from datetime import timedelta

from django.utils import timezone
from django.utils.dateparse import parse_datetime

from apps.accounts.roles import perm
from apps.audit import services as audit
from apps.clients.services import contact_role

SESSION_KEY = "hosting_admin_access"
MINUTES = 60
MIN_REASON = 5
MAX_REASON = 200


class AdminAccessRequired(Exception):
    """A staff member opened a customer's hosting screen without an active Admin Access session for that account."""

    def __init__(self, account):
        super().__init__("Admin Access is required.")
        self.account = account


def is_admin_actor(user, account):
    """True when ``user`` is staff looking at an account of a client they are not themselves a contact of."""
    if not (user and user.is_authenticated) or not user.has_perm(perm("view_hosting")):
        return False
    return contact_role(user, account.client) is None


def minutes_left(request, account):
    grants = request.session.get(SESSION_KEY) or {}
    expires = parse_datetime(grants.get(str(account.pk), "") or "")
    if expires is None:
        return 0
    return max(0, int((expires - timezone.now()).total_seconds() // 60) + 1) if expires > timezone.now() else 0


def require(request, account):
    """Let a customer through untouched; let staff through only with an active grant for this account."""
    if is_admin_actor(request.user, account) and not minutes_left(request, account):
        raise AdminAccessRequired(account)


def start(request, account, reason):
    reason = (reason or "").strip()
    if len(reason) < MIN_REASON or len(reason) > MAX_REASON:
        raise ValueError(f"Write the reason for opening this account ({MIN_REASON} to {MAX_REASON} characters), for example a ticket number.")
    grants = dict(request.session.get(SESSION_KEY) or {})
    grants[str(account.pk)] = (timezone.now() + timedelta(minutes=MINUTES)).isoformat()
    request.session[SESSION_KEY] = grants
    audit.record("hosting.admin_access_started", actor=request.user, target=account,
                 metadata={"reason": reason, "minutes": MINUTES, "can_change": request.user.has_perm(perm("manage_hosting"))},
                 request=request)


def end(request, account):
    grants = dict(request.session.get(SESSION_KEY) or {})
    if grants.pop(str(account.pk), None) is not None:
        request.session[SESSION_KEY] = grants
        audit.record("hosting.admin_access_ended", actor=request.user, target=account, metadata={}, request=request)
