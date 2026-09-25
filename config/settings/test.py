import tempfile
from pathlib import Path

from .base import *  # noqa: F401,F403
from .base import REST_FRAMEWORK, env

DEBUG = False
ALLOW_TEST_PAYMENT_GATEWAY = True
PRIVATE_MEDIA_ROOT = str(Path(tempfile.gettempdir()) / "wpanel-test-private-media")  # tests use their own temp dir
ORDER_AUTO_FULFIL = False  # fulfilment tests switch it on explicitly
SECRET_KEY = "test-secret-key-0123456789-abcdefghijklmnopqrstuvwxyz"
# Tests must not depend on whatever a developer's local .env happens to contain -
# force the deterministic SECRET_KEY-derived fallback (see apps.core.crypto).
CREDENTIALS_ENCRYPTION_KEY = ""

if not env("DATABASE_URL", default=""):
    DATABASES = {"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"}}

CACHES = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}
PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]
EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"

CELERY_TASK_ALWAYS_EAGER = True
CELERY_TASK_EAGER_PROPAGATES = True
CELERY_BROKER_URL = "memory://"
CELERY_RESULT_BACKEND = "cache+memory://"

REST_FRAMEWORK = {
    **REST_FRAMEWORK,
    "DEFAULT_THROTTLE_RATES": {"anon": "1000/min", "user": "1000/min", "auth": "1000/min"},
}
