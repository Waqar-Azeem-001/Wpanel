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
        target = backup_panel.make_backup(settings.PANEL_BACKUP_DIR)
        backup_panel.verify_backup(target)  # a backup that cannot be read back is not a backup
        copied = backup_panel.upload_offsite(target) if backup_panel.offsite_configured() else "no off-site storage set"
    except Exception as exc:  # noqa: BLE001 - tell the team, then let the failure show on the automation page
        from django.urls import reverse

        from apps.notifications import services as notifications

        notifications.notify_team("backup.failed", "view_settings", title="The panel's nightly backup failed", body=f"{type(exc).__name__}: {str(exc)[:300]}",
                                  link=reverse("console:automation"))
        raise
    return f"{target} ({copied})"
