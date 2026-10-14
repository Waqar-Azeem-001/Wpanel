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

    return str(backup_panel.make_backup(settings.PANEL_BACKUP_DIR))
