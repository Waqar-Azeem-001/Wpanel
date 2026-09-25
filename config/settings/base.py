"""
Base settings shared by every environment.

Only infrastructure settings (database, cache, broker, secret key, hosts) come
from the environment. Provider credentials (registrar, WHM, payment, email
provider) are live, database-configured connections and must never be added
here as environment variables.
"""
from datetime import timedelta
from pathlib import Path

import environ
from celery.schedules import crontab

BASE_DIR = Path(__file__).resolve().parent.parent.parent

env = environ.Env()
environ.Env.read_env(BASE_DIR / ".env", overwrite=False)

SECRET_KEY = env("DJANGO_SECRET_KEY", default="insecure-dev-key-change-me")
DEBUG = env.bool("DJANGO_DEBUG", default=False)
ALLOWED_HOSTS = env.list("DJANGO_ALLOWED_HOSTS", default=["localhost", "127.0.0.1"])
CSRF_TRUSTED_ORIGINS = env.list("DJANGO_CSRF_TRUSTED_ORIGINS", default=[])

SITE_NAME = env("SITE_NAME", default="Wpanel")
SITE_URL = env("SITE_URL", default="http://localhost:8000")
# All catalog prices and orders are in this one currency (multi-currency is a deferred item).
STORE_CURRENCY = env("STORE_CURRENCY", default="USD")
# The built-in test payment gateway simulates a hosted checkout page and signs its own webhooks. It moves no
# money, so it must never be usable in production: off by default, on in dev/test settings only.
ALLOW_TEST_PAYMENT_GATEWAY = env.bool("ALLOW_TEST_PAYMENT_GATEWAY", default=False)
# A paid order is fulfilled (services created, terms started) automatically in the background. The switch
# exists so tests of *other* behaviour can pay an order without provisioning; it is on everywhere else.
ORDER_AUTO_FULFIL = env.bool("ORDER_AUTO_FULFIL", default=True)
# Emails get a 1x1 image that records when it is fetched ("opened"). It is a signal, not proof of reading: mail
# clients that block images hide it, and privacy proxies and link scanners fetch it. Never added to security emails.
EMAIL_OPEN_TRACKING = env.bool("EMAIL_OPEN_TRACKING", default=True)

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    # Third party
    "rest_framework",
    "rest_framework_simplejwt.token_blacklist",
    "django_filters",
    "drf_spectacular",
    # Project
    "apps.core",
    "apps.accounts",
    "apps.audit",
    "apps.notifications",
    "apps.clients",
    "apps.products",
    "apps.domains",
    "apps.hosting",
    "apps.billing",
    "apps.orders",
    "apps.renewals",
    "apps.support",
]

MIDDLEWARE = [
    "apps.core.middleware.RequestIDMiddleware",
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "apps.core.context_processors.site",
                "apps.orders.context_processors.cart_summary",
                "apps.notifications.context_processors.unread_notifications",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"

DATABASES = {
    "default": env.db("DATABASE_URL", default="postgres://wpanel:wpanel@localhost:5432/wpanel"),
}
DATABASES["default"]["ATOMIC_REQUESTS"] = False
DATABASES["default"]["CONN_MAX_AGE"] = env.int("DATABASE_CONN_MAX_AGE", default=60)

REDIS_URL = env("REDIS_URL", default="redis://localhost:6379/0")

CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.redis.RedisCache",
        "LOCATION": REDIS_URL,
    }
}

AUTH_USER_MODEL = "accounts.User"
LOGIN_URL = "accounts:login"
LOGIN_REDIRECT_URL = "accounts:profile"
LOGOUT_REDIRECT_URL = "accounts:login"

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator", "OPTIONS": {"min_length": 10}},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

# Token lifetimes for emailed links.
PASSWORD_RESET_TIMEOUT = 60 * 60 * 3  # 3 hours
EMAIL_VERIFICATION_MAX_AGE = 60 * 60 * 24 * 3  # 3 days

LANGUAGE_CODE = "en-us"
TIME_ZONE = env("TIME_ZONE", default="UTC")
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STATICFILES_DIRS = [BASE_DIR / "static"]
MEDIA_URL = "media/"
MEDIA_ROOT = BASE_DIR / "media"
# Ticket attachments live here, NOT under MEDIA_ROOT (which nginx serves publicly): they are only reachable
# through an authenticated view. Keep it out of any web-served path.
PRIVATE_MEDIA_ROOT = env("PRIVATE_MEDIA_ROOT", default=str(BASE_DIR / "private_media"))
SUPPORT_MAX_ATTACHMENT_MB = 5

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# Sessions
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
SESSION_COOKIE_AGE = 60 * 60 * 12

# --- Django REST Framework -------------------------------------------------
REST_FRAMEWORK = {
    "DEFAULT_VERSIONING_CLASS": "rest_framework.versioning.NamespaceVersioning",
    "DEFAULT_VERSION": "v1",
    "ALLOWED_VERSIONS": ["v1"],
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "rest_framework_simplejwt.authentication.JWTAuthentication",
        "rest_framework.authentication.SessionAuthentication",
    ],
    "DEFAULT_PERMISSION_CLASSES": ["rest_framework.permissions.IsAuthenticated"],
    "DEFAULT_PAGINATION_CLASS": "apps.core.pagination.StandardPagination",
    "PAGE_SIZE": 25,
    "DEFAULT_FILTER_BACKENDS": [
        "django_filters.rest_framework.DjangoFilterBackend",
        "rest_framework.filters.SearchFilter",
        "rest_framework.filters.OrderingFilter",
    ],
    "DEFAULT_THROTTLE_CLASSES": [
        "rest_framework.throttling.AnonRateThrottle",
        "rest_framework.throttling.UserRateThrottle",
    ],
    "DEFAULT_THROTTLE_RATES": {
        "anon": env("THROTTLE_ANON", default="60/min"),
        "user": env("THROTTLE_USER", default="300/min"),
        "auth": env("THROTTLE_AUTH", default="10/min"),
    },
    "EXCEPTION_HANDLER": "apps.core.exceptions.api_exception_handler",
    "DEFAULT_SCHEMA_CLASS": "drf_spectacular.openapi.AutoSchema",
    "TEST_REQUEST_DEFAULT_FORMAT": "json",
}

SIMPLE_JWT = {
    "ACCESS_TOKEN_LIFETIME": timedelta(minutes=env.int("JWT_ACCESS_MINUTES", default=15)),
    "REFRESH_TOKEN_LIFETIME": timedelta(days=env.int("JWT_REFRESH_DAYS", default=14)),
    "ROTATE_REFRESH_TOKENS": True,
    "BLACKLIST_AFTER_ROTATION": True,
    "UPDATE_LAST_LOGIN": True,
    "AUTH_HEADER_TYPES": ("Bearer",),
    # Access tokens stop working as soon as the password changes.
    "CHECK_REVOKE_TOKEN": True,
}

SPECTACULAR_SETTINGS = {
    "TITLE": "Wpanel API",
    "DESCRIPTION": "Hosting management portal API. Shared by the web portal and future mobile clients.",
    "VERSION": "1.0.0",
    "SERVE_INCLUDE_SCHEMA": False,
    # API schema/docs are for staff and integrators, not the public.
    "SERVE_PERMISSIONS": ["rest_framework.permissions.IsAdminUser"],
    "SCHEMA_PATH_PREFIX": r"/api/v[0-9]+",
    # Stable enum names for generated (e.g. mobile) API clients.
    "ENUM_NAME_OVERRIDES": {
        "UserRoleEnum": "apps.accounts.roles.Role",
        "AccountStatusEnum": "apps.accounts.models.AccountStatus",
        "ClientStatusEnum": "apps.clients.models.ClientStatus",
        "ContactRoleEnum": "apps.clients.models.ContactRole",
        "ProductTypeEnum": "apps.products.models.ProductType",
        "CatalogStatusEnum": "apps.products.models.CatalogStatus",
        "BillingCycleEnum": "apps.products.models.BillingCycle",
        "ServerStatusEnum": "apps.products.models.ServerStatus",
        "ServerKindEnum": "apps.products.models.ServerKind",
        "DomainStatusEnum": "apps.domains.models.DomainStatus",
        "DnsRecordTypeEnum": "apps.domains.models.DnsRecordType",
        "RegistrarKindEnum": "apps.domains.models.RegistrarProvider.Kind",
        "HostingStatusEnum": "apps.hosting.models.HostingStatus",
        "DiscountTypeEnum": "apps.billing.models.DiscountType",
        "OrderStatusEnum": "apps.orders.models.OrderStatus",
        "CartItemKindEnum": "apps.orders.models.ItemKind",
        "CartStatusEnum": "apps.orders.models.CartStatus",
        "InvoiceStatusEnum": "apps.billing.models.InvoiceStatus",
        "QuoteStatusEnum": "apps.billing.models.QuoteStatus",
        "TransactionStatusEnum": "apps.billing.models.TransactionStatus",
        "TransactionTypeEnum": "apps.billing.models.TransactionType",
        "FulfilmentStatusEnum": "apps.orders.models.FulfilmentStatus",
        "TicketStatusEnum": "apps.support.models.TicketStatus",
        "TicketPriorityEnum": "apps.support.models.TicketPriority",
        "TicketMessageKindEnum": "apps.support.models.MessageKind",
        "ServiceChangeKindEnum": "apps.renewals.models.ChangeKind",
        "ServiceChangeStatusEnum": "apps.renewals.models.ChangeStatus",
    },
}

# --- Celery ------------------------------------------------------------------
CELERY_BROKER_URL = env("CELERY_BROKER_URL", default=REDIS_URL)
CELERY_RESULT_BACKEND = env("CELERY_RESULT_BACKEND", default=REDIS_URL)
CELERY_TASK_ACKS_LATE = True
CELERY_TASK_REJECT_ON_WORKER_LOST = True
CELERY_WORKER_PREFETCH_MULTIPLIER = 1
CELERY_TASK_TIME_LIMIT = 300
CELERY_TASK_SOFT_TIME_LIMIT = 240
CELERY_TIMEZONE = TIME_ZONE
CELERY_BEAT_SCHEDULE = {
    "generate-renewal-invoices": {
        "task": "apps.renewals.tasks.generate_renewal_invoices_task",
        "schedule": crontab(hour=3, minute=0),
    },
    "send-invoice-reminders": {
        "task": "apps.billing.tasks.send_invoice_reminders_task",
        "schedule": crontab(hour=8, minute=0),
    },
    "purge-sensitive-emails": {
        "task": "apps.notifications.tasks.purge_sensitive_emails_task",
        "schedule": crontab(hour=4, minute=30),
    },
    "auto-close-resolved-tickets": {
        "task": "apps.support.tasks.auto_close_resolved_tickets_task",
        "schedule": crontab(hour=4, minute=0),
    },
    "sweep-stuck-orders": {
        "task": "apps.orders.tasks.sweep_stuck_orders_task",
        "schedule": crontab(minute="*/5"),
    },
}

# --- Email -------------------------------------------------------------------
# Phase 01 uses Django's email backend for delivery. The live, database-configured
# email provider arrives in Phase 11 (see roadmap Known Work).
EMAIL_BACKEND = "django.core.mail.backends.console.EmailBackend"
DEFAULT_FROM_EMAIL = env("DEFAULT_FROM_EMAIL", default="Wpanel <no-reply@localhost>")

# --- Logging -----------------------------------------------------------------
LOG_LEVEL = env("LOG_LEVEL", default="INFO")
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "filters": {"request_id": {"()": "apps.core.logging.RequestIDFilter"}},
    "formatters": {
        "standard": {
            "format": "%(asctime)s %(levelname)s [%(name)s] [req:%(request_id)s] %(message)s",
        },
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "standard",
            "filters": ["request_id"],
        },
    },
    "root": {"handlers": ["console"], "level": LOG_LEVEL},
    "loggers": {
        "django": {"handlers": ["console"], "level": LOG_LEVEL, "propagate": False},
        "apps": {"handlers": ["console"], "level": LOG_LEVEL, "propagate": False},
    },
}

# --- Credential encryption -----------------------------------------------------
# Key used to encrypt provider credentials stored in the database. Falls back to a
# key derived from SECRET_KEY; set explicitly in production so SECRET_KEY rotation
# does not make stored credentials unreadable.
CREDENTIALS_ENCRYPTION_KEY = env("CREDENTIALS_ENCRYPTION_KEY", default="")
