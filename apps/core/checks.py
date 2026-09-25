"""Startup checks: a fault in the menu registry stops ``manage.py check`` (and so the deploy), not a customer's click."""
from django.core.checks import Error, register


@register()
def menu_registry(app_configs, **kwargs):
    from . import navigation

    return [Error(problem, hint="Fix the entry in apps/core/navigation.py.", id="core.E001")
            for problem in navigation.problems()]


@register(deploy=True)
def site_url_is_https(app_configs, **kwargs):
    """Every link in every email is built from SITE_URL; on a live site it must be a secure address people can open."""
    from django.conf import settings

    url = settings.SITE_URL
    if url.startswith("https://") and "localhost" not in url and not url.endswith("/"):
        return []
    return [Error(f"SITE_URL is {url!r}; emailed links would not work for customers.",
                  hint="Set SITE_URL to the public address, starting with https:// and with no trailing slash "
                       "(for example https://portal.example.com).", id="core.E002")]
