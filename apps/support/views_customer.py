"""Customer support pages: their tickets, and the authenticated attachment download (also used by staff)."""
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from apps.clients.models import Client
from apps.core import portal
from apps.core.exceptions import ServiceError
from apps.core.web import ACTION_ERRORS, apply_form_error, error_text

from . import forms, services
from .models import TicketAttachment, TicketStatus


def _own_ticket(request, pk):
    return get_object_or_404(services.visible_tickets_for_user(request.user), pk=pk)


@login_required
def ticket_list(request):
    tickets = services.visible_tickets_for_user(request.user)
    legacy = {"open": "active", "closed": "closed"}.get(request.GET.get("view", ""))  # the older ?view=open|closed
    choices = [("active", "Open"), (TicketStatus.AGENT_REPLY, "Answered"), (TicketStatus.CUSTOMER_REPLY, "Your reply sent"),
               (TicketStatus.PENDING, "Pending"), (TicketStatus.RESOLVED, "Resolved"), (TicketStatus.CLOSED, "Closed"),
               ("all", "All tickets")]

    def apply(rows, value):
        if value == "active":
            return rows.exclude(status=TicketStatus.CLOSED)
        return rows if value == "all" else rows.filter(status=value)

    tickets, view_panel = portal.status_filter(
        request, tickets, choices, url_name="support_customer:list", all_label=None, apply=apply, default="active",
        current=request.GET.get("status") or legacy)
    page = Paginator(tickets.order_by("-last_activity_at", "-id"), 25).get_page(request.GET.get("page"))
    return render(request, "support/customer/list.html", {"page": page, "sidebar": [view_panel]})


@login_required
def ticket_new(request):
    clients = list(Client.objects.filter(contacts__user=request.user).distinct())
    if not clients:
        return render(request, "support/customer/unavailable.html")
    form = forms.NewTicketForm(request.POST or None, request.FILES or None, clients=clients)
    if request.method == "POST" and form.is_valid():
        data = form.cleaned_data
        related = data["hosting_account"] or data["domain"]
        client = related.client if related else (clients[0] if len(clients) == 1 else None)
        try:
            if client is None:
                raise ServiceError("Pick a related hosting account or domain so we know which account this is for.",
                                   code="client_required")
            ticket = services.open_ticket(
                request.user, client, department=data["department"], subject=data["subject"], body=data["body"],
                priority=data["priority"], hosting_account=data["hosting_account"], domain=data["domain"],
                files=data["files"], request=request)
        except ACTION_ERRORS as exc:
            apply_form_error(form, exc)
        else:
            messages.success(request, f"Ticket {ticket.reference} opened. We will reply as soon as we can.")
            return redirect("support_customer:ticket", pk=ticket.pk)
    return render(request, "support/customer/new.html", {"form": form})


@login_required
def ticket_detail(request, pk):
    ticket = _own_ticket(request, pk)
    form = forms.ReplyForm(request.POST or None, request.FILES or None)
    if request.method == "POST" and form.is_valid():
        try:
            services.reply(request.user, ticket, form.cleaned_data["body"], files=form.cleaned_data["files"],
                           request=request)
        except ACTION_ERRORS as exc:
            apply_form_error(form, exc)
        else:
            messages.success(request, "Your reply has been sent.")
            return redirect("support_customer:ticket", pk=ticket.pk)
    return render(request, "support/customer/detail.html", {
        "ticket": ticket, "messages_": services.messages_for(request.user, ticket), "form": form,
        "can_reply": services.customer_can_reply(ticket)})


@require_POST
@login_required
def ticket_close(request, pk):
    ticket = _own_ticket(request, pk)
    try:
        services.set_status(request.user, ticket, TicketStatus.CLOSED, reason="closed by customer", request=request)
    except ACTION_ERRORS as exc:
        messages.error(request, error_text(exc))
    else:
        messages.success(request, "Ticket closed. Reply to it any time in the next week to reopen it.")
    return redirect("support_customer:ticket", pk=pk)


@login_required
def attachment(request, pk):
    """Download an attachment: only someone allowed to read its message, and always as a download, never inline."""
    item = get_object_or_404(TicketAttachment.objects.select_related("message__ticket__client"), pk=pk)
    if not services.can_view_attachment(request.user, item):
        raise Http404
    try:
        handle = item.file.open("rb")
    except (FileNotFoundError, ValueError):
        raise Http404
    response = FileResponse(handle, as_attachment=True, filename=item.original_name, content_type=item.content_type)
    response["X-Content-Type-Options"] = "nosniff"
    response["Content-Security-Policy"] = "default-src 'none'; sandbox"
    response["Cache-Control"] = "private, no-store"
    return response

