from celery import shared_task


@shared_task
def ping():
    """Round-trip check that a worker is consuming from the broker."""
    return "pong"


@shared_task
def panel_backup_task():
    """Nightly: an encrypted copy of WHPanel's own database in PANEL_BACKUP_DIR (copy it off this server too)."""
    from django.conf import settings

    from apps.core.management.commands import backup_panel

    try:
        target, copied = backup_panel.run_backup()
    except Exception as exc:  # noqa: BLE001 - tell the team, then let the failure show on the automation page
        from django.urls import reverse

        from apps.notifications import services as notifications

        notifications.notify_team("backup.failed", "view_settings", title="The panel's nightly backup failed", body=f"{type(exc).__name__}: {str(exc)[:300]}",
                                  link=reverse("console:automation"))
        raise
    return f"{target} ({copied})"


def _forget_restore_queued():
    from django.core.cache import cache

    cache.delete("panel-restore-queued")  # the page may start another restore now


@shared_task
def restore_panel_task(name, actor_id):
    """Put the panel's database back from one of its own backups (started by a Super Admin on the Storage page). Tells the team how it went; the record is written AFTER the restore, into the restored database."""
    from django.conf import settings
    from django.urls import reverse

    from apps.accounts.models import User
    from apps.audit import services as audit
    from apps.core.management.commands import backup_panel
    from apps.notifications import services as notifications

    actor = User.objects.filter(pk=actor_id).first()
    path = backup_panel.Path(settings.PANEL_BACKUP_DIR) / name
    from apps.core.locks import single_flight

    try:
        with single_flight("panel-restore", wait=0):  # one restore at a time, whatever queued it
            text = backup_panel.restore_backup(path)
    except Exception as exc:  # noqa: BLE001 - the team must hear about it either way
        _forget_restore_queued()
        notifications.notify_team("backup.failed", "view_settings", title="The panel restore failed", body=f"{type(exc).__name__}: {str(exc)[:300]}", link=reverse("console:storage"))
        raise
    _forget_restore_queued()
    actor = User.objects.filter(pk=actor_id).first() or actor  # the user row of the restored database, if the person exists there
    audit.record("storage.restored", actor=actor, metadata={"backup": name, "result": text[:300]})
    notifications.notify_team("backup.restored", "view_settings", title="The panel was restored from a backup", body=text[:400], link=reverse("console:storage"))
    return text


@shared_task(soft_time_limit=40)
def refresh_status_task():
    """Every 50 seconds: look at the portal, hosting servers, email and background jobs and keep the answer for the public status page (see ``apps.core.status``)."""
    from . import status

    return status.refresh()["overall"]
