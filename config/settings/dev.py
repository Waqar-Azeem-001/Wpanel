from .base import *  # noqa: F401,F403
from .base import BASE_DIR, env

DEBUG = env.bool("DJANGO_DEBUG", default=True)

# Allow running locally without PostgreSQL/Redis. Production always uses PostgreSQL.
if not env("DATABASE_URL", default=""):
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": BASE_DIR / "db.sqlite3",
        }
    }

if not env("REDIS_URL", default=""):
    CACHES = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}
    CELERY_TASK_ALWAYS_EAGER = True
    CELERY_TASK_EAGER_PROPAGATES = True
    CELERY_BROKER_URL = "memory://"
    CELERY_RESULT_BACKEND = "cache+memory://"
