"""
Signing in on a new device and keeping a person on one device at a time.

* A person's browser holds a device cookie. If it matches the one device on record, a correct password signs them in as before.
* Anything else (a new browser, another computer, a cleared cookie) is held at a second step: a six-digit code emailed to them.
  The code is kept hashed, lasts ten minutes, allows five tries (then a lock), and can be asked for again only every 30 seconds and
  five times an hour.
* When the code is right the new device becomes THE device, and every other session of that person is ended. A session that
  survives anyway (a request in flight) is signed out by ``SingleDeviceMiddleware`` because its token no longer matches.

Switch it off with ``SIGNIN_EMAIL_CODE = False`` (development and the test settings).
"""
import hashlib
import secrets
from datetime import datetime, timedelta

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.hashers import check_password, make_password
from django.contrib.sessions.models import Session
from django.utils import timezone
from rest_framework import status

from apps.audit import services as audit
from apps.core.exceptions import ServiceError
from apps.notifications import services as notifications

from . import services
from .models import EmailSignInCode, SignInDevice

DEVICE_COOKIE = "wp_device"
SESSION_KEY = "device_token"
PENDING_KEY = "signin_code_user_id"
PENDING_EXPIRES = "signin_code_expires"
PENDING_REMEMBER = "signin_code_remember"
PENDING_NEXT = "signin_code_next"
CODE_MINUTES = 10
MAX_TRIES = 5
RESEND_SECONDS = 30
MAX_PER_HOUR = 5
LOCK_THRESHOLD = 5
LOCK_MINUTES = 15
COOKIE_SECONDS = 60 * 60 * 24 * 365


def enabled():
    return bool(getattr(settings, "SIGNIN_EMAIL_CODE", False))


def _hash(token):
    return hashlib.sha256(token.encode()).hexdigest()


def _client_ip(request):
    from apps.core.utils import client_ip  # the real address (the proxy's X-Forwarded-For), not the proxy's own

    return client_ip(request)


def describe_device(request):
    """A readable device name from the browser's user-agent: "Chrome on Windows". Falls back to "an unknown device"."""
    agent = (request.META.get("HTTP_USER_AGENT", "") if request is not None else "") or ""
    browser = next((name for key, name in (("Edg/", "Edge"), ("OPR/", "Opera"), ("Firefox/", "Firefox"), ("Chrome/", "Chrome"), ("Safari/", "Safari")) if key in agent), "")
    system = next((name for key, name in (("Windows", "Windows"), ("Android", "Android"), ("iPhone", "iPhone"), ("iPad", "iPad"), ("Mac OS X", "macOS"), ("Linux", "Linux")) if key in agent), "")
    if browser and system:
        return f"{browser} on {system}"
    return browser or system or "an unknown device"


def is_trusted_device(request, user):
    """True when this browser's device cookie is the person's one device."""
    token = request.COOKIES.get(DEVICE_COOKIE, "")
    device = SignInDevice.objects.filter(user=user).first()
    return bool(token and device and secrets.compare_digest(device.token_hash, _hash(token)))


def needs_code(request, user):
    return enabled() and not is_trusted_device(request, user)


# --- the emailed code -----------------------------------------------------------------------------------

def start_pending(request, user, *, remember, next_url):
    request.session[PENDING_KEY] = user.pk
    request.session[PENDING_EXPIRES] = (timezone.now() + timedelta(minutes=CODE_MINUTES)).isoformat()
    request.session[PENDING_REMEMBER] = bool(remember)
    request.session[PENDING_NEXT] = next_url or ""


def pending_user(request):
    user_id = request.session.get(PENDING_KEY)
    expires = request.session.get(PENDING_EXPIRES)
    if not user_id or not expires:
        return None
    try:
        if timezone.now() > datetime.fromisoformat(expires):
            clear_pending(request)
            return None
    except ValueError:
        clear_pending(request)
        return None
    return get_user_model().objects.filter(pk=user_id, is_active=True).first()


def clear_pending(request):
    for key in (PENDING_KEY, PENDING_EXPIRES, PENDING_REMEMBER, PENDING_NEXT):
        request.session.pop(key, None)


def send_code(user, request=None, *, resend=False):
    """Email a fresh code. Raises a plain error when asked again too often (a "send again" click also waits 30 seconds)."""
    now = timezone.now()
    last = EmailSignInCode.objects.filter(user=user).first()
    if resend and last and now - last.created_at < timedelta(seconds=RESEND_SECONDS):
        raise ServiceError(f"A code was just sent. You can ask for another in {RESEND_SECONDS} seconds.", code="code_too_soon",
                           status_code=status.HTTP_429_TOO_MANY_REQUESTS)
    if EmailSignInCode.objects.filter(user=user, created_at__gte=now - timedelta(hours=1)).count() >= MAX_PER_HOUR:
        raise ServiceError("Too many codes were asked for. Try again in an hour.", code="code_limit", status_code=status.HTTP_429_TOO_MANY_REQUESTS)
    EmailSignInCode.objects.filter(user=user, used_at__isnull=True).update(used_at=now)  # an older code stops working
    code = f"{secrets.randbelow(1_000_000):06d}"
    EmailSignInCode.objects.create(user=user, code_hash=make_password(code), expires_at=now + timedelta(minutes=CODE_MINUTES))
    notifications.dispatch("account.signin_code", user=user, context={
        "code": code, "minutes": CODE_MINUTES, "ip": _client_ip(request) or "unknown", "device": describe_device(request),
        "when": timezone.localtime().strftime("%d %b %Y, %H:%M")})
    audit.record("auth.signin_code_sent", actor=user, target=user, request=request)


def verify_code(user, code, request=None):
    """True when the code is the latest, unspent, unexpired one. Wrong tries count towards a lock, quite apart from the password's."""
    if services.is_locked_out("signin_code", str(user.pk)):
        raise ServiceError("Too many wrong codes. Wait a few minutes and sign in again.", code="account_locked",
                           status_code=status.HTTP_429_TOO_MANY_REQUESTS)
    row = EmailSignInCode.objects.filter(user=user, used_at__isnull=True, expires_at__gt=timezone.now()).first()
    if row is None:
        return False
    row.tries += 1
    if row.tries >= MAX_TRIES:
        row.used_at = timezone.now()
    row.save(update_fields=["tries", "used_at"])
    typed = (code or "").strip().replace(" ", "")
    if typed and check_password(typed, row.code_hash) and row.tries <= MAX_TRIES:
        row.used_at = timezone.now()
        row.save(update_fields=["used_at"])
        services.clear_login_attempts("signin_code", str(user.pk))
        return True
    if services.register_failed_attempt("signin_code", str(user.pk), threshold=LOCK_THRESHOLD, lock_minutes=LOCK_MINUTES):
        audit.record("auth.signin_code_locked", actor=user, target=user, request=request)
    return False


# --- the one device -------------------------------------------------------------------------------------

def _end_other_sessions(user, keep_key):
    for session in Session.objects.filter(expire_date__gt=timezone.now()).iterator():
        if session.session_key == keep_key:
            continue
        try:
            if str(session.get_decoded().get("_auth_user_id")) == str(user.pk):
                session.delete()
        except Exception:  # an unreadable old session is not ours to worry about
            continue


def register_login(request, response, user):
    """After a successful sign-in: make this browser the person's one device (when it is not already) and sign everything else out."""
    if not enabled():
        return response
    if is_trusted_device(request, user):
        request.session[SESSION_KEY] = SignInDevice.objects.get(user=user).token_hash
        return response
    token = secrets.token_urlsafe(32)
    SignInDevice.objects.update_or_create(user=user, defaults={
        "token_hash": _hash(token), "user_agent": request.META.get("HTTP_USER_AGENT", "")[:200], "ip_address": _client_ip(request),
        "created_at": timezone.now()})
    request.session[SESSION_KEY] = _hash(token)
    request.session.save()
    _end_other_sessions(user, request.session.session_key)
    response.set_cookie(DEVICE_COOKIE, token, max_age=COOKIE_SECONDS, httponly=True, samesite="Lax", secure=request.is_secure())
    audit.record("auth.device_changed", actor=user, target=user, request=request)
    notifications.dispatch("account.new_device", user=user, context={
        "ip": _client_ip(request) or "unknown", "device": describe_device(request), "when": timezone.localtime().strftime("%d %b %Y, %H:%M")})
    return response


class SingleDeviceMiddleware:
    """Signs a session out when the person has since signed in on another device. Sessions made without a device token (the API, tests) are left alone."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if enabled() and request.user.is_authenticated:
            token = request.session.get(SESSION_KEY)
            if token:
                device = SignInDevice.objects.filter(user=request.user).values_list("token_hash", flat=True).first()
                if device != token:
                    from django.contrib import messages
                    from django.contrib.auth import logout
                    from django.shortcuts import redirect

                    logout(request)
                    messages.info(request, "You were signed out because your account was signed in on another device.")
                    return redirect("accounts:login")
        return self.get_response(request)
