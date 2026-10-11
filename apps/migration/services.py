"""
Moving a cPanel account to a native node (M2/M3, ADR 19). One ``Migration`` per account walks through fixed stages; every step that touches a server is an allow-listed agent command
(``MIGRATE_*`` and the ordinary website commands), so the node does the work and WHPanel records the outcome. A person approves the two decisions that matter: *start importing*
(after reading the report) and *cut over*.

    awaiting_archive -> analysing -> ready -> importing -> validated -> window -> complete
                            |            |                      |          |
                      manual_review    failed                 failed    rolled_back

What is NOT automated and why:
* Making the cPanel backup (``pkgacct``) and copying it to the node: it is done by an operator with two commands (docs/runbooks/cpanel-migration.md). WHPanel can only check that it arrived.
* The cPanel account is never deleted or changed by this module. After the rollback window staff retire it by hand.
* DNS is changed only when WHPanel itself hosts the zone; otherwise the new address is shown for the customer or staff to set.
"""
from datetime import timedelta

from django.db import transaction
from django.utils import timezone

from apps.agent import catalog
from apps.agent.models import AgentNode, NodeStatus
from apps.agent import services as agent
from apps.audit import services as audit
from apps.core.exceptions import ServiceError
from apps.hosting.models import LIVE_STATUSES, HostingAccount
from apps.products.models import Server, ServerKind

from .models import ACTIVE, Migration, MigrationEvent, MigrationState as S

SUBJECT = "migrate:"
DEFAULT_ARCHIVE = "cpmove-{user}.tar.gz"


def _require(user, codename="manage_hosting"):
    from apps.accounts.roles import perm

    if user is not None and not user.has_perm(perm(codename)):
        raise ServiceError("You do not have permission to do that.", code="forbidden")


def event(m, stage, message, ok=True):
    MigrationEvent.objects.create(migration=m, stage=stage, ok=ok, message=message[:500])


def _can(server, capability):
    node = AgentNode.objects.filter(server=server, status=NodeStatus.ENROLLED).first()
    return node is not None and capability in (node.capabilities or [])


def _node_ip(server):
    node = AgentNode.objects.filter(server=server).first()
    return server.ip_address or next((a for a in (node.ip_addresses if node else []) if ":" not in a), "")


def _dispatch(m, server, command, params, key):
    return agent.dispatch(None, server, command, params, idempotency_key=f"mig-{m.pk}-{m.attempt}-{key}", subject=f"{SUBJECT}{m.pk}")


# --- start ------------------------------------------------------------------------------------------------------------------------------------

@transaction.atomic
def start(user, account, target, *, mail_target=None, window_days=7, expected_sha256="", request=None):
    _require(user)
    if account.server is None or account.server.kind != ServerKind.WHM_API:
        raise ServiceError("Only an account that lives on a cPanel server can be migrated.", code="not_cpanel")
    if account.status not in LIVE_STATUSES:
        raise ServiceError("Only a live account can be migrated.", code="not_live")
    if target.kind != ServerKind.NATIVE or not _can(target, "web"):
        raise ServiceError("Choose an enrolled native node that hosts websites.", code="bad_target")
    if mail_target is not None and not _can(mail_target, "mail"):
        raise ServiceError("That node does not hold mail.", code="bad_mail_target")
    if not catalog.ACCOUNT.fullmatch(account.username):
        raise ServiceError(f"The username {account.username} cannot be used on a native node (3 to 16 lowercase letters and digits, starting with a letter).", code="bad_username")
    if Migration.objects.filter(account=account, state__in=[s.value for s in ACTIVE]).exists():
        raise ServiceError("This account already has a migration in progress.", code="active_migration")
    if expected_sha256 and not catalog.SHA256.fullmatch(expected_sha256):
        raise ServiceError("That is not a SHA-256 checksum.", code="bad_checksum")
    m = Migration.objects.create(account=account, source=account.server, target=target, mail_target=mail_target, window_days=window_days, expected_sha256=expected_sha256.lower(),
                                 archive_name=DEFAULT_ARCHIVE.format(user=account.username), created_by=user if getattr(user, "is_authenticated", False) else None)
    event(m, "start", f"Migration of {account.username} ({account.domain}) from {account.server.name} to {target.name} started.")
    audit.record("migration.started", actor=user, target=account, metadata={"migration": m.pk, "from": m.source.name, "to": target.name}, request=request)
    return m


def instructions(m):
    """The two commands an operator runs, written for this account (shown on the screen)."""
    node = m.target
    return {"archive": m.archive_name, "make": f"/scripts/pkgacct {m.account.username} /root/migrate",
            "copy": f"rsync -av /root/migrate/{m.archive_name} root@{node.hostname}:/var/lib/whp/stage/migrations/",
            "check": f"sha256sum /root/migrate/{m.archive_name}"}


# --- analysis ---------------------------------------------------------------------------------------------------------------------------------

def check_archive(user, m):
    """Ask the target node to read the backup that should have arrived. Safe to repeat."""
    _require(user)
    if m.state not in (S.AWAITING_ARCHIVE, S.MANUAL_REVIEW, S.READY, S.ANALYSING):
        raise ServiceError("This migration is past the analysis stage.", code="bad_state")
    params = {"account": m.account.username, "archive": m.archive_name}
    if m.expected_sha256:
        params["expected_sha256"] = m.expected_sha256
    m.attempt += 1
    m.state, m.last_error = S.ANALYSING, ""
    m.save(update_fields=["attempt", "state", "last_error", "updated_at"])
    _dispatch(m, m.target, "MIGRATE_ANALYZE", params, "analyze")
    event(m, "discovery", "Asked the node to read the backup.")
    return m


def approve(user, m, *, accept_warnings=False):
    """A person has read the report and says: import it."""
    _require(user)
    if m.state != S.READY:
        raise ServiceError("There is no clean report to approve yet.", code="bad_state")
    if m.report.get("warnings") and not accept_warnings:
        raise ServiceError("Read the warnings and tick that you accept them.", code="warnings_not_accepted")
    m.state, m.step, m.parts_done, m.last_error = S.IMPORTING, "", [], ""
    m.save(update_fields=["state", "step", "parts_done", "last_error", "updated_at"])
    audit.record("migration.approved", actor=user, target=m.account, metadata={"migration": m.pk, "warnings": len(m.report.get("warnings", []))})
    event(m, "import", "Import approved.")
    _advance(m)
    return m


def retry(user, m):
    _require(user)
    if m.state != S.FAILED:
        raise ServiceError("Only a failed migration can be retried.", code="bad_state")
    if m.step == "":
        return check_archive(user, m)
    m.attempt += 1
    m.state, m.last_error = S.IMPORTING, ""
    m.save(update_fields=["attempt", "state", "last_error", "updated_at"])
    event(m, "import", f"Retrying the {m.step} step.")
    _advance(m)
    return m


def cancel(user, m):
    _require(user)
    if m.state in (S.WINDOW, S.COMPLETE, S.ROLLED_BACK, S.CANCELLED):
        raise ServiceError("A migration that has been switched over is rolled back, not cancelled.", code="bad_state")
    m.state = S.CANCELLED
    m.save(update_fields=["state", "updated_at"])
    event(m, "cancel", "Cancelled. The cPanel account was never changed.")
    audit.record("migration.cancelled", actor=user, target=m.account, metadata={"migration": m.pk})
    return m


# --- import chain -----------------------------------------------------------------------------------------------------------------------------

def _plan(m):
    """The steps this migration needs, in order."""
    steps = ["site", "files"]
    if m.report.get("databases"):
        steps.append("databases")
    if m.report.get("mail") and m.mail_target is not None:
        steps += ["maildomain", "mail"]
    return steps + ["validate"]


def _advance(m):
    from apps.hosting.adapters.registry import build_adapter

    username, domain = m.account.username, m.account.domain.lower()
    for step in _plan(m):
        if step in m.parts_done:
            continue
        m.step = step
        m.save(update_fields=["step", "updated_at"])
        base = {"account": username, "archive": m.archive_name}
        if step == "site":
            limits = build_adapter(m.target)._limits(m.account.package_name)
            _dispatch(m, m.target, "CREATE_WEBSITE", {"account": username, "domain": domain, "php_version": m.report.get("php_target") or "8.2", **limits}, "site")
        elif step == "files":
            _dispatch(m, m.target, "MIGRATE_IMPORT", {**base, "part": "files"}, "files")
        elif step == "databases":
            _dispatch(m, m.target, "MIGRATE_IMPORT", {**base, "part": "databases"}, "databases")
        elif step == "maildomain":
            _dispatch(m, m.mail_target, "MAIL_DOMAIN_ADD", {"account": username, "domain": domain}, "maildomain")
        elif step == "mail":
            _dispatch(m, m.mail_target, "MIGRATE_IMPORT", {**base, "part": "mail"}, "mail")
        elif step == "validate":
            _dispatch(m, m.target, "MIGRATE_VALIDATE", base, "validate")
        return
    return


def job_finished(job):
    """The node has answered one of this migration's commands."""
    try:
        m = Migration.objects.select_related("account", "target", "mail_target", "source").get(pk=int(job.subject[len(SUBJECT):]))
    except (ValueError, Migration.DoesNotExist):
        return
    if m.state in (S.CANCELLED, S.ROLLED_BACK, S.COMPLETE):
        return
    ok = job.result_ok is True
    if job.command == "MIGRATE_ANALYZE":
        return _analysed(m, job, ok)
    if m.state != S.IMPORTING:
        return
    if not ok:
        m.state, m.last_error = S.FAILED, job.result_message or job.get_status_display()
        m.save(update_fields=["state", "last_error", "updated_at"])
        event(m, m.step or "import", f"{job.command} failed: {m.last_error}", ok=False)
        audit.record("migration.step_failed", target=m.account, metadata={"migration": m.pk, "step": m.step, "error": m.last_error})
        return
    if job.command == "MIGRATE_VALIDATE":
        m.validation = job.result_data or {}
        if m.validation.get("ok"):
            m.state = S.VALIDATED
            m.parts_done = [*m.parts_done, "validate"]
            event(m, "validation", f"Everything matched: {m.validation.get('files_expected', 0)} files, {len(m.validation.get('databases', []))} database(s).")
        else:
            m.state, m.last_error = S.MANUAL_REVIEW, f"{m.validation.get('files_missing', 0)} file(s) or a database are missing on the node."
            event(m, "validation", m.last_error, ok=False)
        m.save(update_fields=["state", "validation", "parts_done", "last_error", "updated_at"])
        return
    detail = job.result_data or {}
    summary = ", ".join(f"{k}: {v}" for k, v in detail.items() if isinstance(v, (int, str)) and v != "")[:200]
    m.parts_done = [*m.parts_done, m.step]
    m.save(update_fields=["parts_done", "updated_at"])
    event(m, m.step, f"{job.command} done" + (f" ({summary})" if summary else "") + ".")
    _advance(m)


def _analysed(m, job, ok):
    if not ok:
        missing = job.result_code == "not_found"
        m.state = S.AWAITING_ARCHIVE if missing else S.FAILED
        m.last_error = job.result_message or job.get_status_display()
        m.save(update_fields=["state", "last_error", "updated_at"])
        event(m, "discovery", ("The backup has not arrived yet: " if missing else "Reading the backup failed: ") + m.last_error, ok=False)
        return
    report = {k: v for k, v in (job.result_data or {}).items()}
    m.report = report
    if report.get("user") and report["user"] != m.account.username:
        report.setdefault("warnings", []).append(f"The backup is of the cPanel account {report['user']}, not {m.account.username}.")
        report.setdefault("blockers", []).append("The backup belongs to a different cPanel account.")
    if report.get("main_domain") and report["main_domain"].lower() != m.account.domain.lower():
        report.setdefault("blockers", []).append(f"The backup's main domain is {report['main_domain']}, but this account's is {m.account.domain}.")
    if report.get("blockers"):
        m.state, m.last_error = S.MANUAL_REVIEW, "; ".join(report["blockers"])[:500]
        event(m, "compatibility", f"Needs a person: {m.last_error}", ok=False)
    else:
        m.state, m.last_error = S.READY, ""
        event(m, "compatibility", f"Compatible. {len(report.get('warnings', []))} warning(s), {report.get('files', 0)} files, {len(report.get('databases', []))} database(s), "
                                  f"{sum(len(x['mailboxes']) for x in report.get('mail', []))} mailbox(es).")
    m.save(update_fields=["report", "state", "last_error", "updated_at"])


# --- site check, cutover, rollback, completion --------------------------------------------------------------------------------------------------

def check_site(m, fetch=None):
    """Does the site answer from the new node, before DNS points at it? (Host header sent straight to the node's address.)"""
    ip = _node_ip(m.target)
    if not ip:
        raise ServiceError("The node's address is not known.", code="no_ip")
    if fetch is None:
        import requests

        def fetch(url, host):
            return requests.get(url, headers={"Host": host}, timeout=10, allow_redirects=False).status_code
    try:
        status = fetch("http" + f"://{ip}/", m.account.domain.lower())
    except Exception as exc:  # noqa: BLE001 - any network failure is a "no" with a reason
        status, note = 0, str(exc)[:120]
    else:
        note = ""
    ok = 200 <= status < 400
    m.validation = {**m.validation, "site": {"status": status, "ok": ok, "note": note, "at": timezone.now().isoformat()}}
    m.save(update_fields=["validation", "updated_at"])
    event(m, "validation", f"The site {'answered' if ok else 'did not answer'} from {m.target.name} (status {status}).", ok=ok)
    return ok


def _dns(domain):
    from apps.hosting.adapters.native_dns import get_dns

    client = get_dns()
    return client if client is not None and client.zone_exists(domain) else None


@transaction.atomic
def cutover(user, m, *, request=None):
    _require(user)
    if m.state != S.VALIDATED:
        raise ServiceError("Only an imported and checked migration can be switched over.", code="bad_state")
    from apps.hosting.adapters.powerdns import fqdn

    account, domain, ip = m.account, m.account.domain.lower(), _node_ip(m.target)
    if not ip:
        raise ServiceError("The target node has no known address to point the website at.", code="no_ip")
    note = f"DNS is not hosted by WHPanel. Point the A record of {domain} (and www) to {ip}."
    dns = _dns(domain)
    if dns is not None:
        name = fqdn("@", domain)
        old = next(((ttl, contents) for n, t, ttl, contents in dns.rrsets(domain) if n == name and t == "A"), None)
        m.previous_dns = {"ttl": old[0], "contents": old[1]} if old else {}
        dns.set_rrset(domain, name, "A", 300, [ip])
        note = f"DNS updated: {domain} now points to {ip} (was {', '.join(old[1]) if old else 'nothing'})."
    m.dns_note = note
    account.server = m.target
    if m.mail_target is not None and "mail" in m.parts_done:
        account.mail_server = m.mail_target
    account.save(update_fields=["server", "mail_server", "updated_at"])
    m.state, m.cutover_at = S.WINDOW, timezone.now()
    m.rollback_until = m.cutover_at + timedelta(days=m.window_days)
    m.save(update_fields=["state", "cutover_at", "rollback_until", "dns_note", "previous_dns", "updated_at"])
    event(m, "cutover", note)
    audit.record("migration.cutover", actor=user, target=account, metadata={"migration": m.pk, "to": m.target.name, "dns": dns is not None}, request=request)
    return m


@transaction.atomic
def rollback(user, m, *, request=None):
    """Put the account back on cPanel. The cPanel account was never touched, so this is switching pointers, not restoring data (changes made on the new node meanwhile are NOT copied back)."""
    _require(user)
    if m.state != S.WINDOW:
        raise ServiceError("Only a switched-over migration can be rolled back.", code="bad_state")
    from apps.hosting.adapters.powerdns import fqdn

    account, domain = m.account, m.account.domain.lower()
    dns = _dns(domain)
    note = f"DNS is not hosted by WHPanel: point {domain} back to the cPanel server's address."
    if dns is not None:
        if m.previous_dns.get("contents"):
            dns.set_rrset(domain, fqdn("@", domain), "A", m.previous_dns.get("ttl", 3600), m.previous_dns["contents"])
            note = f"DNS put back: {domain} points to {', '.join(m.previous_dns['contents'])} again."
        else:
            note = "DNS had no address record before the cutover, so none was restored: set it by hand."
    account.server, account.mail_server = m.source, None
    account.save(update_fields=["server", "mail_server", "updated_at"])
    m.state, m.dns_note = S.ROLLED_BACK, note
    m.save(update_fields=["state", "dns_note", "updated_at"])
    event(m, "rollback", "Rolled back to the cPanel server. " + note + " Anything the customer changed on the new node since the cutover stays there.")
    audit.record("migration.rolled_back", actor=user, target=account, metadata={"migration": m.pk}, request=request)
    return m


def finish(user, m, *, force=False):
    """The rollback window is over: the migration is complete and the staged backup is deleted from the node. The cPanel account is NOT removed here."""
    _require(user)
    if m.state != S.WINDOW:
        raise ServiceError("Only a switched-over migration can be completed.", code="bad_state")
    if m.rollback_until and timezone.now() < m.rollback_until and not force:
        raise ServiceError(f"The rollback window is open until {m.rollback_until:%Y-%m-%d %H:%M}.", code="window_open")
    for server in {m.target, m.mail_target} - {None}:
        try:
            agent.dispatch(None, server, "MIGRATE_DISCARD", {"archive": m.archive_name}, subject="", idempotency_key=f"mig-{m.pk}-discard-{server.pk}")
        except ServiceError:
            pass  # a node that is gone has nothing to delete; the staged file is also swept by the node's housekeeping
    m.state, m.completed_at = S.COMPLETE, timezone.now()
    m.save(update_fields=["state", "completed_at", "updated_at"])
    event(m, "complete", "Complete. The cPanel account still exists: retire it by hand when you are ready.")
    audit.record("migration.completed", actor=user, target=m.account, metadata={"migration": m.pk, "forced": force})
    return m


# --- the dashboard (M3) -----------------------------------------------------------------------------------------------------------------------

STAGES = ("backup", "transfer", "discovery", "compatibility", "import", "validation", "cutover", "rollback window", "completion")


def stage_marks(m):
    """For the per-account tracker: each stage is 'done', 'now', 'problem' or 'todo'."""
    state = m.state
    done = {S.AWAITING_ARCHIVE: 0, S.ANALYSING: 2, S.MANUAL_REVIEW: 3, S.READY: 4, S.IMPORTING: 4, S.FAILED: 4, S.VALIDATED: 6, S.WINDOW: 7, S.ROLLED_BACK: 7, S.COMPLETE: 9, S.CANCELLED: 0}[state]
    if state == S.MANUAL_REVIEW and m.validation:
        done = 5  # the import ran; the check afterwards found differences
    if state == S.FAILED and not m.step:
        done = 2  # reading the backup itself failed
    marks = ["done" if i < done else "todo" for i in range(len(STAGES))]
    if state in (S.COMPLETE, S.CANCELLED):
        return list(zip(STAGES, marks))
    marks[min(done, len(STAGES) - 1)] = "problem" if state in (S.FAILED, S.MANUAL_REVIEW, S.ROLLED_BACK) else "now"
    return list(zip(STAGES, marks))


def summary():
    """The figures for the top of the dashboard. ``remaining`` are live accounts still on a cPanel server."""
    remaining = HostingAccount.objects.filter(status__in=LIVE_STATUSES, server__kind=ServerKind.WHM_API).count()
    states = {s: Migration.objects.filter(state=s).count() for s in S.values}
    migrated = states[S.WINDOW] + states[S.COMPLETE]
    busy = sum(states[s] for s in (S.AWAITING_ARCHIVE, S.ANALYSING, S.READY, S.IMPORTING, S.VALIDATED))
    attention = states[S.MANUAL_REVIEW]
    failed = states[S.FAILED]
    return {"total": remaining + migrated, "migrated": migrated, "in_progress": busy, "pending": max(remaining - busy - attention - failed, 0), "failed": failed, "manual_review": attention,
            "rolled_back": states[S.ROLLED_BACK], "remaining_on_cpanel": remaining}


def timeline(m):
    """The account's migration as the detail screen shows it: one line per thing that happens, each with a status of done, now, problem, pending or skipped, and a short note."""
    report, state = m.report or {}, m.state
    analysed = bool(report)
    plan = _plan(m) if analysed else []
    rows = []

    def add(label, status, note=""):
        rows.append({"label": label, "status": status, "note": note})

    waiting = state in (S.AWAITING_ARCHIVE, S.ANALYSING)
    add("Backup arrived on the node", "done" if analysed else ("now" if waiting else "pending"), "" if analysed else "Waiting for the cPanel backup to be copied to the node.")
    add("Discovery", "done" if analysed else ("now" if state == S.ANALYSING else "pending"),
        f"{report.get('files', 0)} files, {len(report.get('databases', []))} database(s), {len(report.get('mail', []))} mail domain(s)" if analysed else "")
    blockers = report.get("blockers") or []
    add("Compatibility check", "problem" if blockers else ("done" if analysed else "pending"), "; ".join(blockers) if blockers else f"{len(report.get('warnings', []))} warning(s)" if analysed else "")
    for key, label, needed in (("site", "Website created on the node", True), ("files", "Files transfer", True), ("databases", "Database transfer", bool(report.get("databases"))),
                               ("mail", "Email transfer", bool(report.get("mail")) and m.mail_target is not None)):
        if analysed and not needed:
            add(label, "skipped", "Nothing to move." if key != "mail" else "No mail in the backup, or no mail node chosen.")
        elif key in m.parts_done:
            add(label, "done")
        elif state == S.IMPORTING and m.step in (key, "maildomain" if key == "mail" else key):
            add(label, "now")
        elif state == S.FAILED and m.step in (key, "maildomain" if key == "mail" else key):
            add(label, "problem", m.last_error)
        else:
            add(label, "pending")
    if "validate" in m.parts_done:
        add("Validation", "done", f"{m.validation.get('files_expected', 0)} files checked")
    elif state == S.MANUAL_REVIEW and m.validation:
        add("Validation", "problem", m.last_error)
    elif state == S.IMPORTING and m.step == "validate":
        add("Validation", "now")
    else:
        add("Validation", "pending")
    add("DNS cutover", "done" if m.cutover_at else "pending", m.dns_note if m.cutover_at else "")
    if state == S.ROLLED_BACK:
        add("Rollback window", "problem", "Rolled back to cPanel.")
    elif state == S.COMPLETE:
        add("Rollback window", "done", "Closed.")
    elif state == S.WINDOW:
        add("Rollback window", "now", f"Open until {m.rollback_until:%Y-%m-%d %H:%M}: the account can still be put back on {m.source.name}.")
    else:
        add("Rollback window", "pending")
    add("Complete", "done" if state == S.COMPLETE else "pending")
    if state == S.CANCELLED:
        for row in rows:
            if row["status"] in ("now", "pending"):
                row["status"], row["note"] = "pending", "Cancelled." if row["label"] == "Complete" else row["note"]
    return rows
