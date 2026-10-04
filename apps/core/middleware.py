import re
import uuid

from django.http import HttpResponse
from django.template.loader import render_to_string
from django.urls import reverse

from .request_context import request_id_var

REQUEST_ID_HEADER = "X-Request-ID"
_VALID_ID = re.compile(r"^[A-Za-z0-9._-]{1,64}$")


class RequestIDMiddleware:
    """Assigns a correlation ID to every request and echoes it in the response."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        incoming = request.headers.get(REQUEST_ID_HEADER, "")
        request_id = incoming if _VALID_ID.match(incoming) else uuid.uuid4().hex
        request.request_id = request_id
        token = request_id_var.set(request_id)
        try:
            response = self.get_response(request)
        finally:
            request_id_var.reset(token)
        response[REQUEST_ID_HEADER] = request_id
        return response


class StripNullBytesMiddleware:
    """
    Remove NUL (0x00) characters from query strings and submitted forms before any view sees them.

    PostgreSQL refuses text containing a NUL byte, so ``?q=%00`` or a form field holding one turned a search box into a
    server error (SQLite quietly accepted it, which hid the problem). Django's form fields already reject NUL; this
    covers the raw ``request.GET`` / ``request.POST`` values that views hand to the database directly. API bodies are
    left alone (the serializers reject NUL themselves).
    """

    FORM_TYPES = ("application/x-www-form-urlencoded", "multipart/form-data")

    def __init__(self, get_response):
        self.get_response = get_response

    @staticmethod
    def _cleaned(querydict):
        if not any("\x00" in key or any("\x00" in v for v in values) for key, values in querydict.lists()):
            return querydict
        copy = querydict.__class__("", mutable=True, encoding=querydict.encoding)
        for key, values in querydict.lists():
            copy.setlist(key.replace("\x00", ""), [v.replace("\x00", "") for v in values])
        copy._mutable = False
        return copy

    def __call__(self, request):
        request.GET = self._cleaned(request.GET)
        if (request.method in ("POST", "PUT", "PATCH") and not request.path.startswith("/api/")
                and request.content_type in self.FORM_TYPES):
            request.POST = self._cleaned(request.POST)
        return self.get_response(request)


class SecurityHeadersMiddleware:
    """
    Two headers Django has no setting for (Phase 16): a Content Security Policy and a Permissions Policy, on every
    response that does not already carry its own (an uploaded file, an internal preview, already sets a tighter one -
    see apps.branding.views._image and apps.support.views_customer.attachment - and is left alone). Every script and
    stylesheet the portal serves is its own (no CDN, no inline script anywhere - see apps/core/test_ui.py), so the
    policy can be strict without breaking anything: no inline or third-party script, no framing, no browser feature
    a hosting portal has no reason to ask for.
    """

    CSP = ("default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; "
          "font-src 'self'; connect-src 'self'; object-src 'none'; base-uri 'self'; form-action 'self'; "
          "frame-ancestors 'none'")
    PERMISSIONS_POLICY = "camera=(), microphone=(), geolocation=(), payment=(), usb=(), interest-cohort=()"

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        if not response.has_header("Content-Security-Policy"):
            response["Content-Security-Policy"] = self.CSP
        if not response.has_header("Permissions-Policy"):
            response["Permissions-Policy"] = self.PERMISSIONS_POLICY
        return response


class MaintenanceModeMiddleware:
    """
    Shows the dependency-free maintenance page (Phase 15's "maintenance mode toggle") for a web page while
    ``BrandSettings.maintenance_mode`` is on. Exempt, so the switch is never locked behind itself: a signed-in staff
    member (the same view either way - no second staff-only site), the sign-in page and the few brand/static assets
    it needs to render styled, and the whole API (``/api/...``) - this is a customer-facing "closed for maintenance"
    notice for the web site (Admin Operations, Phase 15), not an infrastructure-level outage gate; a storefront
    integration or a future mobile client has no reason to go down because staff flipped it on. A harder gate for
    when the app itself cannot safely be reached at all (e.g. mid-deploy) is `deploy/nginx/whpanel.conf`'s own flag
    file, a separate, lower-level mechanism this middleware does not depend on.

    Placed last in ``MIDDLEWARE`` so every other cross-cutting concern (security headers, CSRF, sessions) still
    applies normally to both the maintenance response and the ordinary one.
    """

    EXEMPT_PREFIXES = ("/api/", "/static/")

    def __init__(self, get_response):
        self.get_response = get_response

    @staticmethod
    def _exempt_paths():
        return {reverse("accounts:login"), reverse("brand_css"), reverse("brand_logo"), reverse("brand_favicon"),
                reverse("brand_logo_dark"), reverse("brand_mark_dark")}

    def __call__(self, request):
        from apps.branding.services import get as get_brand

        if not get_brand().maintenance_mode:
            return self.get_response(request)
        if request.path.startswith(self.EXEMPT_PREFIXES) or request.path in self._exempt_paths():
            return self.get_response(request)
        user = getattr(request, "user", None)
        if user is not None and user.is_authenticated and user.is_staff:
            return self.get_response(request)
        return HttpResponse(render_to_string("maintenance.html"), status=503, content_type="text/html")
