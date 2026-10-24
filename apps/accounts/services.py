"""
Account business logic. API views and web views both call these functions;
neither contains account rules of its own.
"""
import logging
from datetime import datetime

from django.conf import settings
from django.contrib.auth import authenticate, password_validation
from django.contrib.auth.hashers import check_password, make_password
from django.contrib.auth.models import Group
from django.contrib.auth.tokens import default_token_generator
from django.core import signing
from django.core.cache import cache
from django.db import IntegrityError, transaction
from django.urls import reverse
from django.utils import timezone
from django.utils.encoding import force_bytes, force_str
from django.utils.http import urlsafe_base64_decode, urlsafe_base64_encode
from rest_framework import status
from rest_framework_simplejwt.token_blacklist.models import BlacklistedToken, OutstandingToken
from rest_framework_simplejwt.tokens import RefreshToken

from apps.audit import services as audit
from apps.core import crypto
from apps.core.exceptions import ServiceError
from apps.notifications import services as notifications

from . import mfa as mfa_lib
from .models import AccountStatus, MFARecoveryCode, User
from .roles import ALL_CODENAMES, PORTAL_PERMISSIONS, PRIVILEGED_ROLES, ROLE_PERMISSIONS, STAFF_ROLES, Role, perm

logger = logging.getLogger(__name__)

EMAIL_VERIFICATION_SALT = "accounts.email-verification"


# --- Registration & verification ---------------------------------------------

def register_user(**kwargs):
    """Create an account. Two sign-ups with the same address at the same instant make one account; the other is told the address is taken."""
    try:
        return _register_user(**kwargs)
    except IntegrityError as exc:
        if "email" in str(exc).lower():
            raise ServiceError("An account with this email already exists.", code="email_taken") from exc
        raise


@transaction.atomic
def _register_user(*, email, password, first_name="", last_name="", phone="", company_name="", referral_code="",
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
    notifications.notify_team("client.new", "view_clients", exclude=user, title=f"New account: {user.email}",
                              body="\n".join(line for line in (
                                  f"Name: {client.display_name}", f"Email: {user.email}", f"Phone: {client.phone}" if client.phone else "",
                                  f"Company: {client.company_name}" if client.company_name else "") if line), link=reverse("clients_staff:detail", args=[client.pk]))
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

# Account lockout (beyond the per-IP/per-account rate limit the API already applies): after this many wrong passwords
# for the *same* email address (real or not - locking on the string typed, not on whether an account exists, means the
# behaviour never hints at which emails are registered), that email cannot be tried again for a while. A right password
# clears the count. Kept in the cache, not the database: it is meant to be forgotten, and must work the same on SQLite
# (dev) and PostgreSQL (production) without a migration.
LOGIN_LOCK_THRESHOLD = 5
LOGIN_LOCK_MINUTES = 15
LOGIN_ATTEMPT_WINDOW_MINUTES = 15


def _lock_keys(scope, subject):
    subject = (subject or "").strip().lower()
    return f"lockout:{scope}:attempts:{subject}", f"lockout:{scope}:locked:{subject}"


def is_locked_out(scope, subject):
    _attempts_key, locked_key = _lock_keys(scope, subject)
    return bool(cache.get(locked_key))


def register_failed_attempt(scope, subject, *, threshold=LOGIN_LOCK_THRESHOLD, lock_minutes=LOGIN_LOCK_MINUTES):
    """One more wrong try for ``subject`` in ``scope``. Returns True the moment this attempt causes a new lock."""
    attempts_key, locked_key = _lock_keys(scope, subject)
    count = (cache.get(attempts_key) or 0) + 1
    cache.set(attempts_key, count, timeout=LOGIN_ATTEMPT_WINDOW_MINUTES * 60)
    if count >= threshold:
        cache.set(locked_key, True, timeout=lock_minutes * 60)
        cache.delete(attempts_key)
        return True
    return False


def clear_login_attempts(scope, subject):
    for key in _lock_keys(scope, subject):
        cache.delete(key)


def authenticate_user(*, email, password, request=None):
    """
    Return the user for valid credentials, or raise a generic error.

    Failed attempts are audited by the ``user_login_failed`` signal receiver, which also counts towards the lockout
    above; a locked-out email is refused before the password is even checked.
    """
    email = (email or "").lower()
    if is_locked_out("login", email):
        raise ServiceError(
            "Too many failed attempts. Wait a few minutes and try again, or reset your password.",
            code="account_locked", status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        )
    user = authenticate(request, username=email, password=password)
    if user is None:
        raise ServiceError(
            "Invalid email or password.", code="invalid_credentials", status_code=status.HTTP_401_UNAUTHORIZED
        )
    clear_login_attempts("login", email)
    return user


def record_failed_login(email, request=None):
    existing = User.objects.filter(email__iexact=email or "").first()
    reason = "unknown_email"
    if existing is not None:
        reason = "inactive_account" if not existing.is_active else "bad_password"
    just_locked = register_failed_attempt("login", email)
    audit.record(
        "auth.login_failed",
        target=existing,
        metadata={"email": (email or "")[:254], "reason": reason},
        request=request,
    )
    if just_locked:
        audit.record("auth.account_locked", target=existing, metadata={"email": (email or "")[:254], "minutes": LOGIN_LOCK_MINUTES},
                     request=request)


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


def admin_setup_link(actor, user, *, request=None):
    """
    Super Admin only: a fresh one-time link for someone to choose their own password, returned to hand over yourself (when the email
    did not arrive, say). It is not emailed. It works once and for a few hours; treat it like a password. The audit trail records
    that a link was made, never the link.
    """
    if not actor.is_superuser:
        raise ServiceError("Only a Super Admin can create a sign-in link by hand.", code="permission_denied",
                           status_code=status.HTTP_403_FORBIDDEN)
    _password_target(actor, user)
    if user.status != AccountStatus.ACTIVE:
        raise ServiceError("This account is not active. Reactivate it first.", code="account_inactive")
    uid = urlsafe_base64_encode(force_bytes(user.pk))
    link = settings.SITE_URL.rstrip("/") + reverse("accounts:password_reset_confirm",
                                                    args=[uid, default_token_generator.make_token(user)])
    audit.record("account.setup_link_created", actor=actor, target=user, request=request)
    return link


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


def set_extra_permissions(actor, user, codenames, *, request=None):
    """
    Grant this one staff member individual permissions beyond what their role gives everyone holding it (Django's
    own per-user ``user_permissions`` - already honoured by ``has_perm()`` alongside the role's group permissions;
    this is the first thing that actually sets it). Never touches the role itself or anyone else's access.
    """
    from django.contrib.auth.models import Permission
    from django.contrib.contenttypes.models import ContentType

    from .models import PortalAccess

    _require(actor, "assign_roles")
    if user.pk == actor.pk:
        raise ServiceError("You cannot change your own extra permissions.", code="self_action")
    if user.role not in STAFF_ROLES:
        raise ServiceError("Only a staff member can hold portal permissions.", code="not_staff")
    if (user.role in PRIVILEGED_ROLES) and not actor.is_superuser:
        raise ServiceError("Only a Super Admin can change an admin's permissions.",
                           code="permission_denied", status_code=status.HTTP_403_FORBIDDEN)
    unknown = set(codenames) - set(ALL_CODENAMES)
    if unknown:
        raise ServiceError(f"Unknown permission(s): {', '.join(sorted(unknown))}.", code="invalid_permission")
    content_type = ContentType.objects.get_for_model(PortalAccess, for_concrete_model=False)
    wanted = list(Permission.objects.filter(content_type=content_type, codename__in=codenames))
    before = set(user.user_permissions.values_list("codename", flat=True))
    user.user_permissions.set(wanted)
    after = set(codenames)
    if before != after:
        audit.record("account.extra_permissions_changed", actor=actor, target=user,
                     metadata={"added": sorted(after - before), "removed": sorted(before - after)}, request=request)
    return user


# --- Two-factor sign-in (TOTP, Phase 16) -----------------------------------------------------------------------
#
# Optional for everyone: a customer or a staff member turns it on themselves, from their own profile, whenever they
# want it - nothing forces it or steers anyone towards it. The maths lives in ``mfa`` (no database, no request); this
# is the part that touches the account: the secret at rest (encrypted like a provider credential), the recovery codes
# (hashed like a password, one-time), and the pause between "password was right" and "really signed in" while the
# code is checked.

MFA_PENDING_SESSION_KEY = "mfa_pending_user_id"
MFA_PENDING_EXPIRES_KEY = "mfa_pending_expires"
MFA_PENDING_REMEMBER_KEY = "mfa_pending_remember"
MFA_PENDING_NEXT_KEY = "mfa_pending_next"
MFA_PENDING_TIMEOUT_SECONDS = 10 * 60
MFA_LOCK_THRESHOLD = 5
MFA_LOCK_MINUTES = 15


def mfa_issuer():
    """The name an authenticator app shows next to the account (the brand, so several sites in one app stay apart)."""
    from apps.branding import services as branding

    return branding.get().name


def mfa_begin_setup(user):
    """
    A fresh secret to show on the setup page, replacing any not-yet-confirmed one (refusing if two-factor sign-in is
    already on - turn it off first). Returns ``(secret, otpauth_uri)``; nothing is active until a code confirms it.
    """
    if user.mfa_enabled:
        raise ServiceError("Two-factor sign-in is already on. Turn it off before setting it up again.",
                           code="mfa_already_enabled")
    secret = mfa_lib.generate_secret()
    user.mfa_secret_encrypted = crypto.encrypt(secret)
    user.save(update_fields=["mfa_secret_encrypted", "updated_at"])
    return secret, mfa_lib.provisioning_uri(secret, email=user.email, issuer=mfa_issuer())


def _replace_recovery_codes(user):
    """Ten fresh single-use codes, replacing whatever the account had. Returns them in the clear (shown once, never stored)."""
    MFARecoveryCode.objects.filter(user=user).delete()
    codes = mfa_lib.generate_recovery_codes()
    MFARecoveryCode.objects.bulk_create([
        MFARecoveryCode(user=user, code_hash=make_password(mfa_lib.normalize_recovery_code(code))) for code in codes
    ])
    return codes


def _clear_mfa(user):
    user.mfa_enabled = False
    user.mfa_secret_encrypted = ""
    user.mfa_confirmed_at = None
    user.save(update_fields=["mfa_enabled", "mfa_secret_encrypted", "mfa_confirmed_at", "updated_at"])
    MFARecoveryCode.objects.filter(user=user).delete()


@transaction.atomic
def mfa_confirm_setup(user, code, *, request=None):
    """Turn two-factor sign-in on: the code from the app must match the secret ``mfa_begin_setup`` just showed."""
    if user.mfa_enabled:
        raise ServiceError("Two-factor sign-in is already on.", code="mfa_already_enabled")
    if not user.mfa_secret_encrypted:
        raise ServiceError("Start setup again; there is nothing waiting to be confirmed.", code="mfa_not_started")
    secret = crypto.decrypt(user.mfa_secret_encrypted)
    if not mfa_lib.verify(secret, code):
        raise ServiceError("That code is not right. Check the time on your phone and try again.", code="mfa_invalid_code")
    user.mfa_enabled = True
    user.mfa_confirmed_at = timezone.now()
    user.save(update_fields=["mfa_enabled", "mfa_confirmed_at", "updated_at"])
    codes = _replace_recovery_codes(user)
    audit.record("account.mfa_enabled", actor=user, target=user, request=request)
    notifications.dispatch("account.mfa_enabled", user=user)
    return codes


@transaction.atomic
def mfa_disable(user, *, password, request=None):
    """Turn two-factor sign-in off yourself; the current password confirms it is really you."""
    if not user.check_password(password):
        raise ServiceError("Current password is incorrect.", code="invalid_password")
    if not user.mfa_enabled:
        raise ServiceError("Two-factor sign-in is already off.", code="mfa_not_enabled")
    _clear_mfa(user)
    audit.record("account.mfa_disabled", actor=user, target=user, request=request)
    notifications.dispatch("account.mfa_disabled", user=user)


@transaction.atomic
def mfa_admin_reset(actor, user, *, request=None):
    """Super Admin only: turn two-factor sign-in off for someone who lost their authenticator app, so they can set it up again."""
    if not actor.is_superuser:
        raise ServiceError("Only a Super Admin can reset two-factor sign-in for someone else.", code="permission_denied",
                           status_code=status.HTTP_403_FORBIDDEN)
    if user.pk == actor.pk:
        raise ServiceError("Turn off your own two-factor sign-in from your profile.", code="self_action")
    if not user.mfa_enabled and not user.mfa_secret_encrypted:
        raise ServiceError("Two-factor sign-in is not set up for this person.", code="mfa_not_enabled")
    _clear_mfa(user)
    audit.record("account.mfa_reset_by_admin", actor=actor, target=user, request=request)
    notifications.dispatch("account.mfa_reset_by_admin", user=user)


@transaction.atomic
def mfa_regenerate_recovery_codes(user, *, password, request=None):
    """Ten new recovery codes; the old ones stop working. The current password confirms it is really you."""
    if not user.check_password(password):
        raise ServiceError("Current password is incorrect.", code="invalid_password")
    if not user.mfa_enabled:
        raise ServiceError("Two-factor sign-in is not on.", code="mfa_not_enabled")
    codes = _replace_recovery_codes(user)
    audit.record("account.mfa_recovery_codes_regenerated", actor=user, target=user, request=request)
    return codes


def mfa_verify_code_or_recovery(user, code):
    """
    True if ``code`` is a correct authenticator code, or an unused recovery code (which it then spends). Guessing is
    limited the same way a password is: a wrong code counts towards this account's own lockout, quite apart from the
    password's, so a stolen password alone is not enough time to sit and guess the second factor too.
    """
    if is_locked_out("mfa", str(user.pk)):
        raise ServiceError("Too many wrong codes. Wait a few minutes and try again.", code="account_locked",
                           status_code=status.HTTP_429_TOO_MANY_REQUESTS)
    secret = crypto.decrypt(user.mfa_secret_encrypted) if user.mfa_secret_encrypted else ""
    if secret and mfa_lib.verify(secret, code):
        clear_login_attempts("mfa", str(user.pk))
        return True
    normalized = mfa_lib.normalize_recovery_code(code)
    if normalized:
        for row in MFARecoveryCode.objects.filter(user=user, used_at__isnull=True):
            if check_password(normalized, row.code_hash):
                row.used_at = timezone.now()
                row.save(update_fields=["used_at"])
                clear_login_attempts("mfa", str(user.pk))
                audit.record("account.mfa_recovery_code_used", actor=user, target=user)
                return True
    register_failed_attempt("mfa", str(user.pk), threshold=MFA_LOCK_THRESHOLD, lock_minutes=MFA_LOCK_MINUTES)
    return False


def start_mfa_challenge(request, user, *, remember, next_url):
    """Password was right and two-factor sign-in is on: hold the sign-in open (nobody is signed in yet) until the code is checked."""
    request.session[MFA_PENDING_SESSION_KEY] = user.pk
    request.session[MFA_PENDING_EXPIRES_KEY] = (timezone.now() + timezone.timedelta(seconds=MFA_PENDING_TIMEOUT_SECONDS)).isoformat()
    request.session[MFA_PENDING_REMEMBER_KEY] = bool(remember)
    request.session[MFA_PENDING_NEXT_KEY] = next_url or ""


def get_pending_mfa_user(request):
    """The user waiting on a code, or ``None`` (clearing the pending state) if there is none or it has expired."""
    user_id = request.session.get(MFA_PENDING_SESSION_KEY)
    if not user_id:
        return None
    expires = request.session.get(MFA_PENDING_EXPIRES_KEY)
    try:
        expired = expires is None or timezone.now() > datetime.fromisoformat(expires)
    except ValueError:
        expired = True
    if expired:
        clear_mfa_pending(request)
        return None
    return User.objects.filter(pk=user_id, is_active=True).first()


def clear_mfa_pending(request):
    for key in (MFA_PENDING_SESSION_KEY, MFA_PENDING_EXPIRES_KEY, MFA_PENDING_REMEMBER_KEY, MFA_PENDING_NEXT_KEY):
        request.session.pop(key, None)


def set_permission_access(actor, user, allow, hide, *, request=None):
    """
    One person's access, permission by permission: ``allow`` = given on top of the role, ``hide`` = taken away from this person although the role gives it, anything else follows the role.
    The role and everybody else holding it are never touched. Gated exactly like role assignment, and a Super Admin cannot be restricted.
    """
    from .models import HiddenPermission

    allow, hide = set(allow or ()), set(hide or ())
    if allow & hide:
        raise ServiceError("A permission cannot be both allowed and hidden.", code="invalid_permission")
    if user.is_superuser:
        raise ServiceError("A Super Admin always has full access.", code="permission_denied", status_code=status.HTTP_403_FORBIDDEN)
    unknown = (allow | hide) - set(ALL_CODENAMES)
    if unknown:
        raise ServiceError(f"Unknown permission(s): {', '.join(sorted(unknown))}.", code="invalid_permission")
    before_hidden = set(user.hidden_permissions.values_list("codename", flat=True))
    set_extra_permissions(actor, user, sorted(allow), request=None)  # the same checks as before; raises when this person may not be changed
    user.hidden_permissions.exclude(codename__in=hide).delete()
    for codename in hide - before_hidden:
        HiddenPermission.objects.get_or_create(user=user, codename=codename)
    if before_hidden != hide:
        audit.record("account.permissions_hidden_changed", actor=actor, target=user,
                     metadata={"hidden": sorted(hide - before_hidden), "shown_again": sorted(before_hidden - hide)}, request=request)
    audit.record("account.permission_access_saved", actor=actor, target=user, metadata={"allowed": sorted(allow), "hidden": sorted(hide)}, request=request)
    return user


def access_table(user):
    """Every portal permission for one person: [{codename, label, area, role_gives, extra, hidden, effective}] - what the role gives, what was added, what was hidden, and what that leaves."""
    role_gives = set(ROLE_PERMISSIONS.get(user.role, [])) if user.role in {r.value for r in Role} else set()
    extra = set(user.user_permissions.values_list("codename", flat=True))
    hidden = set(user.hidden_permissions.values_list("codename", flat=True))
    rows = []
    for codename, label in PORTAL_PERMISSIONS:
        given = codename in role_gives
        rows.append({"codename": codename, "label": label, "role_gives": given, "extra": codename in extra, "hidden": codename in hidden,
                     "effective": user.is_superuser or ((given or codename in extra) and codename not in hidden)})
    return rows
