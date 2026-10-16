import datetime as dt
import re
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from urllib.parse import quote

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from apps.accounts.roles import perm
from apps.audit import services as audit
from apps.clients.models import Client
from apps.core.exceptions import ServiceError

from . import actions, agent, live
from .views_chats import serialise
from .models import AssistantSettings, CallbackRequest, Conversation, ConversationStatus, KnowledgeEntry, Message, SenderType, Unanswered

SHOWN = 60


def _client(user):
    return Client.objects.filter(contacts__user=user).order_by("id").first()


def _rows(conversation):
    return [{"sender": m.sender, "body": m.body, "tool": (m.tool_call or {}).get("tool", ""), "kind": (m.tool_call or {}).get("kind", ""), "at": m.created_at,
             "action": (m.tool_call or {}).get("action"), "state": (m.tool_call or {}).get("status", ""), "needs": (m.tool_call or {}).get("needs", "approve"),
             "who": (m.staff.get_full_name() or m.staff.email.split("@")[0]) if m.staff_id else "", "id": m.id}
            for m in conversation.messages.select_related("staff").order_by("-id")[:SHOWN][::-1]]


def _context(request, client):
    config = AssistantSettings.load()
    conversation = agent.current_conversation(request.user, client)
    rows = _rows(conversation)
    waited = (timezone.now() - conversation.handoff_requested_at) if conversation.handoff_requested_at and conversation.status == ConversationStatus.WAITING else None
    return {
        "conversation": conversation, "rows": rows, "last_id": rows[-1]["id"] if rows else 0, "ready": agent.available(config), "left": max(0, config.daily_limit - agent.used_today(client)),
        "waiting": conversation.status == ConversationStatus.WAITING, "with_person": conversation.status == ConversationStatus.WITH_STAFF, "person": conversation.assigned_to,
        "offer_ticket": waited is not None and waited.total_seconds() >= live.WAIT_BEFORE_TICKET_OFFER_MINUTES * 60,
        "act_base": reverse("assistant:act", args=[0, "approve"]).replace("0/approve/", ""),
        "whatsapp_url": (("https" + "://wa.me/" + config.whatsapp_number + "?text=" + quote(f"Hello, I am continuing chat #{conversation.pk} from the customer area.")) if config.whatsapp_number else ""),
        "verify_base": reverse("assistant:verify", args=[0]).replace("0/", ""),
        "feedback_base": reverse("assistant:feedback", args=[0, "up"]).replace("0/up/", "")}


@login_required
def chat(request):
    client = _client(request.user)
    if client is None:
        return render(request, "assistant/unavailable.html")
    return render(request, "assistant/chat.html", _context(request, client))


@login_required
def panel(request):
    """The chat for the pop-up (no page around it)."""
    client = _client(request.user)
    if client is None:
        return render(request, "assistant/panel_none.html")
    return render(request, "assistant/panel.html", _context(request, client))


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
        return JsonResponse({"messages": [serialise(m) for m in saved]})
    return redirect("assistant:chat")


def _action_answer(request, action, wants_json):
    messages_after = [serialise(m) for m in action.conversation.messages.filter(sender=SenderType.AI).order_by("-id")[:1]]
    return JsonResponse({"ok": True, "status": action.status, "messages": messages_after, "action": action.pk})


@require_POST
@login_required
def act(request, pk, verb):
    """Approve or cancel a change the Agent proposed. Only the person it was shown to can."""
    try:
        action = actions.approve(request.user, pk, request=request) if verb == "approve" else actions.cancel(request.user, pk, request=request)
    except ServiceError as exc:
        if request.headers.get("X-Requested-With") == "fetch":
            return JsonResponse({"error": exc.message}, status=exc.status_code if 400 <= exc.status_code < 600 else 400)
        messages.error(request, exc.message)
        return redirect("assistant:chat")
    if request.headers.get("X-Requested-With") == "fetch":
        return _action_answer(request, action, True)
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
        messages.success(request, live.describe(out))
    if request.headers.get("X-Requested-With") == "fetch":
        return JsonResponse({"ok": True})
    return redirect("assistant:chat")


@login_required
def poll(request):
    """What is new in the open chat since message ``after``: the team's replies and notes (the client's own messages and the assistant's answers come back with the send)."""
    client = _client(request.user)
    if client is None:
        raise PermissionDenied
    conversation = agent.current_conversation(request.user, client, create=False)
    if conversation is None:
        return JsonResponse({"status": "closed", "messages": []})
    try:
        after = int(request.GET.get("after", 0))
    except ValueError:
        after = 0
    new = conversation.messages.filter(id__gt=after, sender__in=[SenderType.STAFF, SenderType.EVENT]).select_related("staff")
    return JsonResponse({"status": conversation.status, "messages": [serialise(m) for m in new if m.sender == SenderType.STAFF or (m.tool_call or {}).get("kind")]})


@require_POST
@login_required
def ticket_instead(request):
    client = _client(request.user)
    if client is None:
        raise PermissionDenied
    conversation = agent.current_conversation(request.user, client)
    try:
        ticket = live.ticket_instead(request.user, conversation, request=request)
    except ServiceError as exc:
        messages.error(request, exc.message)
    else:
        messages.success(request, f"Done: ticket {ticket.reference}. Our team will reply there and by email.")
        if request.headers.get("X-Requested-With") == "fetch":
            return JsonResponse({"ok": True, "url": reverse("support_customer:ticket", args=[ticket.pk])})
        return redirect("support_customer:ticket", pk=ticket.pk)
    if request.headers.get("X-Requested-With") == "fetch":
        return JsonResponse({"ok": False})
    return redirect("assistant:chat")


@require_POST
@login_required
def feedback(request, pk, vote):
    """The customer's thumb on an answer."""
    message = Message.objects.filter(pk=pk, sender=SenderType.AI, conversation__user=request.user).first()
    if message is None or vote not in ("up", "down"):
        raise Http404
    message.feedback = 1 if vote == "up" else -1
    message.save(update_fields=["feedback", "updated_at"])
    return JsonResponse({"ok": True, "vote": vote})


@require_POST
@login_required
def new_chat(request):
    client = _client(request.user)
    if client is None:
        raise PermissionDenied
    Conversation.objects.filter(user=request.user, client=client).exclude(status=ConversationStatus.CLOSED).update(status=ConversationStatus.CLOSED, updated_at=timezone.now())
    if request.headers.get("X-Requested-With") == "fetch":
        return JsonResponse({"ok": True})
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
            hours = int(request.POST.get("change_hours") or 0)
            if not model or len(model) > 80 or any(c.isspace() for c in model):
                raise ServiceError("Write the model name as Claude's documentation gives it, for example claude-sonnet-5-5.", code="model_invalid")
            if not 1 <= limit <= 500:
                raise ServiceError("The daily limit is a number from 1 to 500.", code="limit_invalid")
            if not 1 <= hours <= 168:
                raise ServiceError("The hours for a change are a number from 1 to 168.", code="hours_invalid")
            try:
                opens = dt.datetime.strptime(request.POST.get("hours_start") or config.hours_start.strftime("%H:%M"), "%H:%M").time()
                closes = dt.datetime.strptime(request.POST.get("hours_end") or config.hours_end.strftime("%H:%M"), "%H:%M").time()
                zone = (request.POST.get("hours_timezone") or config.hours_timezone).strip()
                ZoneInfo(zone)
            except (ValueError, ZoneInfoNotFoundError):
                raise ServiceError("Write the hours as HH:MM and a time zone such as Asia/Karachi.", code="working_hours_invalid") from None
            if closes <= opens:
                raise ServiceError("The closing time must be after the opening time.", code="hours_order")
        except (ServiceError, ValueError) as exc:
            messages.error(request, getattr(exc, "message", "The daily limit is a number from 1 to 500."))
        else:
            config.enabled, config.model, config.daily_limit, config.change_hours = request.POST.get("enabled") == "on", model, limit, hours
            config.mode = "claude" if request.POST.get("mode") == "claude" else "knowledge"
            config.changes_enabled = request.POST.get("changes_enabled") == "on"
            config.offline_ticket = request.POST.get("offline_ticket") == "on"
            config.whatsapp_number = re.sub(r"\D", "", request.POST.get("whatsapp_number", ""))[:20]
            config.hours_enabled = request.POST.get("hours_enabled") == "on"
            config.hours_start, config.hours_end, config.hours_timezone = opens, closes, zone
            config.hours_days = ",".join(d for d in "0123456" if request.POST.get(f"day_{d}") == "on")
            config.save()
            audit.record("assistant.settings_saved", actor=request.user, target=config, metadata={"enabled": config.enabled, "mode": config.mode, "model": model, "daily_limit": limit}, request=request)
            messages.success(request, "The assistant settings were saved.")
        return redirect("assistant_staff:settings")
    start = timezone.now().replace(hour=0, minute=0, second=0, microsecond=0)
    return render(request, "assistant/settings.html", {
        "config": config, "can_manage": can_manage, "key_set": bool(agent.api_key()), "today": Message.objects.filter(sender=SenderType.CLIENT, created_at__gte=start).count(),
        "chats": Conversation.objects.select_related("client", "user", "ticket").order_by("-updated_at")[:25],
        "entries": KnowledgeEntry.objects.filter(is_active=True).count(), "gaps": Unanswered.objects.count(),
        "days": [(str(i), name, str(i) in config.hours_days.split(",")) for i, name in enumerate(live.DAYS)], "online_now": len(live.staff_online())})


@login_required
def staff_analytics(request):
    _staff(request)
    from . import analytics

    try:
        days = int(request.GET.get("days", 7))
    except ValueError:
        days = 7
    days = days if days in (7, 30, 90) else 7
    return render(request, "assistant/analytics.html", {"r": analytics.report(days), "choices": (7, 30, 90)})


@login_required
def staff_chat(request, pk):
    _staff(request)
    conversation = get_object_or_404(Conversation.objects.select_related("client", "user", "ticket"), pk=pk)
    if request.user.has_perm(perm("view_support")):  # the live chat page shows the same record and lets support take, reply and close
        return redirect("livechat:detail", pk=conversation.pk)
    return render(request, "assistant/transcript.html", {"conversation": conversation, "rows": [{"sender": m.sender, "body": m.body, "tool": (m.tool_call or {}), "at": m.created_at}
                                                                                              for m in conversation.messages.all()]})


# --- the security check page: the code goes here, never into the chat ---------------------------------------------------------------------------

def _verify_state(request, pk, error="", done=None):
    try:
        action = actions._own(request.user, pk)
    except ServiceError:
        raise Http404 from None
    vc = getattr(action, "verification", None)
    return render(request, "assistant/verify.html", {
        "action": action, "vc": vc, "error": error, "done": done, "expires_ms": int(vc.expires_at.timestamp() * 1000) if vc else 0,
        "resend_ms": int((vc.last_sent_at + actions.RESEND_AFTER).timestamp() * 1000) if vc else 0,
        "can_try": action.status == "pending" and action.needs == "code" and vc is not None and timezone.now() <= vc.expires_at and vc.attempts < actions.MAX_ATTEMPTS})


@login_required
def verify(request, pk):
    if request.method == "POST":
        try:
            done = actions.verify(request.user, pk, request.POST.get("code", ""), request=request)
        except ServiceError as exc:
            return _verify_state(request, pk, error=exc.message)
        return _verify_state(request, pk, done=done)
    return _verify_state(request, pk)


@require_POST
@login_required
def verify_resend(request, pk):
    try:
        actions.resend_code(request.user, pk)
        messages.success(request, "A new code was sent to your email.")
    except ServiceError as exc:
        messages.error(request, exc.message)
    return redirect("assistant:verify", pk=pk)


# --- a callback at a time that suits the customer --------------------------------------------------------------------------------------------------

PHONE = re.compile(r"^[0-9+()\s-]{7,20}$")


@login_required
def callback(request):
    client = _client(request.user)
    if client is None:
        return render(request, "assistant/unavailable.html")
    values = {"phone": client.phone, "when": "", "note": ""}
    if request.method == "POST":
        values = {"phone": request.POST.get("phone", "").strip(), "when": request.POST.get("when", ""), "note": request.POST.get("note", "").strip()[:300]}
        try:
            if not PHONE.match(values["phone"]):
                raise ServiceError("Write a phone number we can call, for example +92 300 1234567.", code="phone_invalid")
            try:
                when = dt.datetime.strptime(values["when"], "%Y-%m-%dT%H:%M")
            except ValueError:
                raise ServiceError("Choose the day and time for the call.", code="when_invalid") from None
            when = timezone.make_aware(when, timezone.get_current_timezone()) if timezone.is_naive(when) else when
            if when < timezone.now() or when > timezone.now() + dt.timedelta(days=14):
                raise ServiceError("Choose a time in the next 14 days.", code="when_range")
            if CallbackRequest.objects.filter(user=request.user, status="requested").count() >= 3:
                raise ServiceError("You already have three callbacks waiting. We will call you soon.", code="too_many")
        except ServiceError as exc:
            messages.error(request, exc.message)
        else:
            conversation = agent.current_conversation(request.user, client, create=False)
            row = CallbackRequest.objects.create(client=client, user=request.user, conversation=conversation, phone=values["phone"], preferred_at=when, note=values["note"])
            audit.record("assistant.callback_requested", actor=request.user, target=row, metadata={"at": when.isoformat()}, request=request)
            from apps.notifications import services as notifications

            notifications.notify_team("assistant.callback", "manage_support", exclude=request.user, title=f"{client} asked for a callback at {timezone.localtime(when):%a %d %b %H:%M}", body=values["note"], link="/staff/chats/callbacks/")
            messages.success(request, f"Done. We will call {values['phone']} around {timezone.localtime(when):%A %d %B, %H:%M}.")
            return redirect("assistant:chat")
    mine = CallbackRequest.objects.filter(user=request.user, status="requested")
    return render(request, "assistant/callback.html", {"v": values, "mine": mine, "soonest": timezone.localtime().strftime("%Y-%m-%dT%H:%M")})
