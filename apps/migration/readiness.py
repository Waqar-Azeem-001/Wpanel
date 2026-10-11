"""
Is cPanel ready to be retired? (M4)  Every condition in the roadmap, each either MEASURED here from WHPanel's own records or SIGNED OFF by a named person who says what they saw.
Nothing is ticked on WHPanel's say-so alone for things only a real node can show (isolation, restores, support readiness...). While any line is open, the cPanel provider, the
"open cPanel" link and the WHM credentials stay in the code; removing them is a change made by a person once every line here is green.
"""
from datetime import timedelta

from django.utils import timezone

from apps.agent.models import AgentJob, AgentNode, JobStatus, NodeStatus
from apps.audit import services as audit
from apps.core.exceptions import ServiceError
from apps.hosting.models import LIVE_STATUSES, HostingAccount
from apps.products.models import ServerKind

from .models import Migration, MigrationState as S, RetirementSignoff

SIGNED = (
    ("features", "Every customer feature works on the native stack", "Files, databases, mail, DNS, SSL and backups: tried by a person on a real node."),
    ("provisioning", "Native provisioning is verified", "New accounts are created on a real node and the websites work."),
    ("isolation", "Isolation is verified", "The cross-tenant probe and noisy-neighbour test were run on a real node and passed."),
    ("backups", "Backups are verified", "Backups run on schedule into the real bucket."),
    ("restores", "Restores are verified", "A restore test and a real restore were done from the real bucket."),
    ("monitoring", "Monitoring is verified", "Alerts reach a person; a real failure was noticed."),
    ("security", "The security audit passed", "An external or internal audit of the native stack is signed off."),
    ("rollback", "Rollback procedures were tested", "A real migration was rolled back and the customer's site kept working."),
    ("operations", "The operations team can support the native stack", "Someone other than the builder has run the runbooks."),
)


def _evidence(key):
    if key == "provisioning":
        n = AgentJob.objects.filter(command="CREATE_WEBSITE", status=JobStatus.SUCCEEDED).count()
        return f"{n} website(s) created through agents so far (a pretend node counts here too: check they were real)."
    if key == "isolation":
        n = AgentJob.objects.filter(command="ISOLATION_CHECK", status=JobStatus.SUCCEEDED).count()
        return f"{n} isolation check(s) have run."
    if key == "restores":
        n = AgentJob.objects.filter(command__in=("BACKUP_TEST_RESTORE", "BACKUP_RESTORE"), status=JobStatus.SUCCEEDED).count()
        return f"{n} restore test(s) or restore(s) have succeeded."
    if key == "monitoring":
        nodes = AgentNode.objects.filter(status=NodeStatus.ENROLLED)
        return f"{nodes.count()} enrolled node(s); {nodes.exclude(monitor_state='healthy').count()} not healthy."
    return ""


def checks():
    signoffs = {s.key: s for s in RetirementSignoff.objects.select_related("confirmed_by")}
    out = []
    for key, label, hint in SIGNED:
        s = signoffs.get(key)
        out.append({"key": key, "label": label, "hint": hint, "ok": s is not None, "kind": "signoff", "evidence": _evidence(key),
                    "detail": f"Signed off by {s.confirmed_by or 'someone'}: {s.note}" if s else "Needs a person to confirm."})
    done = Migration.objects.filter(state__in=(S.WINDOW, S.COMPLETE)).count()
    bad = Migration.objects.filter(state__in=(S.FAILED, S.MANUAL_REVIEW)).count()
    out.append({"key": "migrations", "label": "Migrations are validated", "hint": "At least one migration is switched over or complete and none is failed or waiting for a person.",
                "ok": done > 0 and bad == 0, "kind": "measured", "evidence": "", "detail": f"{done} switched over or complete, {bad} failed or needing a person."})
    remaining = HostingAccount.objects.filter(status__in=LIVE_STATUSES, server__kind=ServerKind.WHM_API).count()
    out.append({"key": "no_dependency", "label": "No live account depends on cPanel", "hint": "", "ok": remaining == 0, "kind": "measured", "evidence": "",
                "detail": f"{remaining} live account(s) are still on a cPanel server."})
    since = timezone.now() - timedelta(days=30)
    fresh = HostingAccount.objects.filter(server__kind=ServerKind.WHM_API, created_at__gte=since).count()
    out.append({"key": "no_new", "label": "No new account was made on cPanel in the last 30 days", "hint": "Native-first provisioning (M1).", "ok": fresh == 0, "kind": "measured",
                "evidence": "", "detail": f"{fresh} account(s) were made on cPanel in the last 30 days."})
    return out


def ready():
    return all(c["ok"] for c in checks())


def sign(user, key, note):
    if key not in {k for k, _, _ in SIGNED}:
        raise ServiceError("Unknown condition.", code="unknown")
    if len((note or "").strip()) < 10:
        raise ServiceError("Say what you saw (at least a sentence), so the sign-off means something.", code="note_too_short")
    RetirementSignoff.objects.update_or_create(key=key, defaults={"confirmed_by": user, "note": note.strip()[:500]})
    audit.record("migration.signoff", actor=user, metadata={"key": key, "note": note.strip()[:200]})


def withdraw(user, key):
    RetirementSignoff.objects.filter(key=key).delete()
    audit.record("migration.signoff_withdrawn", actor=user, metadata={"key": key})
