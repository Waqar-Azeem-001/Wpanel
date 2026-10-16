"""Public brand assets and the staff brand settings page. Every rule lives in ``services``."""
from django.contrib import messages
from django.contrib.auth.views import redirect_to_login
from django.core.exceptions import PermissionDenied
from django.http import Http404, HttpResponse, HttpResponseNotModified
from django.shortcuts import redirect, render

from apps.accounts.roles import perm
from apps.core.web import ACTION_ERRORS, apply_form_error

from . import forms, services
from .models import BrandSettings

ASSET_CACHE = "public, max-age=3600"


def _cached(request, body, content_type, version, max_age):
    etag = f'"brand-{version}"'
    if request.headers.get("If-None-Match") == etag:
        response = HttpResponseNotModified()
    else:
        response = HttpResponse(body, content_type=content_type)
    response["ETag"] = etag
    response["Cache-Control"] = f"public, max-age={max_age}"
    response["X-Content-Type-Options"] = "nosniff"
    return response


def brand_css(request):
    """The design tokens for the current brand: the only place the brand colours reach the stylesheet."""
    b = services.get()
    css = (":root {\n"
           f"  --brand-primary: {b.primary};\n  --brand-primary-dark: {b.primary_dark};\n"
           f"  --brand-primary-rgb: {b.primary_rgb};\n  --brand-primary-light-rgb: {b.primary_light_rgb};\n"
           f"  --brand-primary-subtle: {b.primary_subtle};\n"
           f"  --brand-accent: {b.accent};\n  --brand-accent-text: {b.accent_text};\n}}\n")
    return _cached(request, css, "text/css; charset=utf-8", b.version, 300)


def _image(request, kind):
    found = services.image(kind)
    if found is None:
        raise Http404("No image has been uploaded.")
    data, content_type = found
    response = _cached(request, data, content_type, services.get().version, 3600)
    response["Content-Security-Policy"] = "default-src 'none'; sandbox"  # an uploaded file can never run as a page
    return response


def brand_logo(request):
    return _image(request, "logo")


def brand_favicon(request):
    return _image(request, "favicon")


def brand_logo_dark(request):
    return _image(request, "logo_dark")


def brand_mark_dark(request):
    return _image(request, "mark_dark")


def settings_page(request):
    if not request.user.is_authenticated:
        return redirect_to_login(request.get_full_path())
    if not request.user.has_perm(perm("view_settings")):
        raise PermissionDenied
    can_manage = request.user.has_perm(perm("manage_settings"))
    row = BrandSettings.load()
    form = forms.BrandForm(request.POST or None, request.FILES or None, instance=row)
    if request.method == "POST":
        if not can_manage:
            raise PermissionDenied
        if form.is_valid():
            data = form.cleaned_data
            try:
                services.save_settings(
                    request.user, values={k: data[k] for k in services.TEXT_FIELDS}, request=request,
                    logo=data["logo"].read() if data.get("logo") else None,
                    favicon=data["favicon"].read() if data.get("favicon") else None,
                    logo_dark=data["logo_dark"].read() if data.get("logo_dark") else None,
                    mark_dark=data["mark_dark"].read() if data.get("mark_dark") else None,
                    remove_logo=data.get("remove_logo"), remove_favicon=data.get("remove_favicon"),
                    remove_logo_dark=data.get("remove_logo_dark"), remove_mark_dark=data.get("remove_mark_dark"),
                    policy_links=posted_policy_links(request) if "policy_form" in request.POST else None,
                    social_links=posted_social_links(request) if "social_form" in request.POST else None)
            except ACTION_ERRORS as exc:
                apply_form_error(form, exc)
            else:
                messages.success(request, "Brand settings saved.")
                return redirect("brand_staff:settings")
    current = services.get()
    policy_rows = posted_policy_links(request) if request.method == "POST" and "policy_form" in request.POST else [
        {"title": title, "url": url} for title, url in current.policy_links]
    social_rows = posted_social_links(request) if request.method == "POST" and "social_form" in request.POST else [
        {"title": title, "url": url} for title, url, _icon in current.social_links]
    looks = [{"key": key, **look} for key, look in services.LOOKS.items()]
    return render(request, "branding/settings.html", {"form": form, "can_manage": can_manage, "current": current, "looks": looks,
                                                      "chosen_theme": form["theme"].value() or "custom",
                                                      "policy_rows": policy_rows, "social_rows": social_rows, "section": "brand"})


def posted_policy_links(request):
    """The policy rows as typed: [{"title", "url"}], the boxes repeating their names."""
    titles, urls = request.POST.getlist("policy_title"), request.POST.getlist("policy_url")
    return [{"title": title, "url": urls[i] if i < len(urls) else ""} for i, title in enumerate(titles)]


def posted_social_links(request):
    """The social link rows as typed: [{"title", "url"}]."""
    titles, urls = request.POST.getlist("social_title"), request.POST.getlist("social_url")
    return [{"title": title, "url": urls[i] if i < len(urls) else ""} for i, title in enumerate(titles)]


def _menu_rows():
    """Every entry of the customer menu with what a Super Admin changed: [{key, label, standard, indent, visible, order}]."""
    from apps.core import navigation
    from apps.products.models import CategoryKind, StoreCategory

    chosen = services.menu_settings("client")
    rows = []

    def add(key, standard, indent, position):
        label, visible, order = chosen.get(key, ("", True, None))
        rows.append({"key": key, "standard": standard, "label": label, "indent": indent, "visible": visible,
                     "order": order if order is not None else position * 10, "locked": key == "client.home"})

    tops = [e for e in navigation.ITEMS if "client" in e.areas and not e.parent and e.position == "main"
            and e.requires in ("", "client_contact")]
    for position, top in enumerate(tops):
        add(top.key, str(top.label).replace("{name}", "").strip() or "Overview", 0, position)
        kids = [c for c in navigation.ITEMS if c.parent == top.key and "client" in c.areas]
        for sub, child in enumerate(kids):
            add(child.key, str(child.label), 1, sub)
        if top.key == "client.services":
            for sub, category in enumerate(StoreCategory.objects.filter(is_active=True, kind=CategoryKind.PLANS).order_by("sort_order", "name"),
                                            start=len(kids)):
                add(f"client.services.cat.{category.slug}", f"{category.name} (store category)", 1, sub)
    return rows


def menu_page(request):
    """Super Admin: which entries of the customer menu are shown, what they are called and in what order."""
    if not request.user.is_authenticated:
        return redirect_to_login(request.get_full_path())
    if not request.user.has_perm(perm("view_settings")):
        raise PermissionDenied
    can_manage = request.user.has_perm(perm("manage_settings"))
    if request.method == "POST":
        if not can_manage:
            raise PermissionDenied
        try:
            if request.POST.get("do") == "reset":
                services.reset_menu(request.user, request=request)
                messages.success(request, "The customer menu is back to the standard.")
            else:
                rows = {}
                for row in _menu_rows():
                    key = row["key"]
                    raw_order = request.POST.get(f"order_{key}", "").strip()
                    order = int(raw_order) if raw_order.isdigit() and int(raw_order) < 32000 else None
                    standard_order = row["order"] if key not in services.menu_settings("client") else None
                    rows[key] = {"label": request.POST.get(f"label_{key}", ""), "visible": request.POST.get(f"visible_{key}") == "on",
                                 "order": None if order is not None and order == standard_order else order}
                services.save_menu(request.user, rows, request=request)
                messages.success(request, "The customer menu was saved.")
        except ACTION_ERRORS as exc:
            messages.error(request, str(getattr(exc, "message", exc)))
        return redirect("brand_staff:menu")
    return render(request, "branding/menu.html", {"rows": _menu_rows(), "can_manage": can_manage})
