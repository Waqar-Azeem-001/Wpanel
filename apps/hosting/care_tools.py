"""
The rest of website care, each a small honest tool that works through the hosting-control layer for any provider:

* site options for WordPress (maintenance page, "ask search engines not to index"), a must-use plugin WordPress reads on every request;
* performance rules (compression and browser caching) in a clearly marked block of ``.htaccess`` (Apache/LiteSpeed servers);
* quarantine: move a flagged file aside, and put it back;
* traffic: requests, visitors, top pages and status codes from the website's own access log, where the provider can show it;
* a Google PageSpeed report for the home page;
* the WordPress manager: plugins, themes and core with their updates (one-file tool, see ``wp_tool``).
"""
import json
import re
import time
from collections import Counter

import requests
from django.conf import settings
from django.utils import timezone

from apps.audit import services as audit
from apps.core.exceptions import ServiceError

from . import care, site_apps, wp_tool
from .adapters.capabilities import Capability
from .models import SiteScan, SpeedTest

OPTIONS_FILE = "whp-site-options.php"
QUARANTINE_DIR = ".whp-quarantine"
BEGIN, END = "# BEGIN WHPanel performance", "# END WHPanel performance"
HTACCESS_RULES = """<IfModule mod_deflate.c>
  AddOutputFilterByType DEFLATE text/html text/plain text/css text/xml application/javascript application/json image/svg+xml
</IfModule>
<IfModule mod_expires.c>
  ExpiresActive On
  ExpiresByType image/jpeg "access plus 1 year"
  ExpiresByType image/png "access plus 1 year"
  ExpiresByType image/webp "access plus 1 year"
  ExpiresByType image/svg+xml "access plus 1 year"
  ExpiresByType text/css "access plus 1 month"
  ExpiresByType application/javascript "access plus 1 month"
  ExpiresByType font/woff2 "access plus 1 year"
</IfModule>"""
SECURITY_CRITICAL_RULES = {"core-file-modified"}  # moving a WordPress system file aside would break the website


# --- WordPress site options --------------------------------------------------------------------------------------------

def options_file(maintenance, discourage_search):
    lines = ["<?php", "/* Managed by WHPanel (Care). Change these choices in your hosting panel; edits to this file are replaced. */"]
    if maintenance:
        lines += [
            "add_action( 'template_redirect', function () {",
            "    if ( is_user_logged_in() && current_user_can( 'edit_posts' ) ) { return; }",
            "    status_header( 503 ); header( 'Retry-After: 3600' ); nocache_headers();",
            "    wp_die( '<h1>Be right back</h1><p>This website is being updated. Please try again in a little while.</p>', 'Maintenance', array( 'response' => 503 ) );",
            "} );",
        ]
    if discourage_search:
        lines.append("add_filter( 'pre_option_blog_public', '__return_zero' );")
    return "\n".join(lines) + "\n"


def _mu_dir(account):
    return f"{site_apps.docroot_path(account)}/wp-content/mu-plugins"


def _ensure_mu_dir(io, account):
    parent = f"{site_apps.docroot_path(account)}/wp-content"
    if "mu-plugins" not in {e.name for e in io.call(Capability.FILES, "list_files", parent)}:
        io.call(Capability.FILES, "make_directory", _mu_dir(account))


def save_site_options(user, account, maintenance, discourage_search):
    if not care.can_manage_updates(account):
        raise ServiceError("These options are for WordPress websites that WHPanel set up.", code="not_wordpress")
    io = care._Io(user, account)
    _ensure_mu_dir(io, account)
    path = f"{_mu_dir(account)}/{OPTIONS_FILE}"
    io.call(Capability.FILES, "write_file", path, options_file(maintenance, discourage_search).encode(), overwrite=True,
            audit_metadata={"path": path, "maintenance": maintenance, "discourage_search": discourage_search})
    row = care.care_for(account)
    row.maintenance, row.discourage_search = bool(maintenance), bool(discourage_search)
    row.save()
    return row


# --- Performance rules in .htaccess ----------------------------------------------------------------------------------

def merge_htaccess(current, enabled):
    """The .htaccess text with the WHPanel block present (``enabled``) or absent, leaving every other line untouched."""
    pattern = re.compile(r"\n?" + re.escape(BEGIN) + r".*?" + re.escape(END) + r"\n?", re.S)
    cleaned = pattern.sub("\n", current or "").strip("\n")
    if not enabled:
        return cleaned + ("\n" if cleaned else "")
    block = f"{BEGIN}\n{HTACCESS_RULES}\n{END}"
    return (block + "\n\n" + cleaned + "\n") if cleaned else block + "\n"


def performance_supported(account):
    """.htaccess is read by Apache and LiteSpeed (cPanel servers)."""
    return account.server is not None


def save_performance(user, account, enabled):
    if not performance_supported(account):
        raise ServiceError("This server does not use .htaccess rules.", code="not_supported")
    io = care._Io(user, account)
    path = f"{site_apps.docroot_path(account)}/.htaccess"
    try:
        raw = io.call(Capability.FILES, "read_file", path, max_bytes=200_000)
        current = raw.decode("utf-8", "replace") if isinstance(raw, (bytes, bytearray)) else str(raw)
    except ServiceError:
        current = ""
    io.call(Capability.FILES, "write_file", path, merge_htaccess(current, enabled).encode(), overwrite=True,
            audit_metadata={"path": path, "performance": bool(enabled)})
    row = care.care_for(account)
    row.performance = bool(enabled)
    row.save()
    return row


# --- Quarantine ----------------------------------------------------------------------------------------------------------

def _clean_rel(path):
    path = (path or "").strip().strip("/")
    parts = path.split("/")
    if not path or ".." in parts or "\\" in path or "\x00" in path or parts[0] == QUARANTINE_DIR:
        raise ServiceError("That location is not inside your website's folder.", code="bad_path")
    return path


def quarantine_file(user, account, path):
    """Move a file the last scan flagged into the quarantine folder (renamed so it cannot run). Nothing is deleted."""
    rel = _clean_rel(path)
    scan = SiteScan.objects.filter(account=account, state="done").first()
    finding = next((f for f in (scan.findings if scan else []) if f["path"] == rel), None)
    if finding is None:
        raise ServiceError("Only a file the last scan flagged can be moved aside.", code="not_flagged")
    if finding["rule"] in SECURITY_CRITICAL_RULES:
        raise ServiceError("This is one of WordPress's own files. Moving it aside would break the website; reinstall WordPress core instead.", code="critical")
    io = care._Io(user, account)
    folder = site_apps.docroot_path(account)
    base = f"{folder}/{QUARANTINE_DIR}"
    if QUARANTINE_DIR not in {e.name for e in io.call(Capability.FILES, "list_files", folder)}:
        io.call(Capability.FILES, "make_directory", base)
        io.call(Capability.FILES, "write_file", f"{base}/.htaccess", b"Require all denied\nDeny from all\n", overwrite=True)
    stored = f"{int(time.time())}-{rel.replace('/', '__')}.quarantined"
    io.call(Capability.FILES, "rename_path", f"{folder}/{rel}", f"{base}/{stored}", audit_metadata={"path": rel, "action": "quarantine"})
    row = care.care_for(account)
    row.quarantine = [{"path": rel, "stored": stored, "rule": finding["rule"], "at": timezone.now().isoformat()}, *row.quarantine][:100]
    row.save()
    if scan:
        scan.findings = [f for f in scan.findings if f["path"] != rel]
        scan.save(update_fields=["findings", "updated_at"])
    return row


def restore_file(user, account, stored):
    row = care.care_for(account)
    entry = next((q for q in row.quarantine if q["stored"] == stored), None)
    if entry is None:
        raise ServiceError("That file is not in quarantine any more.", code="not_found")
    io = care._Io(user, account)
    folder = site_apps.docroot_path(account)
    target = f"{folder}/{entry['path']}"
    parent = target.rsplit("/", 1)[0]
    existing = {e.name for e in io.call(Capability.FILES, "list_files", parent)}
    if entry["path"].rsplit("/", 1)[-1] in existing:
        raise ServiceError("A file with that name exists again, so this one was not put back.", code="exists")
    io.call(Capability.FILES, "rename_path", f"{folder}/{QUARANTINE_DIR}/{stored}", target, audit_metadata={"path": entry["path"], "action": "restore"})
    row.quarantine = [q for q in row.quarantine if q["stored"] != stored]
    row.save()
    return row


# --- Traffic -----------------------------------------------------------------------------------------------------------

LOG = re.compile(r'^(?P<ip>\S+) \S+ \S+ \[[^\]]*\] "(?P<method>[A-Z]+) (?P<path>\S+)[^"]*" (?P<status>\d{3}) (?P<size>\S+)(?: "[^"]*" "(?P<agent>[^"]*)")?')
STATIC = re.compile(r"\.(css|js|png|jpe?g|gif|webp|svg|ico|woff2?|ttf|map|txt|xml)(\?|$)", re.I)
BOTS = re.compile(r"bot|crawl|spider|slurp|monitor|curl|python-requests|wget", re.I)


def parse_access(lines):
    """Figures from combined-format access log lines. Lines that do not parse are counted and left out, never guessed."""
    requests_n = pages = bots = unparsed = 0
    visitors, top, statuses = set(), Counter(), Counter()
    for line in lines:
        match = LOG.match(line or "")
        if not match:
            unparsed += 1
            continue
        requests_n += 1
        status = match.group("status")
        statuses[status[0] + "xx"] += 1
        agent = match.group("agent") or ""
        if BOTS.search(agent):
            bots += 1
            continue
        visitors.add(match.group("ip"))
        path = match.group("path").split("?", 1)[0]
        if match.group("method") == "GET" and status.startswith("2") and not STATIC.search(path):
            pages += 1
            top[path] += 1
    return {"requests": requests_n, "page_views": pages, "visitors": len(visitors), "bots": bots, "unparsed": unparsed,
            "top_pages": top.most_common(10), "statuses": sorted(statuses.items())}


def traffic(user, account, lines=1000):
    io = care._Io(user, account)
    if not io.supports(Capability.LOGS):
        return None
    try:
        view = io.call(Capability.LOGS, "read_log", "access", lines, "")
    except ServiceError:
        return None
    result = parse_access(view.lines)
    result["lines_read"] = len(view.lines)
    return result


# --- Google PageSpeed --------------------------------------------------------------------------------------------------

PAGESPEED = "https" + "://www.googleapis.com/pagespeedonline/v5/runPagespeed"
METRICS = (("first-contentful-paint", "First contentful paint"), ("largest-contentful-paint", "Largest contentful paint"), ("total-blocking-time", "Total blocking time"),
           ("cumulative-layout-shift", "Layout shift"), ("speed-index", "Speed index"))


def pagespeed(account, strategy="mobile"):
    """Ask Google's PageSpeed service about the home page. Stored as a ``SpeedTest`` of kind "pagespeed". Raises ServiceError when Google cannot be reached or says no."""
    if strategy not in ("mobile", "desktop"):
        strategy = "mobile"
    target = "https" + "://" + account.domain.lower() + "/"
    params = {"url": target, "strategy": strategy, "category": "performance"}
    key = getattr(settings, "PAGESPEED_API_KEY", "")
    if key:
        params["key"] = key
    try:
        reply = requests.get(PAGESPEED, params=params, timeout=(6, 90))
    except requests.exceptions.RequestException as exc:
        raise ServiceError("Google PageSpeed could not be reached (" + type(exc).__name__ + ").", code="unreachable") from exc
    if reply.status_code == 429:
        raise ServiceError("Google PageSpeed is busy or its free quota is used up. Try again later.", code="rate_limited")
    if reply.status_code != 200:
        message = ""
        try:
            message = reply.json().get("error", {}).get("message", "")
        except ValueError:
            pass
        raise ServiceError("Google PageSpeed could not test this website" + (": " + message[:200] if message else "."), code="pagespeed_failed")
    try:
        data = reply.json()
        lighthouse = data["lighthouseResult"]
        score = round(float(lighthouse["categories"]["performance"]["score"]) * 100)
        figures = {label: lighthouse["audits"][key_]["displayValue"] for key_, label in METRICS if key_ in lighthouse.get("audits", {}) and "displayValue" in lighthouse["audits"][key_]}
    except (ValueError, KeyError, TypeError) as exc:
        raise ServiceError("Google PageSpeed answered with something unexpected.", code="pagespeed_failed") from exc
    row = SpeedTest.objects.create(account=account, url=target.rstrip("/"), ok=True, status_code=200, score=score, kind="pagespeed", report={"strategy": strategy, "figures": figures},
                                   https=True)
    stale = list(SpeedTest.objects.filter(account=account, kind="pagespeed").values_list("pk", flat=True)[care.KEEP_RESULTS:])
    if stale:
        SpeedTest.objects.filter(pk__in=stale).delete()
    return row


# --- WordPress manager (plugins, themes, core) ---------------------------------------------------------------------------

def _tidy_leftovers(io, folder):
    for entry in io.call(Capability.FILES, "list_files", folder):
        if entry.name.startswith("whp-wp-"):
            try:
                io.call(Capability.FILES, "delete_path", f"{folder}/{entry.name}")
            except ServiceError:
                pass


def wordpress_action(user, account, action, target="", secret="", domain=""):
    """Run one WordPress action (``wp_tool.ACTIONS``) and return what is installed afterwards: {"core", "plugins", "themes"}. Raises ServiceError with WordPress's own message."""
    if action not in wp_tool.ACTIONS:
        raise ServiceError("That action is not available.", code="unknown_action")
    app = site_apps.current(account, domain)
    if app is None or app.kind != "wordpress" or app.state != "ready":
        raise ServiceError("The WordPress manager is for WordPress websites that WHPanel set up.", code="not_wordpress")
    io = care._Io(user, account)
    folder = site_apps.docroot_path(account, domain)
    _tidy_leftovers(io, folder)
    file_name, result_name, token, source = wp_tool.build(action, target, secret)
    path = f"{folder}/{file_name}"
    io.call(Capability.FILES, "write_file", path, source.encode(), overwrite=False, audit_metadata={"path": path, "purpose": "one-time WordPress tool", "action": action})
    try:
        outcome = site_apps.reach(account, file_name, token, purpose="to manage WordPress", domain=domain)
    except ServiceError:
        care._remove(io, path)
        raise
    data = outcome
    if data is None:  # still working: wait a little for the result file
        deadline = time.monotonic() + 45
        while data is None and time.monotonic() < deadline:
            time.sleep(3)
            try:
                raw = io.call(Capability.FILES, "read_file", f"{folder}/{result_name}", max_bytes=400_000)
                data = json.loads(raw.decode("utf-8", "replace") if isinstance(raw, (bytes, bytearray)) else str(raw))
            except (ServiceError, ValueError):
                data = None
    care._remove(io, f"{folder}/{result_name}")
    if data is None:
        raise ServiceError("WordPress is still working. Reload this page in a minute.", code="still_working")
    audit.record("hosting.wordpress_action", actor=user, target=account, metadata={"action": action, "target": str(target)[:100], "ok": bool(data.get("ok"))})
    if not data.get("ok"):
        raise ServiceError(str(data.get("error") or "WordPress could not do that.")[:300], code="wordpress_failed")
    return {"core": data.get("core") or {}, "plugins": data.get("plugins") or [], "themes": data.get("themes") or []}
