"""
Two rules for staff, in one middleware:

* **Two-factor sign-in is required** to use the staff area (and the Django admin). A staff member who has not set it up is sent to the
  setup page; the pages they need to set it up (their own account pages) and to sign out stay open. Customers are not affected.
* **Idle sign-out.** A staff session that has not been used for ``STAFF_IDLE_TIMEOUT_MINUTES`` is ended, so a computer left open does not stay signed in.

Both are switches in the settings (``REQUIRE_STAFF_2FA``, ``STAFF_IDLE_TIMEOUT_MINUTES``; 0 turns the timeout off).
"""
import time

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import logout
from django.shortcuts import redirect
from django.urls import reverse

LAST_SEEN = "staff_last_seen"
WRITE_EVERY_SECONDS = 60  # do not write the session on every click


def _gated_prefixes():
    return ("/staff/", "/" + settings.ADMIN_URL.lstrip("/"))


class StaffSecurityMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user = request.user
        if user.is_authenticated and user.is_staff:
            limit = int(getattr(settings, "STAFF_IDLE_TIMEOUT_MINUTES", 0) or 0) * 60
            now = time.time()
            last = request.session.get(LAST_SEEN)
            if limit and last and now - last > limit:
                logout(request)
                messages.info(request, "You were signed out because you were away for a while. Please sign in again.")
                return redirect(f"{reverse('accounts:login')}?next={request.path}")
            if not last or now - last > WRITE_EVERY_SECONDS:
                request.session[LAST_SEEN] = now
            if getattr(settings, "REQUIRE_STAFF_2FA", False) and not user.mfa_enabled and request.path.startswith(_gated_prefixes()):
                messages.warning(request, "Staff must turn on two-factor sign-in before using the staff area. It takes a minute: scan the code with an authenticator app.")
                return redirect("accounts:mfa_setup")
        return self.get_response(request)
