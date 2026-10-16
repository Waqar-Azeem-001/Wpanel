"""
The assistant's loop: the client's message and the chat so far go to Claude together with the list of tools; when Claude asks for a tool the panel runs it (as the signed-in
person, on their own services only) and hands the result back, until Claude answers in words. Claude never touches a server: it only asks, the panel decides and does.

The key comes from the server's environment (``ANTHROPIC_API_KEY``), never from the database or the browser.
"""
import json
import logging

import requests
from django.conf import settings
from django.utils import timezone
from rest_framework import status

from apps.audit import services as audit
from apps.core.exceptions import ServiceError

from . import brain, knowledge, tools
from .models import AssistantSettings, Conversation, ConversationStatus, Message, SenderType

logger = logging.getLogger(__name__)

API_URL = "https" + "://api.anthropic.com/v1/messages"
API_VERSION = "2023-06-01"
MAX_STEPS = 6
HISTORY = 24
MAX_INPUT = 2000

def api_key():
    return getattr(settings, "ANTHROPIC_API_KEY", "") or ""


def available(config=None):
    config = config or AssistantSettings.load()
    return bool(config.enabled and (config.mode == "knowledge" or api_key()))


def call_model(model, system, messages, tool_list):
    """One request to Claude. Returns the response body; raises ServiceError when the assistant cannot answer right now."""
    try:
        reply = requests.post(API_URL, timeout=(10, 90), headers={"x-api-key": api_key(), "anthropic-version": API_VERSION, "content-type": "application/json"},
                              json={"model": model, "max_tokens": 1200, "system": system, "messages": messages, "tools": tool_list})
    except requests.exceptions.RequestException as exc:
        logger.warning("assistant: the model could not be reached (%s)", type(exc).__name__)
        raise ServiceError("The AI Agent is not available right now. Please try again in a minute, or open a ticket.", code="assistant_unreachable",
                           status_code=status.HTTP_503_SERVICE_UNAVAILABLE) from exc
    if reply.status_code != 200:
        logger.warning("assistant: the model answered %s", reply.status_code)
        raise ServiceError("The AI Agent is not available right now. Please try again in a minute, or open a ticket.", code="assistant_error",
                           status_code=status.HTTP_503_SERVICE_UNAVAILABLE)
    return reply.json()


def used_today(client):
    start = timezone.now().replace(hour=0, minute=0, second=0, microsecond=0)
    return Message.objects.filter(conversation__client=client, sender=SenderType.CLIENT, created_at__gte=start).count()


def current_conversation(user, client, create=True):
    found = Conversation.objects.filter(user=user, client=client).exclude(status=ConversationStatus.CLOSED).first()
    if found is None and create:
        found = Conversation.objects.create(user=user, client=client)
    return found


def _history(conversation):
    rows = list(conversation.messages.exclude(sender=SenderType.EVENT).order_by("-id")[:HISTORY])[::-1]
    out = []
    for row in rows:
        role = "user" if row.sender == SenderType.CLIENT else "assistant"
        if out and out[-1]["role"] == role:
            out[-1]["content"] += "\n" + row.body
        else:
            out.append({"role": role, "content": row.body})
    while out and out[0]["role"] != "user":
        out.pop(0)
    return out


def send(user, conversation, text, request=None):
    """The client says ``text``. Returns the new messages (events, then the assistant's answer), all saved in the conversation."""
    config = AssistantSettings.load()
    text = (text or "").strip()
    if conversation.ticket_id and conversation.status == ConversationStatus.CLOSED:
        raise ServiceError(f"This chat became ticket {conversation.ticket.reference}. Reply to our team there, or start a new chat.", code="with_staff")
    if conversation.status in (ConversationStatus.WAITING, ConversationStatus.WITH_STAFF):  # a person is on it: the assistant stays quiet, the message just goes to them
        if len(text) > MAX_INPUT:
            raise ServiceError(f"Please keep a message under {MAX_INPUT} characters.", code="too_long")
        if not text:
            raise ServiceError("Write your message first.", code="empty")
        return [Message.objects.create(conversation=conversation, sender=SenderType.CLIENT, body=text)]
    if not available(config):
        raise ServiceError("The AI Agent is not switched on yet. Please open a ticket and our team will help.", code="assistant_off", status_code=status.HTTP_503_SERVICE_UNAVAILABLE)
    if not text:
        raise ServiceError("Write your message first.", code="empty")
    if len(text) > MAX_INPUT:
        raise ServiceError(f"Please keep a message under {MAX_INPUT} characters.", code="too_long")
    if used_today(conversation.client) >= config.daily_limit:
        raise ServiceError("You have reached today's message limit for the AI Agent. Please open a ticket, or try again tomorrow.", code="daily_limit",
                           status_code=status.HTTP_429_TOO_MANY_REQUESTS)

    if config.mode == "knowledge":  # the Agent's own knowledge base: no outside service
        Message.objects.create(conversation=conversation, sender=SenderType.CLIENT, body=text)
        saved = brain.reply(user, conversation, text, request=request)
        conversation.save(update_fields=["updated_at", "status"])
        return saved

    from apps.branding.models import BrandSettings

    row = BrandSettings.objects.first()
    Message.objects.create(conversation=conversation, sender=SenderType.CLIENT, body=text)
    messages = _history(conversation)
    system = knowledge.system_prompt((row.site_name if row else "") or settings.SITE_NAME, timezone.now().date().isoformat(), config.change_hours)
    ctx = tools.Context(user, conversation, request)
    saved, answer = [], ""
    for _ in range(MAX_STEPS):
        body = call_model(config.model, system, messages, tools.schemas())
        blocks = body.get("content") or []
        calls = [b for b in blocks if b.get("type") == "tool_use"]
        answer = "".join(b.get("text", "") for b in blocks if b.get("type") == "text").strip()
        if not calls:
            break
        messages.append({"role": "assistant", "content": blocks})
        results = []
        for call in calls:
            name, args = call.get("name", ""), call.get("input") or {}
            out = tools.run(ctx, name, args)
            audit.record("assistant.tool", actor=user, target=conversation, metadata={"tool": name, "domain": str(args.get("domain", ""))[:100], "failed": "error" in out}, request=request)
            saved.append(Message.objects.create(conversation=conversation, sender=SenderType.EVENT, body=name, tool_call={"tool": name, "domain": str(args.get("domain", ""))[:100], "failed": "error" in out}))
            results.append({"type": "tool_result", "tool_use_id": call.get("id"), "content": json.dumps(out)})
        messages.append({"role": "user", "content": results})
    else:
        answer = answer or "I could not finish checking that. Please open a ticket and our team will look."
    answer = answer or "Sorry, I could not put an answer together. Please try again, or open a ticket."
    saved.append(Message.objects.create(conversation=conversation, sender=SenderType.AI, body=answer[:6000]))
    conversation.save(update_fields=["updated_at", "status"])
    return saved


def handoff(user, conversation, request=None):
    """The "Talk to a person" button: the chat goes to the queue for staff, whatever the assistant thinks."""
    from . import live

    return live.ask_person(user, conversation, "The client pressed Talk to a person.", request=request)
