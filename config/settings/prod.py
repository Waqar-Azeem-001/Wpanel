from .base import *  # noqa: F401,F403
from .base import env

DEBUG = False
SECRET_KEY = env("DJANGO_SECRET_KEY")  # required in production

# With no active email provider configured in the admin, delivery fails loudly
# (recorded on the email log) instead of silently printing to the console.
EMAIL_BACKEND = "apps.notifications.backends.NoProviderConfiguredBackend"

SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SECURE_SSL_REDIRECT = env.bool("DJANGO_SECURE_SSL_REDIRECT", default=True)
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True
SECURE_HSTS_SECONDS = env.int("DJANGO_HSTS_SECONDS", default=60 * 60 * 24 * 30)
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = "same-origin"
X_FRAME_OPTIONS = "DENY"
