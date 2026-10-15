"""
The preview address of a website: ``<name>.<PREVIEW_DOMAIN>`` (for example ``acme-com.preview.example.com``). It opens the real website and its WordPress admin before the
real domain leads to the hosting, and keeps working unchanged after it does: the website itself never changes address, only the way in does.

How it works (nothing on the hosting server's control panel is touched):

1. The wildcard DNS name ``*.<PREVIEW_DOMAIN>`` leads to the panel's own server, whose web server (nginx) asks this app who the name belongs to
   (``views_preview.resolve``) and forwards the request to the hosting server's address with the website's real domain as its Host, so the hosting server serves the
   right website.
2. WordPress would answer with links to its real domain, so a small must-use plugin (``whp-preview-host.php``) makes it answer with the preview address instead, but
   only for requests that carry this website's secret token (made from the panel's secret key and the preview host name, so nobody can ask for it by naming a header).
   Every other request, including the real domain, is left exactly as WordPress had it.

It needs ``PREVIEW_DOMAIN`` and ``PREVIEW_PROXY_KEY`` set and the nginx server block (``deploy/nginx/whpanel-preview.conf.example``); without them the feature is off
and customers get the hosts-file preview.
"""
import hashlib
import hmac
import re

from django.conf import settings

from . import site_apps
from .adapters.capabilities import Capability

MU_FILE = "whp-preview-host.php"


def base():
    return (getattr(settings, "PREVIEW_DOMAIN", "") or "").strip().strip(".").lower()


def enabled():
    return bool(base()) and bool(getattr(settings, "PREVIEW_PROXY_KEY", ""))


def slug(account):
    return re.sub(r"[^a-z0-9]+", "-", account.domain.lower()).strip("-")[:50] or f"site{account.pk}"


def host(account, domain=""):
    """The preview host name of this plan's main website, or "" when the feature is off or for an added domain (those keep the hosts-file way)."""
    if not enabled() or domain:
        return ""
    return f"{slug(account)}.{base()}"


def url(account, domain="", secure=None):
    name = host(account, domain)
    if not name:
        return ""
    scheme = "https" if (secure if secure is not None else getattr(settings, "PREVIEW_SECURE", False)) else "http"
    return f"{scheme}://{name}/"


def token(preview_host):
    """The secret that proves a request came through the panel's proxy for exactly this preview host."""
    return hmac.new(settings.SECRET_KEY.encode(), ("preview|" + preview_host).encode(), hashlib.sha256).hexdigest()[:40]


def account_for_host(preview_host):
    """The hosting account a preview host belongs to, or None. Only active accounts with a website set up are served."""
    from .models import HostingAccount, HostingStatus

    preview_host = (preview_host or "").lower().split(":")[0]
    suffix = "." + base()
    if not enabled() or not preview_host.endswith(suffix):
        return None
    label = preview_host[: -len(suffix)]
    if not re.fullmatch(r"[a-z0-9-]{1,63}", label or ""):
        return None
    for account in HostingAccount.objects.filter(status=HostingStatus.ACTIVE).select_related("server"):
        if slug(account) == label:
            return account
    return None


def mu_plugin(preview_host):
    return (
        "<?php\n"
        "/* Managed by WHPanel. Lets this website answer on its preview address when a request comes through the WHPanel proxy. Edits to this file are replaced. */\n"
        f"$whp_expect_host = '{preview_host}';\n"
        f"$whp_expect_token = '{token(preview_host)}';\n"
        "$whp_host  = isset( $_SERVER['HTTP_X_WHP_PREVIEW_HOST'] ) ? strtolower( (string) $_SERVER['HTTP_X_WHP_PREVIEW_HOST'] ) : '';\n"
        "$whp_token = isset( $_SERVER['HTTP_X_WHP_PREVIEW_TOKEN'] ) ? (string) $_SERVER['HTTP_X_WHP_PREVIEW_TOKEN'] : '';\n"
        "if ( $whp_host === $whp_expect_host && hash_equals( $whp_expect_token, $whp_token ) ) {\n"
        "    $whp_proto = ( isset( $_SERVER['HTTP_X_WHP_PREVIEW_PROTO'] ) && $_SERVER['HTTP_X_WHP_PREVIEW_PROTO'] === 'https' ) ? 'https' : 'http';\n"
        "    $whp_base = $whp_proto . '://' . $whp_host;\n"
        "    if ( $whp_proto === 'https' ) { $_SERVER['HTTPS'] = 'on'; }\n"
        "    add_filter( 'pre_option_home', function () use ( $whp_base ) { return $whp_base; } );\n"
        "    add_filter( 'pre_option_siteurl', function () use ( $whp_base ) { return $whp_base; } );\n"
        "}\n"
    )


def ensure(user, account, domain=""):
    """Put the preview plugin on the website (idempotent) and return its preview host, "" when there is nothing to do. Raises ServiceError when the files cannot be written."""
    from . import care, care_tools

    name = host(account, domain)
    if not name:
        return ""
    app = site_apps.current(account, domain)
    if app is None or app.kind != "wordpress":
        return ""
    io = care._Io(user, account)
    care_tools._ensure_mu_dir(io, account)
    path = f"{care_tools._mu_dir(account)}/{MU_FILE}"
    io.call(Capability.FILES, "write_file", path, mu_plugin(name).encode(), overwrite=True,
            audit_metadata={"path": path, "purpose": "preview address"})
    return name
