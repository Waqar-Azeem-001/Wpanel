"""
Account business logic. API views and web views both call these functions;
neither contains account rules of its own.
"""
import logging

from django.conf import settings
from django.contrib.auth import authenticate, password_validation
from django.contrib.auth.models import Group
from django.contrib.auth.tokens import default_token_generator
from django.core import signing
from django.db import transaction
from django.urls import reverse
from django.utils import timezone
from django.utils.encoding import force_bytes, force_str
from django.utils.http import urlsafe_base64_decode, urlsafe_base64_encode
from rest_framework import status
from rest_framework_simplejwt.token_blacklist.models import BlacklistedToken, OutstandingToken
from rest_framework_simplejwt.tokens import RefreshToken

from apps.audit import services as audit
from apps.core.exceptions import ServiceError
from apps.notifications import services as notifications

from .models import AccountStatus, User
from .roles import PRIVILEGED_ROLES, STAFF_ROLES, Role, perm

logger = logging.getLogger(__name__)

EMAIL_VERIFICATION_SALT = "accounts.email-verification"


# --- Registration & verification ---------------------------------------------

@transaction.atomic
def register_user(*, email, password, first_name="", last_name="", phone="", company_name="", referral_code="",
                  request=None):
    email = User.objects.normalize_email(email).lower()
    if User.objects.filter(email__iexact=email).exists():
        raise ServiceError("An account with this email already exists.", code="email_taken")
    candidate = User(email=email, first_name=first_name, last_name=last_name)
    password_validation.validate_password(password, user=candidate)

    user = User.objects.create_user(
        email=email, password=password, first_name=first_name, last_name=last_name, phone=phone
    )
    sync_role_membership(user)
    audit.record("account.registered", actor=user, target=user, request=request)
    # Every self-registered customer gets their own client account, which later
    # holds their orders, services, domains and invoices.
    from apps.clients.services import create_client_for_registration

    client = create_client_for_registration(user, company_name=company_name, request=request)
    _attribute_referral(user, client, referral_code, request)
    send_verification_email(user)
    return user


def _attribute_referral(user, client, code, request):
    """Remember which affiliate sent this sign-up. A problem here must never stop someone registering."""
    if not code:
        return
    from apps.affiliates import services as affiliates

    try:
        with transaction.atomic():
            affiliates.attribute_signup(client, code, user=user, request=request)
    except Exception:  # noqa: BLE001
        logger.exception("Could not attribute the referral for %s", user.pk)


def make_email_verification_token(user):
    return signing.dumps({"uid": user.pk, "email": user.email}, salt=EMAIL_VERIFICATION_SALT)


def send_verification_email(user):
    if user.is_email_verified:
        return None
    token = make_email_verification_token(user)
    link = settings.SITE_URL.rstrip("/") + reverse("accounts:verify_email", args=[token])
    return notifications.dispatch("account.verification", user=user, context={"link": link}).message


def verify_email(token, request=None):
    try:
        data = signing.loads(token, salt=EMAIL_VERIFICATION_SALT, max_age=settings.EMAIL_VERIFICATION_MAX_AGE)
    except signing.SignatureExpired:
        raise ServiceError("This verification link has expired.", code="token_expired")
    except signing.BadSignature:
        raise ServiceError("This verification link is invalid.", code="token_invalid")

    user = User.objects.filter(pk=data.get("uid")).first()
    # A token issued for a previous email address must not verify a new one.
    if user is None or user.email != data.get("email"):
        raise ServiceError("This verification link is invalid.", code="token_invalid")
    if not user.is_email_verified:
        user.email_verified_at = timezone.now()
        user.save(update_fields=["email_verified_at", "updated_at"])
        audit.record("account.email_verified", actor=user, target=user, request=request)
    return user


# --- Authentication -------------------------------------------------------------

def authenticate_user(*, email, password, request=None):
    """
    Return the user for valid credentials, or raise a generic error.

    Failed attempts are audited by the ``user_login_failed`` signal receiver.
    """
    user = authenticate(request, username=(email or "").lower(), password=password)
    if user is None:
        raise ServiceError(
            "Invalid email or password.", code="invalid_credentials", status_code=status.HTTP_401_UNAUTHORIZED
        )
    return user


def record_failed_login(email, request=None):
    existing = User.objects.filter(email__iexact=email or "").first()
    reason = "unknown_email"
    if existing is not None:
        reason = "inactive_account" if not existing.is_active else "bad_password"
    audit.record(
        "auth.login_failed",
        target=existing,
        metadata={"email": (email or "")[:254], "reason": reason},
        request=request,
    )


def record_login(user, *, request=None, channel):
    audit.record("auth.login", actor=user, target=user, metadata={"channel": channel}, request=request)


def issue_tokens(user, *, request=None):
    """JWT pair for API/mobile clients."""
    refresh = RefreshToken.for_user(user)
    user.last_login = timezone.now()
    user.save(update_fields=["last_login"])
    record_login(user, request=request, channel="api")
    return {"access": str(refresh.access_token), "refresh": str(refresh)}


def revoke_refresh_token(user, refresh_token, *, request=None):
    try:
        token = RefreshToken(refresh_token)
    except Exception:
        raise ServiceError("Invalid refresh token.", code="token_invalid")
    if str(token.get("user_id")) != str(user.pk):
        raise ServiceError("Invalid refresh token.", code="token_invalid")
    token.blacklist()
    audit.record("auth.logout", actor=user, target=user, metadata={"channel": "api"}, request=request)


def revoke_all_tokens(user):
    """Blacklist every outstanding refresh token (password change/reset, suspension)."""
    for token in OutstandingToken.objects.filter(user=user):
        BlacklistedToken.objects.get_or_create(token=token)


# --- Passwords ------------------------------------------------------------------

def request_password_reset(email, request=None):
    """Send a reset link if an active account exists. Never reveals whether it does."""
    user = User.objects.filter(email__iexact=email or "", status=AccountStatus.ACTIVE).first()
    if user is None:
        return None
    uid = urlsafe_base64_encode(force_bytes(user.pk))
    token = default_token_generator.make_token(user)
    link = settings.SITE_URL.rstrip("/") + reverse("accounts:password_reset_confirm", args=[uid, token])
    audit.record("auth.password_reset_requested", target=user, request=request)
    return notifications.dispatch("account.password_reset", user=user,
                                  context={"link": link, "uid": uid, "token": token}).message


def get_user_for_reset(uidb64, token):
    try:
        user = User.objects.get(pk=force_str(urlsafe_base64_decode(uidb64)))
    except (TypeError, ValueError, OverflowError, User.DoesNotExist):
        return None
    if not user.is_active or not default_token_generator.check_token(user, token):
        return None
    return user


@transaction.atomic
def reset_password(uidb64, token, new_password, request=None):
    user = get_user_for_reset(uidb64, token)
    if user is None:
        raise ServiceError("This password reset link is invalid or has expired.", code="token_invalid")
    password_validation.validate_password(new_password, user=user)
    user.set_password(new_password)
    user.save(update_fields=["password", "updated_at"])
    revoke_all_tokens(user)
    audit.record("auth.password_reset", actor=user, target=user, request=request)
    return user


@transaction.atomic
def change_password(user, *, old_password, new_password, request=None):
    if not user.check_password(old_password):
        raise ServiceError("Current password is incorrect.", code="invalid_password")
    password_validation.validate_password(new_password, user=user)
    user.set_password(new_password)
    user.save(update_fields=["password", "updated_at"])
    revoke_all_tokens(user)
    audit.record("auth.password_changed", actor=user, target=user, request=request)
    return user


def _password_target(actor, user):
    """Who an admin may act on: someone else, and never a Super Admin's account (they change their own)."""
    if user.pk == actor.pk:
        raise ServiceError("Change your own password in your profile.", code="self_action")
    if user.is_superuser or user.role == Role.SUPER_ADMIN:
        raise ServiceError("A Super Admin's password can only be changed by that person.", code="permission_denied",
                           status_code=status.HTTP_403_FORBIDDEN)


@transaction.atomic
def admin_set_password(actor, user, new_password, *, request=None):
    """
    Super Admin only: give someone a new password (a customer who cannot use the reset link, say). It is checked against the
    password rules, the person is signed out everywhere, they are told by email that it changed (the email never contains the
    password), and the audit trail records who did it, never the password.
    """
    if not actor.is_superuser:
        raise ServiceError("Only a Super Admin can set another person's password.", code="permission_denied",
                           status_code=status.HTTP_403_FORBIDDEN)
    _password_target(actor, user)
    try:
        password_validation.validate_password(new_password or "", user=user)
    except Exception as exc:  # noqa: BLE001 - shown to the admin as the reason
        raise ServiceError(" ".join(getattr(exc, "messages", None) or [str(exc)]), code="weak_password")
    user.set_password(new_password)
    user.save(update_fields=["password", "updated_at"])
    revoke_all_tokens(user)
    audit.record("account.password_set_by_admin", actor=actor, target=user, request=request)
    notifications.dispatch("account.password_changed_by_admin", user=user, context={"by": actor.full_name or actor.email})
    return user


@transaction.atomic
def admin_send_reset_link(actor, user, *, request=None):
    """Send the person a fresh password-reset link (needs ``manage_users``, or ``manage_clients`` for a customer)."""
    allowed = actor.has_perm(perm("manage_users")) or (actor.has_perm(perm("manage_clients")) and user.role == Role.CUSTOMER)
    if not allowed:
        raise ServiceError("You do not have permission to perform this action.", code="permission_denied",
                           status_code=status.HTTP_403_FORBIDDEN)
    _password_target(actor, user)
    if user.role in PRIVILEGED_ROLES and not actor.is_superuser:
        raise ServiceError("Only a Super Admin can reset an admin account.", code="permission_denied",
                           status_code=status.HTTP_403_FORBIDDEN)
    if user.status != AccountStatus.ACTIVE:
        raise ServiceError("This account is not active, so it cannot receive a reset link. Reactivate it first.",
                           code="account_inactive")
    message = request_password_reset(user.email, request=request)
    audit.record("account.reset_link_sent", actor=actor, target=user, request=request)
    return message


# --- Profile ----------------------------------------------------------------------

PROFILE_FIELDS = ("first_name", "last_name", "phone")


def update_profile(user, *, request=None, **changes):
    changed = {}
    for field in PROFILE_FIELDS:
        if field in changes and getattr(user, field) != changes[field]:
            setattr(user, field, changes[field])
            changed[field] = changes[field]
    if changed:
        user.save(update_fields=[*changed, "updated_at"])
        audit.record("account.profile_updated", actor=user, target=user,
                     metadata={"fields": sorted(changed)}, request=request)
    return user


# --- Administration: status & roles ---------------------------------------------

def _require(actor, codename):
    if not actor.has_perm(perm(codename)):
        raise ServiceError("You do not have permission to perform this action.",
                           code="permission_denied", status_code=status.HTTP_403_FORBIDDEN)


@transaction.atomic
def set_account_status(actor, user, new_status, *, reason="", request=None):
    _require(actor, "manage_users")
    if new_status not in AccountStatus.values:
        raise ServiceError("Unknown account status.", code="invalid_status")
    if user.pk == actor.pk:
        raise ServiceError("You cannot change the status of your own account.", code="self_action")
    if user.role in PRIVILEGED_ROLES and not actor.is_superuser:
        raise ServiceError("Only a Super Admin can change the status of an admin account.",
                           code="permission_denied", status_code=status.HTTP_403_FORBIDDEN)
    previous = user.status
    if previous == new_status:
        return user
    user.status = new_status
    user.save(update_fields=["status", "updated_at"])
    if new_status != AccountStatus.ACTIVE:
        revoke_all_tokens(user)
    audit.record("account.status_changed", actor=actor, target=user,
                 metadata={"from": previous, "to": new_status, "reason": reason[:500]}, request=request)
    return user


@transaction.atomic
def update_user_details(actor, user, *, request=None, **changes):
    """
    Correct someone else's name or phone number from the Users screen (needs ``manage_users``; never your own account,
    which you change in your profile; an admin account only by a Super Admin). Email, role and status have their own
    audited actions.
    """
    _require(actor, "manage_users")
    if user.pk == actor.pk:
        raise ServiceError("Change your own details in your profile.", code="self_action")
    if user.role in PRIVILEGED_ROLES and not actor.is_superuser:
        raise ServiceError("Only a Super Admin can change an admin account.", code="permission_denied",
                           status_code=status.HTTP_403_FORBIDDEN)
    changed = {}
    for field in PROFILE_FIELDS:
        if field in changes and getattr(user, field) != changes[field]:
            setattr(user, field, changes[field])
            changed[field] = changes[field]
    if changed:
        user.save(update_fields=[*changed, "updated_at"])
        audit.record("account.details_updated", actor=actor, target=user, metadata={"fields": sorted(changed)},
                     request=request)
    return user


@transaction.atomic
def create_staff_user(actor, *, email, first_name="", last_name="", role, request=None):
    """
    Add a member of staff (needs ``manage_users`` and ``assign_roles``; admin roles only by a Super Admin). The new person
    has no password: they receive an email with a one-time link to set one.
    """
    from django.core.exceptions import ValidationError
    from django.core.validators import validate_email

    _require(actor, "manage_users")
    _require(actor, "assign_roles")
    email = (email or "").strip().lower()
    try:
        validate_email(email)
    except ValidationError:
        raise ServiceError("Enter a valid email address.", code="invalid_email")
    if role not in STAFF_ROLES:
        raise ServiceError("Choose a staff role.", code="invalid_role")
    if role in PRIVILEGED_ROLES and not actor.is_superuser:
        raise ServiceError("Only a Super Admin can create admin accounts.", code="permission_denied",
                           status_code=status.HTTP_403_FORBIDDEN)
    if User.objects.filter(email__iexact=email).exists():
        raise ServiceError("Someone with that email address already has an account.", code="email_taken")
    user = User.objects.create_user(email=email, password=None, first_name=first_name.strip(), last_name=last_name.strip(),
                                    role=role)
    sync_role_membership(user)
    audit.record("account.staff_created", actor=actor, target=user, metadata={"role": role}, request=request)
    uid = urlsafe_base64_encode(force_bytes(user.pk))
    link = settings.SITE_URL.rstrip("/") + reverse("accounts:password_reset_confirm",
                                                    args=[uid, default_token_generator.make_token(user)])
    notifications.dispatch("account.staff_welcome", user=user, context={"link": link, "role": Role(role).label})
    return user


def sync_role_membership(user):
    """Make the user's group membership match ``user.role`` (one role per user)."""
    group = Group.objects.filter(name=Role(user.role).label).first()
    if group is None:
        from .roles import sync_roles

        sync_roles()
        group = Group.objects.get(name=Role(user.role).label)
    user.groups.set([group])


@transaction.atomic
def assign_role(actor, user, role, *, request=None):
    _require(actor, "assign_roles")
    if role not in Role.values:
        raise ServiceError("Unknown role.", code="invalid_role")
    if user.pk == actor.pk:
        raise ServiceError("You cannot change your own role.", code="self_action")
    if (role in PRIVILEGED_ROLES or user.role in PRIVILEGED_ROLES) and not actor.is_superuser:
        raise ServiceError("Only a Super Admin can grant or revoke admin roles.",
                           code="permission_denied", status_code=status.HTTP_403_FORBIDDEN)
    previous = user.role
    if previous == role:
        return user
    user.role = role
    user.save(update_fields=["role", "updated_at"])
    sync_role_membership(user)
    revoke_all_tokens(user)
    audit.record("account.role_changed", actor=actor, target=user,
                 metadata={"from": previous, "to": role}, request=request)
    return user
