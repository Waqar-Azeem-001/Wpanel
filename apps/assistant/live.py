"""
Live chat with a person. When the client asks for one (or the assistant decides they need one) the chat goes to "waiting"; the assistant stops answering; a staff member takes the
chat, talks to the client in the same window, then hands it back to the assistant, closes it or turns it into a ticket. Only one person owns a chat at a time: taking it is one
atomic update, so two people cannot take the same chat.
"""
from django.db import transaction
from django.utils import timezone
from rest_framework import status

from apps.audit import services as audit
from apps.core.exceptions import ServiceError
from apps.notifications import services as notifications
from apps.support import services as support

from . import tools
from .models import Conversation, ConversationStatus, Message, SenderType

OPEN_FOR_STAFF = (ConversationStatus.WAITING, ConversationStatus.AI, ConversationStatus.WITH_STAFF)
WAIT_BEFORE_TICKET_OFFER_MINUTES = 5


def _denied():
    return ServiceError("You do not have permission to perform this action.", code="permission_denied", status_code=status.HTTP_403_FORBIDDEN)


def _name(user):
    return (user.get_full_name() or user.email.split("@")[0]).strip()


def _event(conversation, text, **extra):
    return Message.objects.create(conversation=conversation, sender=SenderType.EVENT, body=text, tool_call={"tool": "", **extra})


def _summary(conversation):
    """What the person who takes the chat should know. The assistant's own wording when the model answers; otherwise the last lines of the chat."""
    lines = tools.transcript(conversation, limit=8)
    return (conversation.handoff_reason + "\n\n" if conversation.handoff_reason else "") + "Last messages:\n" + lines


def ask_person(user, conversation, reason, request=None):
    """The client (or the assistant for them) asks for a person. Returns {"status": ...}."""
    if conversation.status in (ConversationStatus.WAITING, ConversationStatus.WITH_STAFF):
        return {"status": conversation.status}
    conversation.status, conversation.handoff_reason = ConversationStatus.WAITING, (reason or "")[:300]
    conversation.handoff_requested_at = timezone.now()
    conversation.handoff_summary = _summary(conversation)
    conversation.save(update_fields=["status", "handoff_reason", "handoff_requested_at", "handoff_summary", "updated_at"])
    _event(conversation, "Connecting you to our support team...", kind="handoff")
    audit.record("assistant.handoff_requested", actor=user, target=conversation, metadata={"reason": conversation.handoff_reason}, request=request)
    notifications.notify_team("chat.waiting", "manage_support", exclude=user, title=f"{conversation.client} is waiting in the chat", body=conversation.handoff_reason,
                              link=f"/staff/chats/{conversation.pk}/")
    return {"status": ConversationStatus.WAITING}


def take(staff, conversation, request=None):
    if not support.can_manage(staff):
        raise _denied()
    with transaction.atomic():
        won = Conversation.objects.filter(pk=conversation.pk, status__in=(ConversationStatus.WAITING, ConversationStatus.AI)).update(
            status=ConversationStatus.WITH_STAFF, assigned_to=staff, updated_at=timezone.now())
    if not won:
        raise ServiceError("This chat is already with someone else or is closed.", code="taken")
    conversation.refresh_from_db()
    _event(conversation, f"{_name(staff)} joined the chat. The assistant is paused.", kind="joined")
    audit.record("assistant.chat_taken", actor=staff, target=conversation, request=request)
    return conversation


def _mine(staff, conversation):
    if not support.can_manage(staff):
        raise _denied()
    if conversation.status != ConversationStatus.WITH_STAFF or conversation.assigned_to_id != staff.pk:
        raise ServiceError("This chat is not yours. Take it first.", code="not_yours")


def reply(staff, conversation, text, request=None):
    _mine(staff, conversation)
    text = (text or "").strip()
    if not text:
        raise ServiceError("Write your message first.", code="empty")
    if len(text) > 4000:
        raise ServiceError("Please keep a message under 4000 characters.", code="too_long")
    message = Message.objects.create(conversation=conversation, sender=SenderType.STAFF, staff=staff, body=text)
    conversation.save(update_fields=["updated_at"])
    audit.record("assistant.staff_reply", actor=staff, target=conversation, request=request)
    return message


def hand_back(staff, conversation, request=None):
    _mine(staff, conversation)
    conversation.status, conversation.assigned_to = ConversationStatus.AI, None
    conversation.save(update_fields=["status", "assigned_to", "updated_at"])
    _event(conversation, "The assistant is back. Ask it anything else.", kind="handed_back")
    audit.record("assistant.chat_handed_back", actor=staff, target=conversation, request=request)


def close(staff, conversation, request=None):
    _mine(staff, conversation)
    conversation.status = ConversationStatus.CLOSED
    conversation.save(update_fields=["status", "updated_at"])
    _event(conversation, "This chat was closed. Start a new one any time.", kind="closed")
    audit.record("assistant.chat_closed", actor=staff, target=conversation, request=request)


def transfer(staff, conversation, to_user, request=None):
    _mine(staff, conversation)
    if not support.can_manage(to_user):
        raise ServiceError("Choose a member of the support team.", code="not_agent")
    conversation.assigned_to = to_user
    conversation.save(update_fields=["assigned_to", "updated_at"])
    _event(conversation, f"{_name(staff)} passed the chat to {_name(to_user)}.", kind="transferred")
    audit.record("assistant.chat_transferred", actor=staff, target=conversation, metadata={"to": to_user.pk}, request=request)


def _ticket(actor, conversation, request, subject):
    body = (conversation.handoff_summary or "") + "\n\nChat so far:\n" + tools.transcript(conversation, limit=30)
    department = tools._department("")
    ticket = support.open_ticket(actor, conversation.client, department=department, subject=subject[:150], body=body[:9000], request=request)
    conversation.ticket, conversation.status = ticket, ConversationStatus.CLOSED
    conversation.save(update_fields=["ticket", "status", "updated_at"])
    return ticket


def to_ticket(staff, conversation, request=None):
    """Staff: turn the chat into a ticket for the client and close the chat."""
    if not support.can_manage(staff):
        raise _denied()
    if conversation.ticket_id:
        raise ServiceError("This chat already has a ticket.", code="has_ticket")
    ticket = _ticket(staff, conversation, request, "Chat with support: " + (conversation.handoff_reason or "follow-up")[:80])
    _event(conversation, f"This chat became ticket {ticket.reference}. Our team will reply there and by email.", kind="ticket", ticket=ticket.reference)
    audit.record("assistant.chat_to_ticket", actor=staff, target=conversation, metadata={"ticket": ticket.reference}, request=request)
    return ticket


def ticket_instead(user, conversation, request=None):
    """The client does not want to wait: a ticket with the chat attached, and the chat closes."""
    if conversation.ticket_id:
        raise ServiceError("This chat already has a ticket.", code="has_ticket")
    ticket = _ticket(user, conversation, request, "Chat with support: " + (conversation.handoff_reason or "help wanted")[:80])
    _event(conversation, f"You asked for a ticket instead: {ticket.reference}. Our team will reply there and by email.", kind="ticket", ticket=ticket.reference)
    audit.record("assistant.ticket_instead", actor=user, target=conversation, metadata={"ticket": ticket.reference}, request=request)
    return ticket


def counts():
    from django.db.models import Count

    rows = dict(Conversation.objects.values_list("status").annotate(n=Count("id")))
    return {"all": sum(rows.values()), **{s: rows.get(s, 0) for s in ConversationStatus.values}}
