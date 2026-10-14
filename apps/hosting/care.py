"""
Website care: the things that keep a website healthy without the customer having to remember them - a speed test, a malware scan, WordPress that keeps itself up to date, and
backups that run on a schedule the customer chose. Everything here works for any provider: it reads and writes files through the hosting-control layer, calls the website over
the public internet, and uses the provider's own scheduling only where the provider has some.

Run by a customer, every call goes through ``control.perform`` (ownership, audit). Run by the panel's daily job (``user`` is None) it talks to the account's provider directly,
the same way the provider's own scheduled jobs do; it only ever does what the customer switched on.
"""
import json
import time
from datetime import timedelta

import requests
from django.utils import timezone

from apps.audit import services as audit
from apps.core.exceptions import ServiceError

from . import care_scanner, control, scan_rules, site_apps
from .adapters.base import HostingError
from .adapters.capabilities import OPERATIONS, Capability, CapabilityNotSupported, supported_capabilities
from .models import HostingStatus, SiteCare, SiteScan, SpeedTest, WebsiteApp
from .services import get_adapter

KEEP_RESULTS = 20
SPEED_PAUSE = timedelta(seconds=20)
SCAN_STALE = timedelta(minutes=15)
PERIOD = {"daily": timedelta(days=1), "weekly": timedelta(days=7)}
KEEP_PANEL_BACKUPS = 3
MU_PLUGIN_DIR = "wp-content/mu-plugins"
MU_PLUGIN_FILE = "whp-auto-updates.php"


class _Io:
    """Files and backups of one account, as the customer (checked and audited) or as the panel's own job (``user`` is None)."""

    def __init__(self, user, account):
        self.user, self.account = user, account

    def call(self, capability, operation, *args, **kwargs):
        if operation not in OPERATIONS.get(capability, frozenset()):
            raise ServiceError("That operation is not available.", code="operation_not_allowed")
        if self.user is not None:
            return control.perform(self.user, self.account, capability, operation, *args, **kwargs)
        kwargs.pop("audit_metadata", None)
        try:
            adapter = get_adapter(self.account.server)
            if capability not in supported_capabilities(adapter):
                raise ServiceError("This server does not support that.", code="capability_not_supported")
            return getattr(adapter, operation)(self.account.username, *args, **kwargs)
        except CapabilityNotSupported as exc:
            raise ServiceError("This server does not support that.", code="capability_not_supported") from exc
        except HostingError as exc:
            raise ServiceError(f"The hosting server could not complete that: {exc}", code="adapter_error") from exc

    def supports(self, capability):
        try:
            return capability.value in control.capabilities_for(self.account)
        except ServiceError:
            return False


def care_for(account):
    return SiteCare.objects.get_or_create(account=account)[0]


def _wordpress_ready(account):
    app = WebsiteApp.objects.filter(account=account).first()
    return app is not None and app.kind == "wordpress" and app.state == "ready"


def can_manage_updates(account):
    """WordPress was set up here and is ready: only then is there something to keep up to date."""
    return _wordpress_ready(account)


# --- Speed test -------------------------------------------------------------------------------------------------------

def _fetch(url, hop_limit=5):
    """Fetch the page like a visitor, following redirects only to public addresses. Returns the measurements; raises ServiceError when it cannot be reached."""
    from urllib.parse import urljoin, urlparse

    redirects, started, spent = 0, time.monotonic(), 0.0
    headers = {"User-Agent": "WHPanel-SpeedTest/1.0", "Accept-Encoding": "gzip, br", "Accept": "text/html,*/*;q=0.8"}
    current = url
    while True:
        host = (urlparse(current).hostname or "").lower()
        if not host or not site_apps._public(host):
            raise ServiceError("That address does not lead to a public website.", code="not_public")
        spent = time.monotonic() - started  # what earlier redirects took
        try:
            reply = requests.get(current, headers=headers, timeout=(6, 20), allow_redirects=False, stream=True)
        except requests.exceptions.SSLError as exc:
            raise ServiceError("The secure connection to your website failed (" + type(exc).__name__ + ").", code="ssl_error") from exc
        except requests.exceptions.RequestException as exc:
            raise ServiceError("Your website did not answer (" + type(exc).__name__ + ").", code="unreachable") from exc
        if reply.is_redirect and reply.headers.get("Location") and redirects < hop_limit:
            redirects += 1
            current = urljoin(current, reply.headers["Location"])
            reply.close()
            continue
        break
    first_byte = spent + reply.elapsed.total_seconds()
    size = 0
    try:
        for chunk in reply.iter_content(65536):
            size += len(chunk)
            if size > 5_000_000:
                break
    finally:
        reply.close()
    total = time.monotonic() - started
    control_header = reply.headers.get("Cache-Control", "").lower()
    max_age = 0
    for part in control_header.split(","):
        part = part.strip()
        if part.startswith(("max-age=", "s-maxage=")):
            try:
                max_age = max(max_age, int(part.split("=", 1)[1]))
            except ValueError:
                pass
    return {"final_url": current, "status": reply.status_code, "first_byte_ms": int(first_byte * 1000), "total_ms": int(total * 1000), "size": size,
            "redirects": redirects, "https": current.startswith("https" + "://"),
            "compressed": reply.headers.get("Content-Encoding", "").lower() in ("gzip", "br", "deflate", "zstd"),
            "cached": max_age >= 60 or bool(reply.headers.get("Expires")) and "no-store" not in control_header and "no-cache" not in control_header}


def score(measured):
    """0-100 and the notes behind it. Server response dominates, because it is what the hosting itself decides."""
    advice, points = [], 100
    first = measured["first_byte_ms"]
    if first < 200:
        pass
    elif first < 500:
        points -= 8
        advice.append("The server answers a little slowly. Page caching usually helps.")
    elif first < 1000:
        points -= 22
        advice.append("The server takes a while to answer. Turn on page caching or look at heavy plugins.")
    elif first < 2000:
        points -= 38
        advice.append("The server is slow to answer. Visitors will notice; caching and fewer plugins are the first things to try.")
    else:
        points -= 55
        advice.append("The server takes more than two seconds to answer. Contact us if caching does not help.")
    if measured["total_ms"] > 6000:
        points -= 15
        advice.append("The page takes more than six seconds to arrive in full. Large images are the usual cause.")
    elif measured["total_ms"] > 3000:
        points -= 8
        advice.append("The page takes about three seconds or more to arrive in full.")
    if not measured["https"]:
        points -= 20
        advice.append("The page is not served over HTTPS. Turn on the SSL certificate and force HTTPS.")
    if not measured["compressed"]:
        points -= 8
        advice.append("The page is not compressed. Switching compression on makes it several times smaller to send.")
    if measured["size"] > 2_000_000:
        points -= 10
        advice.append("The page is larger than 2 MB. Optimise images.")
    if not measured["cached"]:
        points -= 4
        advice.append("The page does not tell browsers they may keep it. A cache header makes repeat visits faster.")
    if measured["redirects"] > 2:
        points -= 5
        advice.append("The address goes through several redirects before the page.")
    return max(0, min(100, points)), advice


def _addresses(account):
    domain = account.domain.lower()
    return ["https" + "://" + domain + "/", "http" + "://" + domain + "/"]


def speed_test(account, now=None):
    """Measure the home page now (at most one test every few seconds per website: the earlier result is returned instead). Stores and returns the ``SpeedTest``."""
    now = now or timezone.now()
    latest = SpeedTest.objects.filter(account=account, kind="server").first()
    if latest is not None and now - latest.created_at < SPEED_PAUSE:
        return latest
    measured, error, url = None, "", ""
    for url in _addresses(account):
        try:
            measured = _fetch(url)
            break
        except ServiceError as exc:
            error = exc.message
    if measured is None:
        result = SpeedTest.objects.create(account=account, url=url.rstrip("/"), ok=False, error=error[:300] or "Your website could not be reached.",
                                          advice=["Check that the domain points to your hosting and the website is not suspended."])
    else:
        points, advice = score(measured)
        ok = measured["status"] < 400
        if not ok:
            advice.insert(0, f"The home page answered with an error (status {measured['status']}).")
            points = 0
        result = SpeedTest.objects.create(
            account=account, url=measured["final_url"][:300], ok=ok, status_code=measured["status"], first_byte_ms=measured["first_byte_ms"], total_ms=measured["total_ms"],
            size_bytes=measured["size"], redirects=measured["redirects"], https=measured["https"], compressed=measured["compressed"], cached=measured["cached"],
            score=points, advice=advice)
    stale = list(SpeedTest.objects.filter(account=account, kind="server").values_list("pk", flat=True)[KEEP_RESULTS:])
    if stale:
        SpeedTest.objects.filter(pk__in=stale).delete()
    return result


# --- Malware scan -----------------------------------------------------------------------------------------------------

def last_scan(account):
    return SiteScan.objects.filter(account=account).first()


def _store_findings(scan, data):
    findings = [{"path": str(f.get("path", ""))[:300], "rule": str(f.get("rule", ""))[:40], "severity": str(f.get("severity", "medium"))[:10],
                 "detail": str(f.get("detail", ""))[:300]} for f in (data.get("findings") or []) if isinstance(f, dict)]
    findings.sort(key=lambda f: (scan_rules.SEVERITY_ORDER.get(f["severity"], 9), f["path"], f["rule"]))
    scan.findings = findings[:200]
    scan.files_scanned = int(data.get("files_scanned") or 0)
    scan.wp_version = str(data.get("wp_version") or "")[:30]
    scan.wp_checked = int(data.get("wp_checked") or 0)
    scan.state, scan.finished_at, scan.error = "done", timezone.now(), ""


def _fail(scan, message):
    scan.state, scan.finished_at, scan.error = "failed", timezone.now(), message[:300]
    scan.save()
    return scan


def start_scan(user, account, trigger="manual"):
    """Start a scan unless one is already running. A provider that scans itself is used as is; otherwise the one-file scanner runs on the website. Returns the ``SiteScan``."""
    running = SiteScan.objects.filter(account=account, state="running").first()
    if running is not None:
        if timezone.now() - running.started_at < SCAN_STALE:
            return poll_scan(user, account) or running
        _fail(running, "The scan did not report back in time.")
    io = _Io(user, account)
    scan = SiteScan.objects.create(account=account, trigger=trigger)
    audit.record("hosting.scan_started", actor=user, target=account, metadata={"trigger": trigger})
    if io.supports(Capability.SECURITY):
        try:
            result = io.call(Capability.SECURITY, "run_malware_scan")
        except ServiceError as exc:
            return _fail(scan, exc.message)
        prefix = site_apps.docroot_path(account) + "/"  # a provider names files from the account's home; the page and quarantine speak from the website's folder
        found = [{**f.__dict__, "path": f.path[len(prefix):] if f.path.startswith(prefix) else f.path} for f in result.findings]
        _store_findings(scan, {"files_scanned": result.files_scanned, "findings": found})
        scan.save()
        return scan
    if not io.supports(Capability.FILES):
        return _fail(scan, "This server cannot be scanned from here yet.")
    folder = site_apps.docroot_path(account)
    file_name, result_name, token, source = care_scanner.build()
    path = f"{folder}/{file_name}"
    try:
        io.call(Capability.FILES, "write_file", path, source.encode(), overwrite=False, audit_metadata={"path": path, "purpose": "one-time malware scan"})
    except ServiceError as exc:
        return _fail(scan, exc.message)
    scan.script_ref, scan.result_ref = file_name, result_name
    scan.save(update_fields=["script_ref", "result_ref", "updated_at"])
    try:
        outcome = site_apps.reach(account, file_name, token, purpose="to scan it")
    except ServiceError as exc:
        _remove(io, f"{folder}/{file_name}")
        return _fail(scan, exc.message)
    if outcome is not None:
        _apply(io, scan, outcome)
    return scan


def _remove(io, path):
    try:
        io.call(Capability.FILES, "delete_path", path)
    except ServiceError:
        pass


def _apply(io, scan, data):
    folder = site_apps.docroot_path(scan.account)
    if data.get("ok"):
        _store_findings(scan, data)
        scan.save()
        audit.record("hosting.scan_finished", target=scan.account, metadata={"files": scan.files_scanned, "findings": len(scan.findings), "trigger": scan.trigger})
    else:
        _fail(scan, str(data.get("error") or "The scan could not finish."))
    if scan.result_ref:
        _remove(io, f"{folder}/{scan.result_ref}")


def poll_scan(user, account):
    """A scan that did not answer within the wait: has the scanner left its result? Cheap; safe on every page view."""
    scan = last_scan(account)
    if scan is None or scan.state != "running" or not scan.result_ref:
        return scan
    io = _Io(user, account)
    folder = site_apps.docroot_path(account)
    try:
        raw = io.call(Capability.FILES, "read_file", f"{folder}/{scan.result_ref}", max_bytes=300_000)
        data = json.loads(raw.decode("utf-8", "replace") if isinstance(raw, (bytes, bytearray)) else str(raw))
    except (ServiceError, ValueError):
        if timezone.now() - scan.started_at > SCAN_STALE:
            _remove(io, f"{folder}/{scan.script_ref}")
            _fail(scan, "The scan did not report back in time.")
        return scan
    _apply(io, scan, data if isinstance(data, dict) else {"ok": False})
    return scan


# --- WordPress updates ------------------------------------------------------------------------------------------------

def rules_file(core, plugins, themes):
    """The small must-use plugin WordPress reads on every request. Plain text, no secrets; it only tells WordPress's own updater what it may do."""
    lines = ["<?php", "/* Managed by WHPanel (Care > Updates). Change these choices in your hosting panel; edits to this file are replaced. */"]
    if core == "off":
        lines += ["add_filter( 'allow_minor_auto_core_updates', '__return_false' );", "add_filter( 'allow_major_auto_core_updates', '__return_false' );"]
    elif core == "all":
        lines += ["add_filter( 'allow_minor_auto_core_updates', '__return_true' );", "add_filter( 'allow_major_auto_core_updates', '__return_true' );"]
    else:
        lines += ["add_filter( 'allow_minor_auto_core_updates', '__return_true' );", "add_filter( 'allow_major_auto_core_updates', '__return_false' );"]
    lines.append("add_filter( 'auto_update_plugin', '__return_%s' );" % ("true" if plugins else "false"))
    lines.append("add_filter( 'auto_update_theme', '__return_%s' );" % ("true" if themes else "false"))
    return "\n".join(lines) + "\n"


def save_updates(user, account, core, plugins, themes):
    """Remember the choice and put the rules file on the website. Returns the ``SiteCare``."""
    if core not in dict(SiteCare.CORE_CHOICES):
        raise ServiceError("Choose how WordPress should update.", code="bad_choice")
    if not _wordpress_ready(account):
        raise ServiceError("Automatic updates are for WordPress websites that WHPanel set up.", code="not_wordpress")
    io = _Io(user, account)
    folder = f"{site_apps.docroot_path(account)}/{MU_PLUGIN_DIR}"
    parent = f"{site_apps.docroot_path(account)}/wp-content"
    names = {e.name for e in io.call(Capability.FILES, "list_files", parent)}
    if "mu-plugins" not in names:
        io.call(Capability.FILES, "make_directory", folder)
    path = f"{folder}/{MU_PLUGIN_FILE}"
    io.call(Capability.FILES, "write_file", path, rules_file(core, plugins, themes).encode(), overwrite=True,
            audit_metadata={"path": path, "core": core, "plugins": plugins, "themes": themes})
    care = care_for(account)
    care.wp_core, care.wp_plugins, care.wp_themes, care.updates_written_at = core, plugins, themes, timezone.now()
    care.save()
    return care


# --- Backups ----------------------------------------------------------------------------------------------------------

def backup_state(user, account):
    """What the customer can choose for automatic backups, or None when the server cannot make backups through WHPanel.

    ``mode`` is "provider" when the server schedules its own backups (the choice is passed to it) and "panel" when WHPanel's daily job takes them."""
    io = _Io(user, account)
    if not io.supports(Capability.BACKUPS):
        return None
    try:
        options = io.call(Capability.BACKUPS, "backup_options")
    except ServiceError:
        return None
    care = care_for(account)
    if len(options.schedules) > 1:
        return {"mode": "provider", "choices": list(options.schedules), "current": options.schedule, "last": options.last_scheduled_at, "note": ""}
    return {"mode": "panel", "choices": list(SiteCare.BACKUP_CHOICES), "current": care.backup_schedule, "last": care.last_backup_at.isoformat() if care.last_backup_at else None,
            "note": care.backup_note}


def save_backup_schedule(user, account, schedule):
    state = backup_state(user, account)
    if state is None:
        raise ServiceError("Backups are not available on this server yet.", code="capability_not_supported")
    if schedule not in dict(state["choices"]):
        raise ServiceError("Choose one of the listed schedules.", code="bad_choice")
    if state["mode"] == "provider":
        _Io(user, account).call(Capability.BACKUPS, "set_backup_schedule", schedule, audit_metadata={"schedule": schedule})
        return
    care = care_for(account)
    care.backup_schedule, care.backup_note = schedule, ""
    care.save()
    audit.record("hosting.backup_schedule_set", actor=user, target=account, metadata={"schedule": schedule, "mode": "panel"})


def _panel_backup(account, now):
    """Take one backup for an account WHPanel schedules itself, then keep only the newest few so the home folder does not fill. Returns a short text for the log."""
    io = _Io(None, account)
    care = care_for(account)
    try:
        options = io.call(Capability.BACKUPS, "backup_options")
        kind = options.kinds[0][0] if options.kinds else "full"
        existing = io.call(Capability.BACKUPS, "list_backups")
        if len(existing) >= KEEP_PANEL_BACKUPS:
            oldest = sorted(existing, key=lambda b: b.created_at or "")[: len(existing) - KEEP_PANEL_BACKUPS + 1]
            for backup in oldest:
                try:
                    io.call(Capability.BACKUPS, "delete_backup", backup.backup_id)
                except ServiceError:
                    io.call(Capability.FILES, "delete_path", backup.backup_id)  # the server's archives sit in the home folder as plain files
        io.call(Capability.BACKUPS, "create_backup", kind)
    except ServiceError as exc:
        care.backup_note = f"The last automatic backup did not run: {exc.message}"[:300]
        care.last_backup_at = now
        care.save()
        audit.record("hosting.scheduled_backup_failed", target=account, metadata={"error": exc.message})
        return "failed"
    care.backup_note, care.last_backup_at = "", now
    care.save()
    audit.record("hosting.scheduled_backup", target=account, metadata={"mode": "panel"})
    return "ok"


# --- What the overview shows -------------------------------------------------------------------------------------------

def _ago(moment, now):
    seconds = int((now - moment).total_seconds())
    if seconds < 3600:
        return "just now" if seconds < 120 else f"{seconds // 60} minutes ago"
    if seconds < 86400:
        hours = seconds // 3600
        return f"{hours} hour{'s' if hours != 1 else ''} ago"
    days = seconds // 86400
    return f"{days} day{'s' if days != 1 else ''} ago"


def summary(user, account, now=None):
    """Four short status lines for the overview: ``[{key, title, value, note, tone, icon}]``. Reads only what WHPanel stores, plus the backup options once."""
    now = now or timezone.now()
    settings = care_for(account)
    speed = SpeedTest.objects.filter(account=account, kind="server").first()
    scan = SiteScan.objects.filter(account=account).first()
    rows = []
    if speed is None:
        rows.append({"key": "speed", "title": "Speed", "value": "Not tested", "note": "Test how fast your website answers.", "tone": "idle", "icon": "bi-speedometer"})
    elif not speed.ok:
        rows.append({"key": "speed", "title": "Speed", "value": "Not reachable", "note": speed.error or "The home page answered with an error.", "tone": "warn", "icon": "bi-speedometer"})
    else:
        rows.append({"key": "speed", "title": "Speed", "value": f"{speed.score} / 100", "note": f"Tested {_ago(speed.created_at, now)}",
                     "tone": "ok" if speed.score >= 80 else "warn", "icon": "bi-speedometer"})
    if scan is None or scan.state == "failed":
        rows.append({"key": "scan", "title": "Security", "value": "Not scanned", "note": (scan.error if scan else "") or "Scan your website for malware.", "tone": "idle", "icon": "bi-shield-check"})
    elif scan.state == "running":
        rows.append({"key": "scan", "title": "Security", "value": "Scanning...", "note": "This page updates when it is done.", "tone": "idle", "icon": "bi-shield-check"})
    elif scan.findings:
        rows.append({"key": "scan", "title": "Security", "value": f"{len(scan.findings)} to review", "note": f"Scanned {_ago(scan.finished_at or scan.started_at, now)}", "tone": "bad",
                     "icon": "bi-shield-exclamation"})
    else:
        rows.append({"key": "scan", "title": "Security", "value": "No problems found", "note": f"Scanned {_ago(scan.finished_at or scan.started_at, now)}", "tone": "ok",
                     "icon": "bi-shield-check"})
    if _wordpress_ready(account):
        label = dict(SiteCare.CORE_CHOICES)[settings.wp_core]
        extra = " + plugins" if settings.wp_plugins else ""
        extra += " + themes" if settings.wp_themes else ""
        rows.append({"key": "updates", "title": "WordPress updates", "value": "Automatic" if settings.updates_written_at else "Default",
                     "note": (label + extra) if settings.updates_written_at else "WordPress's own default: security releases only.",
                     "tone": "ok" if settings.updates_written_at and settings.wp_core != "off" else "idle", "icon": "bi-arrow-repeat"})
    try:
        state = backup_state(user, account)
    except ServiceError:
        state = None
    if state is not None:
        current = dict(state["choices"]).get(state["current"], state["current"])
        on = state["current"] != "off"
        rows.append({"key": "backups", "title": "Backups", "value": current if on else "Off", "note": "Automatic backups" if on else "Choose how often to back up.",
                     "tone": "ok" if on else "idle", "icon": "bi-cloud-arrow-up"})
    return rows


# --- The panel's daily job --------------------------------------------------------------------------------------------

def run_due(now=None):
    """Called daily: the scans and panel-scheduled backups that are due. Returns {"scans": n, "backups": n}."""
    now = now or timezone.now()
    done = {"scans": 0, "backups": 0}
    rows = SiteCare.objects.exclude(scan_schedule="off", backup_schedule="off").select_related("account__server")
    for care in rows:
        account = care.account
        if account.status != HostingStatus.ACTIVE or account.server is None:
            continue
        period = PERIOD.get(care.scan_schedule)
        if period is not None:
            last = SiteScan.objects.filter(account=account, state__in=["done", "failed"]).first()
            if last is None or now - (last.finished_at or last.started_at) >= period - timedelta(hours=1):
                start_scan(None, account, trigger="scheduled")
                done["scans"] += 1
        period = PERIOD.get(care.backup_schedule)
        if period is not None and (care.last_backup_at is None or now - care.last_backup_at >= period - timedelta(hours=1)):
            if _Io(None, account).supports(Capability.BACKUPS):
                _panel_backup(account, now)
                done["backups"] += 1
    return done
