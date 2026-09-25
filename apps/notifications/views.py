"""
Web pages for notifications: the in-app inbox and preferences (everyone signed in), the staff email log and its
statistics (``view_settings``), and the open-tracking pixel (public, unauthenticated by nature).
"""
import uuid

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db.models import Q
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_GET, require_POST

from apps.accounts.roles import perm
from apps.core.decorators import portal_permission_required
from apps.core.web import run_action

from . import events, services
from .models import EmailMessage, Notification

# A 1x1 transparent GIF.
PIXEL = (b"GIF89a\x01\x00\x01\x00\x80\x00\x00\x00\x00\x00\xff\xff\xff!\xf9\x04\x01\x00\x00\x00\x00,\x00\x00\x00\x00"
         b"\x01\x00\x01\x00\x00\x02\x02D\x01\x00;")


@require_GET
def open_pixel(request, token):
    """
    Record that an email's tracking image was fetched, and return the image. It reveals nothing: an unknown token gets
    the same picture as a real one. Remember this is a *signal* - image blocking hides opens, privacy proxies and link
    scanners create them.
    """
    if isinstance(token, uuid.UUID):
        services.record_open(token)
    response = HttpResponse(PIXEL, content_type="image/gif")
    response["Cache-Control"] = "no-store, no-cache, max-age=0"
    response["X-Robots-Tag"] = "noindex"
    return response


# --- The in-app inbox -----------------------------------------------------------------------------------------

@login_required
def inbox(request):
    show = request.GET.get("show", "all")
    queryset = Notification.objects.filter(user=request.user)
    if show == "unread":
        queryset = queryset.filter(read_at__isnull=True)
    page = Paginator(queryset, 25).get_page(request.GET.get("page"))
    return render(request, "notifications/inbox.html", {"page": page, "show": show,
                                                        "unread": services.unread_count(request.user)})


def _own_emails(user):
    """Emails sent to this person. A message carrying a secret (a set-password link) is never listed or shown."""
    return EmailMessage.objects.filter(Q(user=user) | Q(to_email__iexact=user.email), is_sensitive=False).order_by("-created_at", "-id")


@login_required
def email_history(request):
    page = Paginator(_own_emails(request.user), 25).get_page(request.GET.get("page"))
    return render(request, "notifications/email_history.html", {"page": page})


@login_required
def email_history_detail(request, pk):
    return render(request, "notifications/email_history_detail.html", {"message": get_object_or_404(_own_emails(request.user), pk=pk)})


@login_required
def open_notification(request, pk):
    """Mark one notification read and go to what it is about (only ever a path on this site)."""
    notification = get_object_or_404(Notification, pk=pk, user=request.user)
    services.mark_read(request.user, [notification.pk])
    return redirect(services.safe_link(notification.link))


@require_POST
@login_required
def mark_all_read(request):
    count = services.mark_read(request.user)
    messages.success(request, f"Marked {count} notification{'s' if count != 1 else ''} as read.")
    return redirect("notifications:inbox")


@login_required
def preferences(request):
    if request.method == "POST":
        choices = {}
        for row in services.preference_rows(request.user):
            category = row["category"]
            choices[category] = (request.POST.get(f"email_{category}") == "on",
                                 request.POST.get(f"in_app_{category}") == "on")

        def action():
            services.save_preferences(request.user, choices)
            return "Your notification preferences have been saved."

        return run_action(request, action, "notifications:preferences")
    essential = [event for event in events.EVENTS.values() if event.essential
                 and (event.category != events.Category.TEAM)]
    return render(request, "notifications/preferences.html", {
        "rows": services.preference_rows(request.user), "essential": essential,
        "category_labels": events.CATEGORY_LABELS})


# --- Staff: email log and statistics -----------------------------------------------------------------------------

@portal_permission_required(perm("view_settings"))
def email_overview(request):
    try:
        days = int(request.GET.get("days", 30))
    except ValueError:
        days = 30
    days = days if days in (7, 30, 90) else 30
    return render(request, "notifications/staff/overview.html", {
        "stats": services.email_stats(days=days), "days": days, "section": "overview",
        "failed": EmailMessage.objects.filter(status=EmailMessage.Status.FAILED).count()})


@portal_permission_required(perm("view_settings"))
def email_list(request):
    queryset = EmailMessage.objects.all()
    status = request.GET.get("status", "")
    event = request.GET.get("event", "")
    term = request.GET.get("q", "").strip()
    if status in EmailMessage.Status.values:
        queryset = queryset.filter(status=status)
    if event:
        queryset = queryset.filter(event=event)
    if term:
        queryset = queryset.filter(Q(to_email__icontains=term) | Q(subject__icontains=term))
    page = Paginator(queryset.order_by("-created_at", "-id"), 30).get_page(request.GET.get("page"))
    return render(request, "notifications/staff/emails.html", {
        "page": page, "status": status, "event": event, "q": term, "statuses": EmailMessage.Status.choices,
        "event_choices": [(e.key, e.label) for e in events.EVENTS.values() if e.email_template],
        "section": "emails"})


@portal_permission_required(perm("view_settings"))
def email_detail(request, pk):
    message = get_object_or_404(EmailMessage, pk=pk)
    secret_removed = message.is_sensitive and not message.sensitive_body
    return render(request, "notifications/staff/email_detail.html", {
        "message": message, "section": "emails", "secret_removed": secret_removed,
        "can_resend": (request.user.has_perm(perm("manage_settings")) and message.status != "sent"
                       and not secret_removed)})


@require_POST
@portal_permission_required(perm("manage_settings"))
def email_resend(request, pk):
    message = get_object_or_404(EmailMessage, pk=pk)

    def action():
        services.resend(request.user, message, request=request)
        return "The email has been queued to send again."

    return run_action(request, action, "notifications_staff:email", pk=pk)
