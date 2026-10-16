"""Public pages: /p/<slug>/ and /contact/, and the staff screens that write them (Setup > Pages)."""
from django.contrib import messages
from django.contrib.auth.views import redirect_to_login
from django.core.exceptions import PermissionDenied
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from apps.accounts.roles import perm
from apps.core.web import ACTION_ERRORS

from . import pages, services
from .models import PublicPage


def public_page(request, slug):
    page = get_object_or_404(PublicPage, slug=slug)
    if not page.is_published and not (request.user.is_authenticated and request.user.has_perm(perm("view_settings"))):
        raise Http404
    return render(request, "public/page.html", {"page": page, "body": pages.render_body(page.body), "draft": not page.is_published})


def contact(request):
    """The built-in Contact page: the details already entered in Setup, and the quickest way to a person."""
    from apps.billing.models import BillingSettings

    try:
        row = BillingSettings.load()
        phone, address = row.phone, row.address
    except Exception:  # noqa: BLE001
        phone = address = ""
    return render(request, "public/contact.html", {"email": services.get().support_email, "phone": phone, "address": address})


def _guard(request, manage=False):
    if not request.user.is_authenticated:
        return redirect_to_login(request.get_full_path())
    if not request.user.has_perm(perm("manage_settings" if manage else "view_settings")):
        raise PermissionDenied
    return None


def staff_pages(request):
    denied = _guard(request)
    if denied:
        return denied
    return render(request, "branding/pages.html", {"pages": PublicPage.objects.all(), "can_manage": request.user.has_perm(perm("manage_settings"))})


def staff_page_edit(request, pk=None):
    denied = _guard(request)
    if denied:
        return denied
    can_manage = request.user.has_perm(perm("manage_settings"))
    page = get_object_or_404(PublicPage, pk=pk) if pk else None
    values = None
    if request.method == "POST":
        if not can_manage:
            raise PermissionDenied
        values = request.POST
        try:
            saved = pages.save(request.user, page, {**request.POST.dict(), "is_published": request.POST.get("is_published") == "on", "in_footer": request.POST.get("in_footer") == "on"},
                               request=request)
        except ACTION_ERRORS as exc:
            messages.error(request, "; ".join(exc.messages) if hasattr(exc, "messages") else str(getattr(exc, "message", exc)))
        else:
            messages.success(request, "The page was saved.")
            return redirect("brand_staff:page", pk=saved.pk)
    shown = {"slug": values.get("slug", "") if values else (page.slug if page else ""), "title": values.get("title", "") if values else (page.title if page else ""),
             "body": values.get("body", "") if values else (page.body if page else ""), "sort_order": values.get("sort_order", "") if values else (page.sort_order if page else 100),
             "is_published": (values.get("is_published") == "on") if values else (page.is_published if page else False),
             "in_footer": (values.get("in_footer") == "on") if values else (page.in_footer if page else True)}
    return render(request, "branding/page_form.html", {"page": page, "v": shown, "can_manage": can_manage})


@require_POST
def staff_page_delete(request, pk):
    denied = _guard(request, manage=True)
    if denied:
        return denied
    pages.delete(request.user, get_object_or_404(PublicPage, pk=pk), request=request)
    messages.success(request, "The page was deleted.")
    return redirect("brand_staff:pages")
