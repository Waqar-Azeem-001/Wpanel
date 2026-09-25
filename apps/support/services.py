"""
Support tickets: opening, replying, internal notes, assignment, priority, status, departments and predefined replies.

Every rule is enforced here (views, API and admin only call these functions):

* A customer sees and writes only their own clients' tickets, and **never sees an internal note** - not in a list,
  a detail, an email, a notification or an attachment download.
* Staff need ``view_support`` to read and ``manage_support`` to act.
* A ticket's related hosting account or domain must belong to the ticket's own client.
* Uploads are validated before anything is written (see ``uploads``); files are stored privately.
"""
import logging
from datetime import timedelta

from django.core.exceptions import ValidationError
from django.db import transaction
from django.urls import reverse
from django.utils import timezone
from rest_framework import status as http

from apps.accounts.models import User
from apps.accounts.roles import Role, perm
from apps.audit import services as audit
from apps.clients.models import ClientContact
from apps.clients.services import contact_role
from apps.core.exceptions import ServiceError
from apps.notifications import services as notifications

from . import lifecycle, uploads
from .models import (CannedReply, Department, MessageKind, Ticket, TicketAttachment, TicketMessage, TicketPriority,
                     TicketStatus)

logger = logging.getLogger(__name__)

MAX_SUBJECT = 200
MAX_BODY = 20000
TICKETS_PER_HOUR = 10
CLOSED_REPLY_WINDOW = timedelta(days=7)
AUTO_CLOSE_AFTER = timedelta(days=7)
CUSTOMER_PRIORITIES = (TicketPriority.LOW, TicketPriority.NORMAL, TicketPriority.HIGH)


def _denied(message="You do not have permission to perform this action."):
    return ServiceError(message, code="permission_denied", status_code=http.HTTP_403_FORBIDDEN)


def can_manage(user):
    return bool(user and user.is_authenticated and user.has_perm(perm("manage_support")))


def can_view_all(user):
    return bool(user and user.is_authenticated and user.has_perm(perm("view_support")))


def _require_manage(actor):
    if not can_manage(actor):
        raise _denied()


def is_client_contact(user, client):
    return bool(user and user.is_authenticated and contact_role(user, client) is not None)


# --- Visibility ----------------------------------------------------------------------------------------

def visible_tickets_for_user(user):
    """Staff with view_support see every ticket; a customer sees their clients' tickets."""
    queryset = Ticket.objects.select_related("client", "department", "assigned_to", "opened_by")
    if can_view_all(user):
        return queryset
    if user and user.is_authenticated:
        return queryset.filter(client_id__in=ClientContact.objects.filter(user=user).values("client_id"))
    return queryset.none()


def messages_for(user, ticket):
    """The messages ``user`` may read: staff see internal notes, nobody else does."""
    queryset = ticket.messages.prefetch_related("attachments")
    return queryset if can_view_all(user) else queryset.filter(is_internal=False)


def can_view_attachment(user, attachment):
    message = attachment.message
    if can_view_all(user):
        return True
    return not message.is_internal and is_client_contact(user, message.ticket.client)


def assignable_agents():
    """Active users who can act on tickets."""
    return [u for u in User.objects.filter(is_active=True).exclude(role=Role.CUSTOMER).order_by("email")
            if u.has_perm(perm("manage_support"))]


# --- Building blocks ------------------------------------------------------------------------------------

def _author_name(user, kind=MessageKind.CUSTOMER):
    """
    The name shown on a message. A staff member is shown by name if they have one, otherwise as "Support team" -
    never by email address, which a customer must not learn from a ticket. (A customer's own name/email is theirs.)
    """
    name = getattr(user, "full_name", "") or ""
    if kind == MessageKind.STAFF:
        return (name or "Support team")[:200]
    return (name or getattr(user, "email", "") or "System")[:200]


def _clean_text(value, label, limit):
    value = (value or "").strip()
    if not value:
        raise ValidationError(f"Enter {label}.")
    if len(value) > limit:
        raise ValidationError(f"{label[0].upper() + label[1:]} is too long (at most {limit} characters).")
    return value


def _check_related(client, hosting_account, domain):
    if hosting_account is not None and hosting_account.client_id != client.pk:
        raise ServiceError("That hosting account does not belong to this client.", code="invalid_related")
    if domain is not None and domain.client_id != client.pk:
        raise ServiceError("That domain does not belong to this client.", code="invalid_related")


def _store_attachments(message, checked):
    """Write the (already validated) files. If one fails, the ones already written are removed again."""
    saved = []
    try:
        for item in checked:
            attachment = TicketAttachment(message=message, original_name=item.name, content_type=item.content_type,
                                          size=item.size)
            attachment.file.save(item.name, item.file, save=False)
            saved.append(attachment.file)
            attachment.save()
    except Exception:
        for stored in saved:
            try:
                stored.storage.delete(stored.name)
            except Exception:  # noqa: BLE001 - cleanup is best effort
                logger.exception("Could not remove an orphaned attachment %s", stored.name)
        raise


def _add_message(ticket, actor, kind, body, *, internal=False, checked=()):
    message = TicketMessage(ticket=ticket, author=actor if getattr(actor, "pk", None) else None,
                            author_name=_author_name(actor, kind) if kind != MessageKind.SYSTEM else "System",
                            kind=kind, body=body, is_internal=internal)
    message.full_clean(exclude=["ticket", "author"])
    message.save()
    _store_attachments(message, checked)
    return message


def _touch(ticket, **fields):
    fields.setdefault("last_activity_at", timezone.now())
    for name, value in fields.items():
        setattr(ticket, name, value)
    ticket.save(update_fields=[*fields, "updated_at"])


def customer_can_reply(ticket, *, now=None):
    """A customer may reply unless the ticket has been closed for longer than the reopen window."""
    if not ticket.is_closed:
        return True
    return ticket.closed_at is not None and (now or timezone.now()) - ticket.closed_at <= CLOSED_REPLY_WINDOW


def _staff_link(ticket):
    return reverse("support_staff:ticket", args=[ticket.pk])


def _customer_link(ticket):
    return reverse("support_customer:ticket", args=[ticket.pk])


def _notify_assignee(ticket, *, event, title, body, exclude=None):
    agent = ticket.assigned_to
    if agent is not None and agent != exclude and agent.is_active:
        notifications.notify(agent, event=event, title=title, body=body[:200], link=_staff_link(ticket))


def _requester(ticket):
    """The customer contact who opened the ticket, or None (e.g. staff opened it on the client's behalf)."""
    opener = ticket.opened_by
    return opener if opener is not None and is_client_contact(opener, ticket.client) else None


def _requester_email(ticket):
    requester = _requester(ticket)
    return requester.email if requester is not None and requester.email else ticket.client.email


# --- Opening a ticket -----------------------------------------------------------------------------------

@transaction.atomic
def open_ticket(actor, client, *, department, subject, body, priority=TicketPriority.NORMAL, hosting_account=None,
                domain=None, files=(), request=None):
    """Open a ticket for ``client``: a customer contact, or staff on the client's behalf."""
    staff = can_manage(actor)
    if not (staff or is_client_contact(actor, client)):
        raise _denied()
    subject = _clean_text(subject, "a subject", MAX_SUBJECT)
    body = _clean_text(body, "a message", MAX_BODY)
    if not isinstance(department, Department) or not department.is_active:
        raise ServiceError("Choose a department.", code="department_invalid")
    if priority not in TicketPriority.values or (not staff and priority not in CUSTOMER_PRIORITIES):
        raise ServiceError("Choose a valid priority.", code="priority_invalid")
    _check_related(client, hosting_account, domain)
    if not staff and Ticket.objects.filter(opened_by=actor, created_at__gte=timezone.now() - timedelta(hours=1)
                                           ).count() >= TICKETS_PER_HOUR:
        raise ServiceError("You have opened several tickets in the last hour. Please wait a little, or add to an "
                           "existing ticket.", code="rate_limited", status_code=http.HTTP_429_TOO_MANY_REQUESTS)
    checked = uploads.check_uploads(files)  # validated before anything is written

    ticket = Ticket(client=client, opened_by=actor, department=department, subject=subject, priority=priority,
                    status=TicketStatus.OPEN, hosting_account=hosting_account, domain=domain,
                    assigned_to=department.default_assignee if department.default_assignee_id and
                    department.default_assignee.is_active else None)
    ticket.full_clean(exclude=["last_activity_at"])
    ticket.save()
    kind = MessageKind.STAFF if staff and not is_client_contact(actor, client) else MessageKind.CUSTOMER
    _add_message(ticket, actor, kind, body, checked=checked)
    if kind == MessageKind.CUSTOMER:
        _touch(ticket, last_customer_reply_at=timezone.now())
    audit.record("ticket.opened", actor=actor, target=ticket,
                 metadata={"client_id": client.pk, "department": department.slug, "priority": priority,
                           "attachments": len(checked), "on_behalf": kind == MessageKind.STAFF}, request=request)
    notifications.send_email(to_email=_requester_email(ticket), template="ticket_opened",
                             context={"ticket": ticket, "link": _customer_link(ticket)},
                             user=actor if getattr(actor, "pk", None) else None, event="ticket.opened")
    _notify_assignee(ticket, event="ticket.assigned", title=f"New ticket {ticket.reference}: {subject}"[:200],
                     body=body, exclude=actor)
    return ticket


# --- Replying -------------------------------------------------------------------------------------------

@transaction.atomic
def reply(actor, ticket, body, *, files=(), internal=False, set_status=None, request=None):
    """
    Add a message. A customer contact replies publicly (which reopens a resolved ticket, or a closed one within a
    week); staff reply publicly, or leave an *internal note* the customer can never see. A public staff reply moves the
    ticket to Agent Reply unless ``set_status`` says otherwise; a customer reply moves it to Customer Reply.
    """
    ticket = Ticket.objects.select_for_update().get(pk=ticket.pk)
    staff = can_manage(actor)
    customer = is_client_contact(actor, ticket.client)
    if not (staff or customer):
        raise _denied()
    body = _clean_text(body, "a message", MAX_BODY)
    checked = uploads.check_uploads(files)
    if internal and not staff:
        raise _denied("Only staff can add internal notes.")
    if set_status is not None and not staff:
        raise _denied()
    if set_status is not None and set_status not in TicketStatus.values:
        raise ServiceError("Choose a valid status.", code="invalid_status")

    now = timezone.now()
    if staff and not internal:  # a public staff reply
        if ticket.is_closed:
            raise ServiceError("This ticket is closed. Reopen it before replying.", code="ticket_closed")
        message = _add_message(ticket, actor, MessageKind.STAFF, body, checked=checked)
        _touch(ticket, last_staff_reply_at=now)
        new_status = set_status or TicketStatus.AGENT_REPLY
        ticket = lifecycle.transition(ticket, new_status, actor=actor, action="ticket.status_changed",
                                      reason="reply", request=request)
        audit.record("ticket.replied", actor=actor, target=ticket,
                     metadata={"message_id": message.pk, "attachments": len(checked), "client_id": ticket.client_id},
                     request=request)
        notifications.send_email(to_email=_requester_email(ticket), template="ticket_reply",
                                 context={"ticket": ticket, "message": message, "link": _customer_link(ticket)},
                                 event="ticket.reply")
        requester = _requester(ticket)
        if requester is not None:
            notifications.notify(requester, event="ticket.reply", title=f"Reply on {ticket.reference}",
                                 body=ticket.subject, link=_customer_link(ticket))
        return message

    if staff and internal:  # a note: no status change, no customer contact
        message = _add_message(ticket, actor, MessageKind.STAFF, body, internal=True, checked=checked)
        audit.record("ticket.note_added", actor=actor, target=ticket,
                     metadata={"message_id": message.pk, "client_id": ticket.client_id}, request=request)
        if set_status is not None:
            ticket = lifecycle.transition(ticket, set_status, actor=actor, reason="note", request=request)
        _notify_assignee(ticket, event="ticket.note", title=f"Note on {ticket.reference}", body=body, exclude=actor)
        return message

    # A customer contact.
    if not customer_can_reply(ticket, now=now):
        raise ServiceError("This ticket was closed some time ago. Please open a new ticket and mention this one.",
                           code="ticket_closed")
    message = _add_message(ticket, actor, MessageKind.CUSTOMER, body, checked=checked)
    _touch(ticket, last_customer_reply_at=now)
    if ticket.is_closed:  # a reply within the window reopens it; a closed ticket can only move to Open
        ticket = lifecycle.transition(ticket, TicketStatus.OPEN, actor=actor, reason="reopened by a customer reply",
                                      request=request)
    ticket = lifecycle.transition(ticket, TicketStatus.CUSTOMER_REPLY, actor=actor,
                                  action="ticket.status_changed", reason="reply", request=request)
    audit.record("ticket.replied", actor=actor, target=ticket,
                 metadata={"message_id": message.pk, "attachments": len(checked), "client_id": ticket.client_id},
                 request=request)
    _notify_assignee(ticket, event="ticket.customer_reply", title=f"Customer replied on {ticket.reference}",
                     body=body, exclude=actor)
    return message


# --- Status, assignment, priority, department ------------------------------------------------------------

@transaction.atomic
def set_status(actor, ticket, new_status, *, reason="", request=None):
    """Staff set any legal status; a customer contact may only close their own ticket."""
    if can_manage(actor):
        pass
    elif is_client_contact(actor, ticket.client) and new_status == TicketStatus.CLOSED:
        pass
    else:
        raise _denied()
    if new_status not in TicketStatus.values:
        raise ServiceError("Choose a valid status.", code="invalid_status")
    return lifecycle.transition(ticket, new_status, actor=actor, reason=reason, request=request)


def _agent_ok(agent):
    return agent is not None and agent.is_active and agent.has_perm(perm("manage_support"))


@transaction.atomic
def assign(actor, ticket, agent, *, request=None):
    """Staff: give the ticket to an agent, or (``agent=None``) leave it unassigned."""
    _require_manage(actor)
    if agent is not None and not _agent_ok(agent):
        raise ServiceError("That person cannot handle tickets.", code="assignee_invalid")
    ticket = Ticket.objects.select_for_update().get(pk=ticket.pk)
    if ticket.assigned_to_id == (agent.pk if agent else None):
        return ticket
    previous = ticket.assigned_to.email if ticket.assigned_to else ""
    _touch(ticket, assigned_to=agent)
    audit.record("ticket.assigned", actor=actor, target=ticket,
                 metadata={"from": previous, "to": agent.email if agent else "", "client_id": ticket.client_id},
                 request=request)
    if agent is not None:
        _notify_assignee(ticket, event="ticket.assigned", title=f"Ticket {ticket.reference} assigned to you",
                         body=ticket.subject, exclude=actor)
    return ticket


@transaction.atomic
def set_priority(actor, ticket, priority, *, request=None):
    _require_manage(actor)
    if priority not in TicketPriority.values:
        raise ServiceError("Choose a valid priority.", code="priority_invalid")
    ticket = Ticket.objects.select_for_update().get(pk=ticket.pk)
    if ticket.priority == priority:
        return ticket
    previous = ticket.priority
    _touch(ticket, priority=priority)
    audit.record("ticket.priority_changed", actor=actor, target=ticket,
                 metadata={"from": previous, "to": priority, "client_id": ticket.client_id}, request=request)
    return ticket


@transaction.atomic
def set_department(actor, ticket, department, *, request=None):
    _require_manage(actor)
    if not isinstance(department, Department) or not department.is_active:
        raise ServiceError("Choose a department.", code="department_invalid")
    ticket = Ticket.objects.select_for_update().get(pk=ticket.pk)
    if ticket.department_id == department.pk:
        return ticket
    previous = ticket.department.slug
    _touch(ticket, department=department)
    audit.record("ticket.department_changed", actor=actor, target=ticket,
                 metadata={"from": previous, "to": department.slug, "client_id": ticket.client_id}, request=request)
    return ticket


def auto_close_resolved(*, now=None):
    """Close tickets that have sat in Resolved for a week with no reply (run daily). Returns how many closed."""
    now = now or timezone.now()
    closed = 0
    stale = Ticket.objects.filter(status=TicketStatus.RESOLVED, resolved_at__lt=now - AUTO_CLOSE_AFTER)
    for ticket in stale:
        with transaction.atomic():
            fresh = Ticket.objects.select_for_update().get(pk=ticket.pk)
            if fresh.status == TicketStatus.RESOLVED:
                lifecycle.transition(fresh, TicketStatus.CLOSED, action="ticket.auto_closed",
                                     reason="Resolved for a week with no reply")
                closed += 1
    return closed


# --- Search and the staff overview ------------------------------------------------------------------------

def search_tickets(queryset, term):
    import re

    from django.db.models import Q

    term = (term or "").strip()
    if not term:
        return queryset
    q = (Q(subject__icontains=term) | Q(client__company_name__icontains=term) | Q(client__email__icontains=term)
         | Q(client__first_name__icontains=term) | Q(client__last_name__icontains=term))
    match = re.fullmatch(r"[tT]?(\d+)", term)
    if match:
        q |= Q(pk=int(match.group(1)))
    return queryset.filter(q)


def overview(user, *, now=None):
    """Counts and lists for the staff support overview."""
    from django.db.models import Count

    now = now or timezone.now()
    tickets = Ticket.objects.all()
    active = tickets.filter(status__in=lifecycle.ACTIVE)
    awaiting = tickets.filter(status__in=lifecycle.AWAITING_AGENT)
    oldest = awaiting.order_by("last_activity_at").first()
    return {
        "counts": {"active": active.count(), "awaiting_agent": awaiting.count(),
                   "unassigned": active.filter(assigned_to__isnull=True).count(),
                   "mine": active.filter(assigned_to=user).count() if user else 0,
                   "urgent": active.filter(priority=TicketPriority.URGENT).count(),
                   "resolved_week": tickets.filter(status=TicketStatus.RESOLVED,
                                                   resolved_at__gte=now - timedelta(days=7)).count()},
        "oldest_waiting": oldest,
        "by_department": list(active.values("department__name").annotate(total=Count("id")).order_by("-total")),
        "recent": list(tickets.select_related("client", "department", "assigned_to")[:10]),
        "needs_attention": list(awaiting.select_related("client", "department", "assigned_to")
                                .order_by("-priority", "last_activity_at")[:10]),
    }


# --- Departments ---------------------------------------------------------------------------------------------

@transaction.atomic
def save_department(actor, department, *, name, description="", is_active=True, sort_order=0, default_assignee=None,
                    request=None):
    """Create (``department=None``) or update a department."""
    from apps.core.utils import unique_slug

    _require_manage(actor)
    if default_assignee is not None and not _agent_ok(default_assignee):
        raise ServiceError("The default assignee cannot handle tickets.", code="assignee_invalid")
    created = department is None
    department = department or Department()
    department.name, department.description = (name or "").strip(), (description or "").strip()
    department.is_active, department.sort_order, department.default_assignee = is_active, sort_order, default_assignee
    if created:
        department.slug = unique_slug(Department.objects.all(), department.name, max_length=60)
    department.full_clean()
    department.save()
    audit.record("department.created" if created else "department.updated", actor=actor, target=department,
                 metadata={"name": department.name, "is_active": is_active}, request=request)
    return department


# --- Predefined replies -----------------------------------------------------------------------------------------

def canned_replies_for(department=None):
    queryset = CannedReply.objects.filter(is_active=True)
    if department is not None:
        from django.db.models import Q

        queryset = queryset.filter(Q(department__isnull=True) | Q(department=department))
    return queryset.select_related("department")


def render_canned(reply, ticket, agent):
    """The reply's text with ``{client}``, ``{ticket}`` and ``{agent}`` filled in (nothing else is interpreted)."""
    text = reply.body
    for token, value in (("{client}", ticket.client.first_name or ticket.client.display_name),
                         ("{ticket}", ticket.reference), ("{agent}", _author_name(agent, MessageKind.STAFF))):
        text = text.replace(token, value)
    return text


@transaction.atomic
def save_canned_reply(actor, reply_obj, *, title, body, department=None, is_active=True, sort_order=0, request=None):
    _require_manage(actor)
    created = reply_obj is None
    reply_obj = reply_obj or CannedReply()
    reply_obj.title, reply_obj.body = (title or "").strip(), (body or "").strip()
    reply_obj.department, reply_obj.is_active, reply_obj.sort_order = department, is_active, sort_order
    reply_obj.full_clean()
    reply_obj.save()
    audit.record("canned_reply.created" if created else "canned_reply.updated", actor=actor, target=reply_obj,
                 metadata={"title": reply_obj.title}, request=request)
    return reply_obj
