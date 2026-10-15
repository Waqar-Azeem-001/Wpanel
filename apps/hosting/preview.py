"""
The preview address of a website: ``<name>.<PREVIEW_DOMAIN>`` (for example ``acme-com.preview.example.com``), set up as an alias of the plan's main domain, so the
website and its WordPress admin can be opened and used before the real domain leads to this hosting.

WordPress normally sends every visitor to the one address it was installed with, so a small must-use plugin (``whp-preview-host.php``) lets it answer on the preview
address as well as on its own domain: on whichever of those two the visitor came, WordPress uses that same address. When the real domain's DNS later points here,
the same website simply works there too, with nothing to change. Any other host name is left alone (WordPress behaves exactly as before), so a forged Host header
cannot make it hand out links to an address of someone else's choosing.

It needs one thing from the operator: a wildcard DNS record ``*.<PREVIEW_DOMAIN>`` leading to the hosting server, and ``PREVIEW_DOMAIN`` set. Without it the
feature is simply off and customers get the hosts-file preview.
"""
import re

from django.conf import settings

from apps.core.exceptions import ServiceError

from . import site_apps
from .adapters.capabilities import Capability

MU_FILE = "whp-preview-host.php"


def base():
    return (getattr(settings, "PREVIEW_DOMAIN", "") or "").strip().strip(".").lower()


def enabled():
    return bool(base())


def host(account, domain=""):
    """The preview host name of this plan's main website, or "" when the feature is off or the plan has no main-site preview (added domains keep the hosts-file way)."""
    if not enabled() or domain:
        return ""
    label = re.sub(r"[^a-z0-9]+", "-", account.domain.lower()).strip("-")[:50] or f"site{account.pk}"
    return f"{label}.{base()}"


def url(account, domain=""):
    name = host(account, domain)
    return ("http" + "://" + name + "/") if name else ""


def mu_plugin(real_domain, preview_host):
    allowed = [real_domain, "www." + real_domain, preview_host]
    hosts = ", ".join("'" + h.replace("\\", "").replace("'", "") + "'" for h in allowed)
    return (
        "<?php\n"
        "/* Managed by WHPanel. Lets this website answer on its preview address as well as on its own domain. Edits to this file are replaced. */\n"
        "$whp_host = isset( $_SERVER['HTTP_HOST'] ) ? strtolower( preg_replace( '/:\\d+$/', '', (string) $_SERVER['HTTP_HOST'] ) ) : '';\n"
        f"if ( in_array( $whp_host, array( {hosts} ), true ) ) {{\n"
        "    $whp_base = ( is_ssl() ? 'https' : 'http' ) . '://' . $whp_host;\n"
        "    add_filter( 'pre_option_home', function () use ( $whp_base ) { return $whp_base; } );\n"
        "    add_filter( 'pre_option_siteurl', function () use ( $whp_base ) { return $whp_base; } );\n"
        "}\n"
    )


def ensure(user, account, domain=""):
    """Set the preview address up (idempotent) and return its host. Raises ServiceError when the server refuses; the caller falls back to the hosts-file way."""
    from . import care, care_tools, control

    name = host(account, domain)
    if not name:
        return ""
    app = site_apps.current(account, domain)
    if app is None or app.kind != "wordpress":
        return ""
    present = {d.name for d in control.perform(user, account, Capability.DOMAINS, "list_domains")}
    if name not in present:
        control.perform(user, account, Capability.DOMAINS, "add_domain", name, "alias", "",
                        audit_metadata={"domain": name, "purpose": "preview address"})
    io = care._Io(user, account)
    care_tools._ensure_mu_dir(io, account)
    path = f"{care_tools._mu_dir(account)}/{MU_FILE}"
    io.call(Capability.FILES, "write_file", path, mu_plugin(account.domain.lower(), name).encode(), overwrite=True,
            audit_metadata={"path": path, "purpose": "preview address"})
    return name
