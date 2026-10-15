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

from apps.core.exceptions import ServiceError

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


LOGIN_DIR = "whp-login"
LOGIN_SECONDS = 300

_PREVIEW_BLOCK = (
    "$whp_expect_host = '{host}';\n"
    "$whp_expect_token = '{token}';\n"
    "$whp_host  = isset( $_SERVER['HTTP_X_WHP_PREVIEW_HOST'] ) ? strtolower( (string) $_SERVER['HTTP_X_WHP_PREVIEW_HOST'] ) : '';\n"
    "$whp_token = isset( $_SERVER['HTTP_X_WHP_PREVIEW_TOKEN'] ) ? (string) $_SERVER['HTTP_X_WHP_PREVIEW_TOKEN'] : '';\n"
    "if ( $whp_host === $whp_expect_host && hash_equals( $whp_expect_token, $whp_token ) ) {{\n"
    "    $whp_proto = ( isset( $_SERVER['HTTP_X_WHP_PREVIEW_PROTO'] ) && $_SERVER['HTTP_X_WHP_PREVIEW_PROTO'] === 'https' ) ? 'https' : 'http';\n"
    "    $whp_base = $whp_proto . '://' . $whp_host;\n"
    "    // the panel reaches this server over https whatever the visitor used, so say what the visitor really used\n"
    "    if ( $whp_proto === 'https' ) {{ $_SERVER['HTTPS'] = 'on'; $_SERVER['SERVER_PORT'] = '443'; }} else {{ unset( $_SERVER['HTTPS'] ); $_SERVER['SERVER_PORT'] = '80'; }}\n"
    "    $_SERVER['HTTP_HOST'] = $whp_host; $_SERVER['SERVER_NAME'] = $whp_host; // WordPress builds its own links and redirects from the host it was asked on\n"
    "    add_filter( 'pre_option_home', function () use ( $whp_base ) {{ return $whp_base; }} );\n"
    "    add_filter( 'pre_option_siteurl', function () use ( $whp_base ) {{ return $whp_base; }} );\n"
    "}}\n"
)

_LOGIN_BLOCK = (
    "// One-click sign-in from the hosting panel: a one-time token (its hash is the name of a small file the panel wrote a moment ago) signs the named administrator in, once.\n"
    "add_action( 'init', function () {\n"
    "    if ( empty( $_GET['whp_login'] ) ) { return; }\n"
    "    $t = (string) $_GET['whp_login'];\n"
    "    if ( ! preg_match( '/^[A-Za-z0-9_-]{20,80}$/', $t ) ) { return; }\n"
    "    $file = WP_CONTENT_DIR . '/' . '__LOGIN_DIR__' . '/' . hash( 'sha256', $t );\n"
    "    if ( ! is_file( $file ) ) { return; }\n"
    "    $data = json_decode( (string) @file_get_contents( $file ), true );\n"
    "    @unlink( $file );\n"
    "    if ( ! is_array( $data ) || empty( $data['u'] ) || empty( $data['exp'] ) || time() > (int) $data['exp'] ) { return; }\n"
    "    $user = get_user_by( 'login', (string) $data['u'] );\n"
    "    if ( ! $user ) { return; }\n"
    "    wp_set_current_user( $user->ID );\n"
    "    wp_set_auth_cookie( $user->ID, false, is_ssl() );\n"
    "    do_action( 'wp_login', $user->user_login, $user );\n"
    "    wp_safe_redirect( admin_url() );\n"
    "    exit;\n"
    "}, 1 );\n"
).replace("__LOGIN_DIR__", LOGIN_DIR)


def mu_plugin(preview_host=""):
    """The must-use plugin: the one-click sign-in always, and the preview-host answers when ``preview_host`` is given."""
    head = "<?php\n/* Managed by WHPanel (one-click sign-in from the hosting panel, and the preview address). Edits to this file are replaced. */\n"
    body = _PREVIEW_BLOCK.format(host=preview_host, token=token(preview_host)) if preview_host else ""
    return head + body + _LOGIN_BLOCK


def _folders(io, account, domain):
    """Make wp-content/mu-plugins and wp-content/whp-login when missing. Returns (mu-plugins path, login path)."""
    wp_content = f"{site_apps.docroot_path(account, domain)}/wp-content"
    present = {e.name for e in io.call(Capability.FILES, "list_files", wp_content)}
    for name in ("mu-plugins", LOGIN_DIR):
        if name not in present:
            io.call(Capability.FILES, "make_directory", f"{wp_content}/{name}")
    return f"{wp_content}/mu-plugins", f"{wp_content}/{LOGIN_DIR}"


def install(user, account, domain=""):
    """Put the plugin on a WordPress website (idempotent). Returns the preview host it answers on, or "". Raises ServiceError when the files cannot be written."""
    from . import care

    app = site_apps.current(account, domain)
    if app is None or app.kind != "wordpress":
        return ""
    name = host(account, domain)
    io = care._Io(user, account)
    mu_dir, _ = _folders(io, account, domain)
    path = f"{mu_dir}/{MU_FILE}"
    io.call(Capability.FILES, "write_file", path, mu_plugin(name).encode(), overwrite=True, audit_metadata={"path": path, "purpose": "sign-in and preview"})
    return name


ensure = install  # the name the installer and the gate already use


def sign_in_token(user, account, domain=""):
    """A one-time sign-in token for the website's administrator, valid for a few minutes. Written as a small file the plugin reads once and deletes."""
    import json
    import secrets
    import time

    from . import care

    app = site_apps.current(account, domain)
    if app is None or app.kind != "wordpress" or not app.admin_user:
        raise ServiceError("There is no WordPress administrator to sign in.", code="no_admin")
    io = care._Io(user, account)
    _, login_dir = _folders(io, account, domain)
    secret = secrets.token_urlsafe(32)
    path = f"{login_dir}/{hashlib.sha256(secret.encode()).hexdigest()}"
    io.call(Capability.FILES, "write_file", path, json.dumps({"u": app.admin_user, "exp": int(time.time()) + LOGIN_SECONDS}).encode(), overwrite=True,
            audit_metadata={"path": path, "purpose": "one-click sign-in", "admin": app.admin_user})
    return secret
