"""
Old and new values for the audit trail (roadmap section 05: "Every sensitive action records Actor, Client, Hosting Service, Action, Old
Value, New Value, IP, Timestamp, Result").

For a handful of settings-style operations the gateway reads the current value just before the change and again just after, and records
only the fields that differ. Each reader returns plain, non-secret fields only: never a password, a file's content, a mail message, or the
arguments of a scheduled job. Operations not listed here (passwords, file contents, anything carrying a secret) record no values, on purpose.
"""
from .adapters.base import HostingError
from .adapters.capabilities import CapabilityNotSupported

CUT = 200


def _php(adapter, user, args):
    s = adapter.get_php_settings(user, args[0])
    return {"version": s.version, "memory_limit": s.memory_limit, "upload_max_filesize": s.upload_max_filesize,
            "max_execution_time": s.max_execution_time, "display_errors": s.display_errors}


def _ssl(adapter, user, args):
    s = adapter.get_ssl_status(user, args[0])
    return {"auto_renew": s.auto_renew, "https_redirect": s.https_redirect, "status": s.status}


def _backup(adapter, user, args):
    o = adapter.backup_options(user)
    return {"schedule": o.schedule, "offsite": o.offsite}


def _ssh(adapter, user, args):
    return {"enabled": adapter.ssh_status(user).enabled}


def _cron(adapter, user, args):
    for job in adapter.list_cron_jobs(user):
        if job.job_id == args[0]:
            words = job.command.split(" ")
            return {"schedule": job.schedule, "script": words[1] if len(words) > 1 else "", "enabled": job.enabled}
    return {}


def _dns(adapter, user, args):
    record_id = getattr(args[1], "record_id", args[1])
    for r in adapter.list_dns_records(user, args[0]):
        if r.record_id == record_id:
            return {"type": r.type, "name": r.name, "value": str(r.value)[:CUT], "ttl": r.ttl, "priority": r.priority}
    return {}


def _redirect(adapter, user, args):
    for d in adapter.list_domains(user):
        if d.name == str(args[0]).strip().lower().rstrip("."):
            return {"redirect_to": d.redirect_to, "permanent": d.redirect_permanent}
    return {}


def _quota(adapter, user, args):
    for m in adapter.list_mailboxes(user):
        if m.address == str(args[0]).strip().lower():
            return {"quota_mb": m.quota_mb}
    return {}


READERS = {"set_php_settings": _php, "set_ssl_auto_renew": _ssl, "set_https_redirect": _ssl, "set_backup_schedule": _backup,
           "set_backup_offsite": _backup, "set_ssh_enabled": _ssh, "update_cron_job": _cron, "update_dns_record": _dns, "delete_dns_record": _dns,
           "set_redirect": _redirect, "set_mailbox_quota": _quota}


def read(adapter, user, operation, args):
    """The current non-secret values around ``operation``, or None when this operation records none or the provider cannot say."""
    reader = READERS.get(operation)
    if reader is None:
        return None
    try:
        return reader(adapter, user, args)
    except (HostingError, CapabilityNotSupported, IndexError, AttributeError, TypeError):
        return None


def difference(before, after):
    """(old, new) holding only the fields that changed; a record that was removed or newly appeared shows as all its fields."""
    if before is None or after is None:
        return {}, {}
    keys = sorted(set(before) | set(after))
    changed = [k for k in keys if before.get(k) != after.get(k)]
    return {k: before.get(k) for k in changed if k in before}, {k: after.get(k) for k in changed if k in after}
