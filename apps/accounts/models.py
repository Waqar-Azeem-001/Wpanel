from django.contrib.auth.models import AbstractBaseUser, BaseUserManager, PermissionsMixin
from django.db import models
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
