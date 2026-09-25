"""
Client business logic. Staff API, customer API, staff web pages and customer
web pages all call these functions.
"""
from django.conf import settings
from django.contrib.auth.tokens import default_token_generator
from django.db import transaction
from django.db.models import Q
from django.urls import reverse
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_encode
from rest_framework import status

from apps.accounts.models import User
from apps.accounts.roles import Role, perm
from apps.audit import services as audit
from apps.audit.models import AuditEvent
from apps.core.exceptions import ServiceError
from apps.notifications import services as notifications

from .models import Client, ClientContact, ClientStatus, ContactRole

# Fields staff may set on a client.
STAFF_FIELDS = (
    "company_name", "first_name", "last_name", "email", "phone", "address_line1", "address_line2", "city",
    "state", "postcode", "country", "tax_id", "currency", "tax_exempt", "notes",
)
# Fields a client's own owner/billing contact may change.
CUSTOMER_FIELDS = (
    "company_name", "first_name", "last_name", "email", "phone", "address_line1", "address_line2", "city",
    "state", "postcode", "country", "tax_id",
)
# Contact roles allowed to edit the client's details from the customer side.
CUSTOMER_EDIT_ROLES = {ContactRole.OWNER, ContactRole.BILLING}


def _denied(message="You do not have permission to perform this action."):
    return ServiceError(message, code="permission_denied", status_code=status.HTTP_403_FORBIDDEN)


def _require(actor, codename):
    if not actor.has_perm(perm(codename)):
        raise _denied()


def _normalise(data):
    data = dict(data)
    for key in ("country", "currency"):
        if data.get(key):
            data[key] = data[key].strip().upper()
    if data.get("email"):
        data["email"] = data["email"].strip().lower()
    return data


# --- Queries ---------------------------------------------------------------------

def clients_for_user(user):
    """Clients the user may see: all for staff with view_clients, otherwise their own."""
    if user.has_perm(perm("view_clients")):
        return Client.objects.all()
    return Client.objects.filter(contacts__user=user)


def search_clients(queryset, term):
    term = (term or "").strip()
    if not term:
        return queryset
    q = (Q(company_name__icontains=term) | Q(first_name__icontains=term) | Q(last_name__icontains=term)
         | Q(email__icontains=term) | Q(phone__icontains=term) | Q(contacts__user__email__icontains=term))
    if term.upper().startswith("C") and term[1:].isdigit():
        q |= Q(pk=int(term[1:]))
    elif term.isdigit():
        q |= Q(pk=int(term))
    return queryset.filter(q).distinct()


def contact_role(user, client):
    contact = ClientContact.objects.filter(client=client, user=user).only("role").first()
    return contact.role if contact else None


def single_contact_client(user):
    """
    The one client ``user`` is a contact of, or None if they have zero or more
    than one. Used where a self-service action needs "the caller's account"
    without asking them to pick from a list (e.g. requesting a new domain or
    hosting account) - see apps.domains.views and apps.hosting.views.
    """
    clients = list(Client.objects.filter(contacts__user=user)[:2])
    return clients[0] if len(clients) == 1 else None


def client_activity(client):
    """Audit events about the client or recorded in its context."""
    return AuditEvent.objects.filter(
        Q(target_type="clients.client", target_id=str(client.pk)) | Q(metadata__client_id=client.pk)
    )


# --- Create / update -------------------------------------------------------------------

def _send_set_password_email(user, client):
    uid = urlsafe_base64_encode(force_bytes(user.pk))
    token = default_token_generator.make_token(user)
    link = settings.SITE_URL.rstrip("/") + reverse("accounts:password_reset_confirm", args=[uid, token])
    notifications.dispatch("client.welcome", user=user, context={"client": client, "link": link})


def _get_or_create_contact_user(email, first_name="", last_name=""):
    """Existing customer user for the email, or a new one with no usable password yet."""
    email = email.strip().lower()
    user = User.objects.filter(email__iexact=email).first()
    if user is not None:
        if user.role != Role.CUSTOMER:
            raise ServiceError("Staff accounts cannot be client contacts.", code="staff_contact")
        return user, False
    user = User.objects.create_user(email=email, password=None, first_name=first_name, last_name=last_name)
    from apps.accounts.services import sync_role_membership

    sync_role_membership(user)
    return user, True


@transaction.atomic
def create_client(actor, data, *, owner_email=None, request=None):
    """
    Staff: create a client. The owner contact is ``owner_email`` (defaults to the
    client email); a new user is created and emailed a set-password link if needed.
    """
    _require(actor, "manage_clients")
    data = _normalise({k: v for k, v in data.items() if k in STAFF_FIELDS})
    client = Client(**data)
    client.full_clean()
    client.save()

    owner, created = _get_or_create_contact_user(
        owner_email or client.email, client.first_name, client.last_name
    )
    ClientContact.objects.create(client=client, user=owner, role=ContactRole.OWNER)
    if created:
        _send_set_password_email(owner, client)
    audit.record("client.created", actor=actor, target=client,
                 metadata={"client_id": client.pk, "owner": owner.email, "owner_created": created}, request=request)
    return client


def create_client_for_registration(user, *, company_name="", request=None):
    """Customer self-registration: a new client owned by the registering user."""
    client = Client.objects.create(
        company_name=company_name, first_name=user.first_name or user.email.split("@")[0],
        last_name=user.last_name, email=user.email, phone=user.phone,
    )
    ClientContact.objects.create(client=client, user=user, role=ContactRole.OWNER)
    audit.record("client.created", actor=user, target=client,
                 metadata={"client_id": client.pk, "source": "registration"}, request=request)
    return client


def _apply_changes(client, data, allowed):
    changed = {}
    for field in allowed:
        if field in data and getattr(client, field) != data[field]:
            setattr(client, field, data[field])
            changed[field] = data[field]
    return changed


@transaction.atomic
def update_client(actor, client, data, *, request=None):
    """Update client details. Staff need manage_clients; customers must be owner/billing contacts."""
    if actor.has_perm(perm("manage_clients")):
        allowed = STAFF_FIELDS
    elif contact_role(actor, client) in CUSTOMER_EDIT_ROLES:
        if client.status == ClientStatus.CLOSED:
            raise _denied("This account is closed.")
        allowed = CUSTOMER_FIELDS
    else:
        raise _denied()

    data = _normalise(data)
    before = {f: getattr(client, f) for f in allowed}
    changed = _apply_changes(client, data, allowed)
    if not changed:
        return client
    try:
        client.full_clean()
    except Exception:
        for field, value in before.items():
            setattr(client, field, value)
        raise
    client.save()
    audit.record("client.updated", actor=actor, target=client,
                 metadata={"client_id": client.pk, "fields": sorted(changed)}, request=request)
    return client


@transaction.atomic
def set_client_status(actor, client, new_status, *, reason="", request=None):
    _require(actor, "manage_clients")
    if new_status not in ClientStatus.values:
        raise ServiceError("Unknown client status.", code="invalid_status")
    previous = client.status
    if previous == new_status:
        return client
    client.status = new_status
    client.save(update_fields=["status", "updated_at"])
    audit.record("client.status_changed", actor=actor, target=client,
                 metadata={"client_id": client.pk, "from": previous, "to": new_status, "reason": reason[:500]},
                 request=request)
    return client


# --- Contacts ------------------------------------------------------------------------------

@transaction.atomic
def add_contact(actor, client, *, email, role=ContactRole.TECHNICAL, first_name="", last_name="", request=None):
    _require(actor, "manage_clients")
    if role not in ContactRole.values:
        raise ServiceError("Unknown contact role.", code="invalid_role")
    user, created = _get_or_create_contact_user(email, first_name, last_name)
    if ClientContact.objects.filter(client=client, user=user).exists():
        raise ServiceError("This user is already a contact of the client.", code="duplicate_contact")
    contact = ClientContact.objects.create(client=client, user=user, role=role)
    if created:
        _send_set_password_email(user, client)
    audit.record("client.contact_added", actor=actor, target=client,
                 metadata={"client_id": client.pk, "user": user.email, "role": role, "user_created": created},
                 request=request)
    return contact


def _owner_count(client):
    return ClientContact.objects.filter(client=client, role=ContactRole.OWNER).count()


@transaction.atomic
def change_contact_role(actor, contact, role, *, request=None):
    _require(actor, "manage_clients")
    if role not in ContactRole.values:
        raise ServiceError("Unknown contact role.", code="invalid_role")
    contact = ClientContact.objects.select_for_update().get(pk=contact.pk)
    if contact.role == role:
        return contact
    if contact.role == ContactRole.OWNER and _owner_count(contact.client) == 1:
        raise ServiceError("A client must keep at least one owner.", code="last_owner")
    previous = contact.role
    contact.role = role
    contact.save(update_fields=["role", "updated_at"])
    audit.record("client.contact_role_changed", actor=actor, target=contact.client,
                 metadata={"client_id": contact.client_id, "user": contact.user.email, "from": previous, "to": role},
                 request=request)
    return contact


@transaction.atomic
def remove_contact(actor, contact, *, request=None):
    _require(actor, "manage_clients")
    contact = ClientContact.objects.select_for_update().get(pk=contact.pk)
    if contact.role == ContactRole.OWNER and _owner_count(contact.client) == 1:
        raise ServiceError("A client must keep at least one owner.", code="last_owner")
    client, email = contact.client, contact.user.email
    contact.delete()
    audit.record("client.contact_removed", actor=actor, target=client,
                 metadata={"client_id": client.pk, "user": email}, request=request)
