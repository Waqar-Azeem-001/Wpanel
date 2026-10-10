"""
Monitoring and controlled self-healing for native nodes (N10).

ONE place decides what a node's state is (``evaluate``), so the staff screens, the alert emails and the repair logic can never disagree. The four states are the roadmap's:
**healthy, warning, critical, offline** (plus ``pending`` and ``revoked`` for nodes that are not running yet or any more).

A figure the node did not report is *unknown*: it is neither a pass nor a fail, and it never turns a node healthy or unhealthy on its own. A state change has to be seen twice in a row before it is
settled, so one odd reading does not raise an alarm; each settled change is recorded and (for a worse state, and when it recovers) emailed to ``MONITORING_ALERT_EMAILS``; a node that stays bad is
reminded about every six hours.

Self-healing is OFF unless ``SELF_HEALING_ENABLED`` is true, because nothing here has run on a real node. When on, it may only restart a service from the short list in ``catalog.HEAL_SERVICES``, only
when that service is reported down on a node WHPanel has confirmed critical, and only within strict limits (see ``heal``). It never deletes, kills, reboots or edits anything.
"""
from datetime import timedelta

from django.conf import settings
from django.core.mail import send_mail
from django.utils import timezone

from apps.audit import services as audit

from . import catalog, services
from .models import AgentJob, AgentNode, HealingAction, JobStatus, NodeStateChange, NodeStatus

OFFLINE_AFTER = timedelta(minutes=3)
CONFIRM = 2  # sweeps in a row a different state must be seen before it is settled
REMIND_AFTER = timedelta(hours=6)
ORDER = {"healthy": 0, "warning": 1, "critical": 2}
THRESHOLDS = {  # (warning at, critical at)
    "cpu": (90, 98), "memory": (90, 97), "disk": (85, 95), "load": (1.5, 3.0), "mail_queue": (200, 1000), "net_errors": (100, 10000),
    "certs": (14, 3),  # days left: at or below
    "job_wait": (5, 30),  # minutes the oldest queued command has waited
}
BACKUP_STALE = timedelta(days=3)
HEAL_PER_HOUR = 3  # at most this many restarts of one service per hour
HEAL_GAP = timedelta(minutes=5)  # and at least this long between two


def _high(name, value, label, unit="%"):
    warn, crit = THRESHOLDS[name]
    state = "critical" if value >= crit else "warning" if value >= warn else "healthy"
    return {"name": label, "state": state, "detail": f"{value:g}{unit}"}


def _low(name, value, label):
    warn, crit = THRESHOLDS[name]
    state = "critical" if value <= crit else "warning" if value <= warn else "healthy"
    return {"name": label, "state": state, "detail": f"{value} days left"}


def evaluate(node, now=None):
    """{"state", "checks": [{"name", "state", "detail"}]} for one node, from its last heartbeat and what WHPanel knows. Pure reading; changes nothing."""
    now = now or timezone.now()
    if node.status != NodeStatus.ENROLLED:
        return {"state": node.status, "checks": []}
    if node.last_seen_at is None or now - node.last_seen_at > OFFLINE_AFTER:
        since = "never" if node.last_seen_at is None else f"{int((now - node.last_seen_at).total_seconds() // 60)} minutes ago"
        return {"state": "offline", "checks": [{"name": "agent contact", "state": "offline", "detail": f"last heard {since}"}]}
    h = node.health or {}
    checks = [{"name": "agent contact", "state": "healthy", "detail": "reporting"}]
    if h.get("cpu_percent") is not None:
        checks.append(_high("cpu", h["cpu_percent"], "CPU"))
    cores = node.cpu_cores or 0
    if h.get("load_1m") is not None and cores:
        checks.append({**_high("load", h["load_1m"] / cores, "load per core", ""), "detail": f"{h['load_1m']:g} on {cores} cores"})
    total = h.get("memory_total_mb") or node.ram_mb
    if h.get("memory_used_mb") is not None and total:
        checks.append(_high("memory", h["memory_used_mb"] * 100 / total, "memory"))
    total = h.get("disk_total_gb") or node.disk_gb
    if h.get("disk_used_gb") is not None and total:
        checks.append(_high("disk", h["disk_used_gb"] * 100 / total, "disk"))
    if h.get("net_errors") is not None:
        checks.append(_high("net_errors", h["net_errors"], "network errors", " new"))
    for name, state in sorted((h.get("services") or {}).items()):
        checks.append({"name": f"service {name}", "state": {"up": "healthy", "degraded": "warning", "down": "critical"}[state], "detail": state})
    if h.get("mail_queue") is not None:
        checks.append(_high("mail_queue", h["mail_queue"], "mail queue", " waiting"))
    if h.get("certs_min_days") is not None:
        checks.append(_low("certs", h["certs_min_days"], "certificates"))
    checks += _backup_checks(node, now)
    if node.reputation_at and not (node.reputation or {}).get("ok", True):
        listed = bool((node.reputation or {}).get("listed"))
        checks.append({"name": "mail reputation", "state": "critical" if listed else "warning", "detail": "on a blacklist" if listed else "mail queue is high"})
    first = AgentJob.objects.filter(node=node, status=JobStatus.QUEUED).order_by("created_at").first()
    if first is not None:
        minutes = (now - first.created_at).total_seconds() / 60
        checks.append({**_high("job_wait", minutes, "waiting commands", " min"), "detail": f"oldest has waited {int(minutes)} min"})
    worst = max((c["state"] for c in checks), key=lambda s: ORDER.get(s, 0))
    return {"state": worst, "checks": checks}


def _backup_checks(node, now):
    from apps.hosting.models import NativeBackup

    checks = []
    repo = getattr(node, "backup_repository", None)
    if "web" in (node.capabilities or []) and (repo is None or not repo.applied_ok):
        checks.append({"name": "backup storage", "state": "warning", "detail": "not set up on this node"})
        return checks
    if repo is None:
        return checks
    mine = NativeBackup.objects.filter(node=node)
    if mine.filter(status="failed", created_at__gte=now - timedelta(hours=24)).exists():
        checks.append({"name": "backups", "state": "warning", "detail": "a backup failed in the last day"})
    last = mine.filter(status="complete").order_by("-created_at").first()
    scheduled = mine.exclude(account__native_backup_settings__schedule="off").exclude(account__native_backup_settings=None).exists()
    if scheduled and (last is None or now - last.created_at > BACKUP_STALE):
        checks.append({"name": "backups", "state": "warning", "detail": "no successful backup for over three days"})
    tested = mine.exclude(restore_tested_at=None).order_by("-restore_tested_at").first()
    if tested is not None and tested.restore_test_ok is False:
        checks.append({"name": "restore test", "state": "critical", "detail": "the last test restore FAILED"})
    return checks


def state_of(node, now=None):
    return evaluate(node, now)["state"]


# --- platform checks (things that are not one node's) ------------------------------------------------------------------------------

def platform_checks():
    """DNS reachability, and whether WHPanel's own command queue is moving. Each is {"name", "state", "detail"}."""
    from apps.hosting.adapters import native_dns

    out = []
    client = native_dns.get_dns()
    if client is None:
        out.append({"name": "DNS servers", "state": "healthy", "detail": "not configured (native DNS is off)"})
    else:
        try:
            client.ping()
            out.append({"name": "DNS servers", "state": "healthy", "detail": "answering"})
        except Exception as exc:  # noqa: BLE001
            out.append({"name": "DNS servers", "state": "critical", "detail": str(exc)[:120]})
    failed = AgentJob.objects.filter(status__in=(JobStatus.FAILED, JobStatus.TIMED_OUT), finished_at__gte=timezone.now() - timedelta(hours=1)).count()
    total = AgentJob.objects.filter(finished_at__gte=timezone.now() - timedelta(hours=1)).count()
    bad = total >= 10 and failed * 100 / total >= 30
    out.append({"name": "agent commands", "state": "warning" if bad else "healthy", "detail": f"{failed} of {total} failed in the last hour"})
    return out


# --- the sweep ----------------------------------------------------------------------------------------------------------------------

def _alert(node, result, kind):
    recipients = [a.strip() for a in getattr(settings, "MONITORING_ALERT_EMAILS", "").split(",") if a.strip()]
    if not recipients:
        return False
    reasons = "\n".join(f"- {c['name']}: {c['detail']}" for c in result["checks"] if c["state"] != "healthy") or "- all checks are healthy"
    state = result["state"].upper()
    subject = {"recovered": f"[WHPanel] {node.server.name} has recovered", "reminder": f"[WHPanel] {node.server.name} is still {state}"}.get(kind, f"[WHPanel] {node.server.name} is {state}")
    try:
        send_mail(subject, f"{node.server.name} is {state}.\n\n{reasons}\n\nSee the Native nodes screen in WHPanel.", settings.DEFAULT_FROM_EMAIL, recipients)
        return True
    except Exception as exc:  # noqa: BLE001 - a broken mail setup must not stop the monitoring itself
        audit.record("agent.monitoring_alert_failed", target=node, metadata={"error": str(exc)[:200]})
        return False


def sweep(now=None):
    """The scheduled job (every minute). For every enrolled node: evaluate, settle state changes, alert, and (when enabled) heal. Returns {node name: settled state}."""
    now = now or timezone.now()
    states = {}
    for node in AgentNode.objects.select_related("server").filter(status=NodeStatus.ENROLLED):
        result = evaluate(node, now)
        new = result["state"]
        reasons = [f"{c['name']}: {c['detail']}" for c in result["checks"] if c["state"] not in ("healthy",)]
        if new != node.monitor_state:
            node.monitor_pending_count = node.monitor_pending_count + 1 if node.monitor_pending == new else 1
            node.monitor_pending = new
            if node.monitor_pending_count >= CONFIRM:
                previous = node.monitor_state
                NodeStateChange.objects.create(node=node, state=new, previous=previous, reasons=reasons)
                node.monitor_state, node.monitor_since, node.monitor_pending, node.monitor_pending_count = new, now, "", 0
                node.monitor_reasons, node.monitor_alerted_at = reasons, now
                audit.record("agent.node_state_changed", target=node, metadata={"from": previous, "to": new, "reasons": reasons})
                if new != "healthy":
                    _alert(node, result, "state")
                elif previous != "healthy":
                    _alert(node, result, "recovered")
        else:
            node.monitor_pending, node.monitor_pending_count, node.monitor_reasons = "", 0, reasons
            if new in ("critical", "offline") and node.monitor_alerted_at and now - node.monitor_alerted_at > REMIND_AFTER:
                node.monitor_alerted_at = now
                _alert(node, result, "reminder")
        node.save(update_fields=["monitor_state", "monitor_since", "monitor_reasons", "monitor_pending", "monitor_pending_count", "monitor_alerted_at", "updated_at"])
        if getattr(settings, "SELF_HEALING_ENABLED", False) and node.monitor_state == "critical":
            heal(node, result, now)
        states[node.server.name] = node.monitor_state
    return states


# --- controlled self-healing --------------------------------------------------------------------------------------------------------

def heal(node, result, now=None):
    """
    Restart a failed service, within strict limits. All of these must hold, or nothing is done:
    the node is settled critical and not in maintenance; the failing service is one of ``catalog.HEAL_SERVICES`` and reported *down* (a struggling one is left alone); no command is running on the node
    right now; the service has not been restarted more than 3 times in the last hour nor within the last 5 minutes. After three failed restarts in a row it gives up, says so once and waits for a person.
    Returns the HealingAction started, or None.
    """
    from apps.products.models import ServerStatus

    now = now or timezone.now()
    if node.server.status != ServerStatus.ACTIVE:
        return None
    down = [c["name"].split(" ", 1)[1] for c in result["checks"] if c["name"].startswith("service ") and c["state"] == "critical" and c["name"].split(" ", 1)[1] in catalog.HEAL_SERVICES]
    if not down or AgentJob.objects.filter(node=node, status=JobStatus.LEASED).exists():
        return None
    for service in down:
        recent = list(HealingAction.objects.filter(node=node, service=service, started_at__gte=now - timedelta(hours=1)).exclude(status="gave_up").order_by("-started_at"))
        if recent and now - recent[0].started_at < HEAL_GAP:
            continue
        if len(recent) >= HEAL_PER_HOUR:
            if not HealingAction.objects.filter(node=node, service=service, status="gave_up", started_at__gte=now - timedelta(hours=1)).exists():
                HealingAction.objects.create(node=node, service=service, status="gave_up", reason="restarts did not fix it", detail="Needs a person.")
                audit.record("agent.healing_gave_up", target=node, metadata={"service": service})
                _alert(node, {"state": "critical", "checks": [{"name": f"{service} is down", "state": "critical", "detail": "automatic restarts did not fix it; a person is needed"}]}, "state")
            continue
        action = HealingAction.objects.create(node=node, service=service, reason=f"{service} was reported down")
        try:
            services.dispatch(None, node.server, "SERVICE_RESTART", {"service": service}, subject=f"heal:{action.pk}")
        except Exception as exc:  # noqa: BLE001
            action.status, action.detail = "failed", str(exc)[:300]
            action.save(update_fields=["status", "detail"])
            continue
        audit.record("agent.healing_started", target=node, metadata={"service": service})
        return action
    return None


def job_finished(job):
    """The node reported on a restart. Records the outcome (it is also what limits the next attempt)."""
    try:
        action = HealingAction.objects.select_related("node__server").get(pk=int(job.subject.split(":", 1)[1]))
    except (ValueError, HealingAction.DoesNotExist):
        return
    ok = job.result_ok is True
    action.status, action.detail = ("ok" if ok else "failed"), (job.result_message or job.get_status_display())[:300]
    action.save(update_fields=["status", "detail"])
    audit.record("agent.healing_" + ("succeeded" if ok else "failed"), target=action.node, metadata={"service": action.service, "detail": action.detail})
