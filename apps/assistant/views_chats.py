"""The staff live-chat dashboard: the list with its tabs, one chat with its history, and the actions (take, reply, hand back, pass on, close, make a ticket)."""
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from apps.accounts.models import User
from apps.core.exceptions import ServiceError
from apps.hosting.models import HostingAccount
from apps.support import services as support

from . import live
from .models import Conversation, ConversationStatus, Message, SenderType

TABS = [("all", "All"), ("waiting", "Waiting"), ("ai", "AI"), ("with_staff", "With staff"), ("closed", "Closed")]


def _see(request):
    if not support.can_view_all(request.user):
        raise PermissionDenied


def serialise(m):
    return {"id": m.id, "sender": m.sender, "body": m.body, "tool": (m.tool_call or {}).get("tool", ""), "kind": (m.tool_call or {}).get("kind", ""),
            "action": (m.tool_call or {}).get("action"), "state": (m.tool_call or {}).get("status", ""), "needs": (m.tool_call or {}).get("needs", "approve"),
            "who": ((m.staff.get_full_name() or m.staff.email.split("@")[0]) if m.staff_id else ""), "at": m.created_at.isoformat()}


@login_required
def chat_list(request):
    _see(request)
    tab = request.GET.get("tab", "all")
    term = (request.GET.get("q") or "").strip()
    rows = Conversation.objects.select_related("client", "user", "assigned_to").order_by("-updated_at")
    if tab in ConversationStatus.values:
        rows = rows.filter(status=tab)
    if term:
        owners = HostingAccount.objects.filter(domain__icontains=term).values_list("client_id", flat=True)
        from django.db.models import Q

        rows = rows.filter(Q(client__first_name__icontains=term) | Q(client__last_name__icontains=term) | Q(client__company_name__icontains=term) | Q(client_id__in=owners))
    rows = list(rows[:60])
    for row in rows:
        row.last = row.messages.exclude(sender=SenderType.EVENT).order_by("-id").first()
        row.domain = HostingAccount.objects.filter(client=row.client).values_list("domain", flat=True).first() or ""
    counts = live.counts()
    return render(request, "assistant/chats.html", {"rows": rows, "tab": tab, "q": term, "tabs": [(k, label, counts.get(k, 0)) for k, label in TABS], "waiting": counts.get("waiting", 0),
                                                    "can_act": support.can_manage(request.user)})


@login_required
def chat_detail(request, pk):
    _see(request)
    conversation = get_object_or_404(Conversation.objects.select_related("client", "user", "assigned_to", "ticket"), pk=pk)
    rows = list(conversation.messages.select_related("staff").all())
    accounts = list(HostingAccount.objects.filter(client=conversation.client).select_related("product")[:5])
    mine = conversation.status == ConversationStatus.WITH_STAFF and conversation.assigned_to_id == request.user.pk
    colleagues = [u for u in User.objects.filter(is_active=True).order_by("email") if support.can_manage(u) and u.pk != request.user.pk][:30] if mine else []
    return render(request, "assistant/chat_detail.html", {
        "c": conversation, "rows": rows, "last_id": rows[-1].id if rows else 0, "accounts": accounts, "mine": mine, "can_act": support.can_manage(request.user), "colleagues": colleagues,
        "waited": (timezone.now() - conversation.handoff_requested_at) if conversation.handoff_requested_at and conversation.status == ConversationStatus.WAITING else None})


@login_required
def chat_poll(request, pk):
    _see(request)
    conversation = get_object_or_404(Conversation, pk=pk)
    try:
        after = int(request.GET.get("after", 0))
    except ValueError:
        after = 0
    new = conversation.messages.filter(id__gt=after).select_related("staff")
    return JsonResponse({"status": conversation.status, "assigned": conversation.assigned_to_id, "messages": [serialise(m) for m in new]})


@require_POST
@login_required
def chat_action(request, pk):
    conversation = get_object_or_404(Conversation, pk=pk)
    action = request.POST.get("action", "")
    try:
        if action == "take":
            live.take(request.user, conversation, request=request)
        elif action == "reply":
            live.reply(request.user, conversation, request.POST.get("message", ""), request=request)
        elif action == "hand_back":
            live.hand_back(request.user, conversation, request=request)
            messages.success(request, "The assistant is answering again.")
        elif action == "close":
            live.close(request.user, conversation, request=request)
            messages.success(request, "The chat was closed.")
        elif action == "transfer":
            target = User.objects.filter(pk=request.POST.get("to") or 0, is_active=True).first()
            if target is None:
                raise ServiceError("Choose a colleague.", code="no_target")
            live.transfer(request.user, conversation, target, request=request)
            messages.success(request, "The chat was passed on.")
        elif action == "ticket":
            ticket = live.to_ticket(request.user, conversation, request=request)
            messages.success(request, f"The chat became ticket {ticket.reference}.")
        else:
            raise ServiceError("That action is not available.", code="unknown_action")
    except ServiceError as exc:
        messages.error(request, exc.message)
    return redirect("livechat:detail", pk=conversation.pk)
