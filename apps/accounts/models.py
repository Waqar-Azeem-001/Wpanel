from django.conf import settings
from django.contrib.auth.models import AbstractBaseUser, BaseUserManager, PermissionsMixin
from django.db import models
from django.db.models.functions import Lower
from django.utils import timezone

from .roles import PORTAL_PERMISSIONS, STAFF_ROLES, Role


class AccountStatus(models.TextChoices):
    ACTIVE = "active", "Active"
    SUSPENDED = "suspended", "Suspended"
    CLOSED = "closed", "Closed"


class UserManager(BaseUserManager):
    use_in_migrations = True

    def _create_user(self, email, password, **extra):
        if not email:
            raise ValueError("Email is required.")
        user = self.model(email=self.normalize_email(email).lower(), **extra)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def get_by_natural_key(self, email):
        return self.get(email__iexact=email)

    def create_user(self, email, password=None, **extra):
        extra.setdefault("role", Role.CUSTOMER)
        return self._create_user(email, password, **extra)

    def create_superuser(self, email, password=None, **extra):
        extra["role"] = Role.SUPER_ADMIN
        user = self._create_user(email, password, **extra)
        from .services import sync_role_membership

        sync_role_membership(user)
        return user


class User(AbstractBaseUser, PermissionsMixin):
    email = models.EmailField(unique=True)
    first_name = models.CharField(max_length=150, blank=True)
    last_name = models.CharField(max_length=150, blank=True)
    phone = models.CharField(max_length=32, blank=True)

    role = models.CharField(max_length=32, choices=Role.choices, default=Role.CUSTOMER, db_index=True)
    status = models.CharField(
        max_length=16, choices=AccountStatus.choices, default=AccountStatus.ACTIVE, db_index=True
    )
    email_verified_at = models.DateTimeField(null=True, blank=True)

    # Two-factor sign-in (TOTP, Phase 16): optional for everyone, staff and customers alike - someone turns it on
    # themselves from their profile. The secret is encrypted the same way a provider credential is
    # (apps.core.crypto); it exists (non-blank) once someone has started setting up, but only counts as active once
    # ``mfa_enabled`` is set, which happens after a code confirms it.
    mfa_secret_encrypted = models.TextField(blank=True, editable=False)
    mfa_enabled = models.BooleanField(default=False, editable=False)
    mfa_confirmed_at = models.DateTimeField(null=True, blank=True, editable=False)

    # A staff member's own arrangement of the dashboard panels below the KPI strip (widget keys, in the order they
    # want them; apps.console.widgets.visible_widgets applies it). A key that no longer exists, or that this person
    # can no longer see, is simply dropped - never carried along as a gap - so adding or removing a widget in the
    # registry never needs a migration or a data fix-up here.
    dashboard_widget_order = models.JSONField(default=list, blank=True, editable=False)

    # Derived from ``status`` and ``role`` on save; never set directly.
    is_active = models.BooleanField(default=True, editable=False)
    is_staff = models.BooleanField(default=False, editable=False)

    date_joined = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)

    objects = UserManager()

    USERNAME_FIELD = "email"
    EMAIL_FIELD = "email"
    REQUIRED_FIELDS = []

    class Meta:
        ordering = ["-date_joined"]
        constraints = [models.UniqueConstraint(Lower("email"), name="accounts_user_email_ci_unique")]  # "A@x.com" and "a@x.com" are one person

    def __str__(self):
        return self.email

    def save(self, *args, **kwargs):
        self.email = self.email.lower()
        self.is_active = self.status == AccountStatus.ACTIVE
        self.is_staff = self.role in STAFF_ROLES
        self.is_superuser = self.role == Role.SUPER_ADMIN
        update_fields = kwargs.get("update_fields")
        if update_fields is not None and ({"status", "role"} & set(update_fields)):
            kwargs["update_fields"] = set(update_fields) | {"is_active", "is_staff", "is_superuser"}
        super().save(*args, **kwargs)

    @property
    def full_name(self):
        return f"{self.first_name} {self.last_name}".strip()

    @property
    def is_email_verified(self):
        return self.email_verified_at is not None

    def get_full_name(self):
        return self.full_name or self.email

    def get_short_name(self):
        return self.first_name or self.email


class PortalAccess(models.Model):
    """Holder for portal permissions (no table). See ``apps.accounts.roles``."""

    class Meta:
        managed = False
        default_permissions = ()
        permissions = PORTAL_PERMISSIONS


class MFARecoveryCode(models.Model):
    """
    One single-use recovery code for signing in without the authenticator app (a lost phone). Ten are made when MFA is
    confirmed or regenerated; each is stored hashed (Django's own password hasher — the same one-way protection as an
    account password) and is spent the moment it is used.
    """

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="mfa_recovery_codes")
    code_hash = models.CharField(max_length=128)
    used_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        indexes = [models.Index(fields=["user", "used_at"])]

    def __str__(self):
        return f"Recovery code for {self.user_id} ({'used' if self.used_at else 'unused'})"


class HiddenPermission(models.Model):
    """A permission the person's role gives, switched off for this one person (the role itself and everyone else keep it). The other half of "extra permissions"."""

    user = models.ForeignKey("accounts.User", on_delete=models.CASCADE, related_name="hidden_permissions")
    codename = models.CharField(max_length=60)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["user", "codename"], name="one_hidden_permission_per_user")]


class SignInDevice(models.Model):
    """
    The one device a person is signed in on. Signing in on another device needs an emailed code and replaces this row, which
    signs the old device out (its session carries the old token, which no longer matches). Only a hash of the token is kept;
    the browser holds the token itself in an HttpOnly cookie.
    """

    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="signin_device")
    token_hash = models.CharField(max_length=64)
    user_agent = models.CharField(max_length=200, blank=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    created_at = models.DateTimeField(default=timezone.now)


class EmailSignInCode(models.Model):
    """A sign-in code sent by email: six digits, kept hashed, valid for a few minutes, a few tries, then it is spent."""

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="signin_codes")
    code_hash = models.CharField(max_length=128)
    created_at = models.DateTimeField(default=timezone.now)
    expires_at = models.DateTimeField()
    tries = models.PositiveSmallIntegerField(default=0)
    used_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
