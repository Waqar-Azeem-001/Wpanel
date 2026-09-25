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
           f"  --brand-accent: {b.accent};\n}}\n")
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
                    remove_logo=data.get("remove_logo"), remove_favicon=data.get("remove_favicon"))
            except ACTION_ERRORS as exc:
                apply_form_error(form, exc)
            else:
                messages.success(request, "Brand settings saved.")
                return redirect("brand_staff:settings")
    return render(request, "branding/settings.html", {"form": form, "can_manage": can_manage,
                                                      "current": services.get(), "section": "brand"})
