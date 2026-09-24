from django.contrib.auth.signals import user_logged_in, user_logged_out, user_login_failed
from django.dispatch import receiver

from apps.audit import services as audit

from . import services


@receiver(user_logged_in, dispatch_uid="accounts.audit_login")
def audit_session_login(sender, request, user, **kwargs):
    channel = "admin" if request is not None and request.path.startswith("/admin/") else "web"
    services.record_login(user, request=request, channel=channel)


@receiver(user_logged_out, dispatch_uid="accounts.audit_logout")
def audit_session_logout(sender, request, user, **kwargs):
    if user is not None:
        audit.record("auth.logout", actor=user, target=user, metadata={"channel": "web"}, request=request)


@receiver(user_login_failed, dispatch_uid="accounts.audit_login_failed")
def audit_login_failed(sender, credentials, request=None, **kwargs):
    services.record_failed_login(credentials.get("username") or credentials.get("email"), request=request)
