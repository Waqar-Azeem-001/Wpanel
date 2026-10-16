from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from apps.accounts.roles import perm
from apps.audit import services as audit
from apps.clients.models import Client
from apps.core.exceptions import ServiceError

from . import agent
from .models import AssistantSettings, Conversation, ConversationStatus, Message, SenderType

SHOWN = 60


def _client(user):
    return Client.objects.filter(contacts__user=user).order_by("id").first()


def _rows(conversation):
    return [{"sender": m.sender, "body": m.body, "tool": (m.tool_call or {}).get("tool", ""), "at": m.created_at} for m in conversation.messages.order_by("-id")[:SHOWN][::-1]]


@login_required
def chat(request):
    client = _client(request.user)
    if client is None:
        return render(request, "assistant/unavailable.html")
    config = AssistantSettings.load()
    conversation = agent.current_conversation(request.user, client)
    return render(request, "assistant/chat.html", {
        "conversation": conversation, "rows": _rows(conversation), "ready": agent.available(config), "left": max(0, config.daily_limit - agent.used_today(client)),
        "with_staff": bool(conversation.ticket_id), "ticket": conversation.ticket})


@require_POST
@login_required
def send(request):
    client = _client(request.user)
    if client is None:
        raise PermissionDenied
    conversation = agent.current_conversation(request.user, client)
    wants_json = request.headers.get("X-Requested-With") == "fetch"
    try:
        saved = agent.send(request.user, conversation, request.POST.get("message", ""), request=request)
    except ServiceError as exc:
        if wants_json:
            return JsonResponse({"error": exc.message}, status=exc.status_code if 400 <= exc.status_code < 600 else 400)
        messages.error(request, exc.message)
        return redirect("assistant:chat")
    if wants_json:
        return JsonResponse({"messages": [{"sender": m.sender, "body": m.body, "tool": (m.tool_call or {}).get("tool", "")} for m in saved]})
    return redirect("assistant:chat")


@require_POST
@login_required
def handoff(request):
    client = _client(request.user)
    if client is None:
        raise PermissionDenied
    conversation = agent.current_conversation(request.user, client)
    try:
        out = agent.handoff(request.user, conversation, request=request)
    except ServiceError as exc:
        messages.error(request, exc.message)
    else:
        messages.success(request, f"Our team has your chat as ticket {out.get('ticket', '')}. They will reply there and by email.")
    return redirect("assistant:chat")


@require_POST
@login_required
def new_chat(request):
    client = _client(request.user)
    if client is None:
        raise PermissionDenied
    Conversation.objects.filter(user=request.user, client=client).exclude(status=ConversationStatus.CLOSED).update(status=ConversationStatus.CLOSED, updated_at=timezone.now())
    return redirect("assistant:chat")


# --- staff: switches and the record of what the assistant did -----------------------------------------------------------------------------------

def _staff(request, manage=False):
    if not request.user.is_authenticated:
        raise PermissionDenied
    if not request.user.has_perm(perm("manage_settings" if manage else "view_settings")):
        raise PermissionDenied


@login_required
def staff_settings(request):
    _staff(request)
    can_manage = request.user.has_perm(perm("manage_settings"))
    config = AssistantSettings.load()
    if request.method == "POST":
        _staff(request, manage=True)
        try:
            model = (request.POST.get("model") or "").strip()
            limit = int(request.POST.get("daily_limit") or 0)
            if not model or len(model) > 80 or any(c.isspace() for c in model):
                raise ServiceError("Write the model name as Claude's documentation gives it, for example claude-sonnet-5-5.", code="model_invalid")
            if not 1 <= limit <= 500:
                raise ServiceError("The daily limit is a number from 1 to 500.", code="limit_invalid")
        except (ServiceError, ValueError) as exc:
            messages.error(request, getattr(exc, "message", "The daily limit is a number from 1 to 500."))
        else:
            config.enabled, config.model, config.daily_limit = request.POST.get("enabled") == "on", model, limit
            config.save()
            audit.record("assistant.settings_saved", actor=request.user, target=config, metadata={"enabled": config.enabled, "model": model, "daily_limit": limit}, request=request)
            messages.success(request, "The assistant settings were saved.")
        return redirect("assistant_staff:settings")
    start = timezone.now().replace(hour=0, minute=0, second=0, microsecond=0)
    return render(request, "assistant/settings.html", {
        "config": config, "can_manage": can_manage, "key_set": bool(agent.api_key()), "today": Message.objects.filter(sender=SenderType.CLIENT, created_at__gte=start).count(),
        "chats": Conversation.objects.select_related("client", "user", "ticket").order_by("-updated_at")[:25]})


@login_required
def staff_chat(request, pk):
    _staff(request)
    conversation = get_object_or_404(Conversation.objects.select_related("client", "user", "ticket"), pk=pk)
    return render(request, "assistant/transcript.html", {"conversation": conversation, "rows": [{"sender": m.sender, "body": m.body, "tool": (m.tool_call or {}), "at": m.created_at}
                                                                                              for m in conversation.messages.all()]})
