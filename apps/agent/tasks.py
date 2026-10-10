from celery import shared_task

from . import monitoring


@shared_task
def monitor_sweep_task():
    """Every minute: settle each native node's state, alert on changes and, if switched on, restart a failed service within strict limits."""
    return monitoring.sweep()


@shared_task
def mail_reputation_task():
    """Hourly: ask the public blacklists about every mail node's addresses."""
    from . import reputation

    return {name: result["ok"] for name, result in reputation.check_all().items()}


@shared_task
def panel_backup_task():
    """Nightly: an encrypted copy of WHPanel's own database in PANEL_BACKUP_DIR (copy it off this server too)."""
    from django.conf import settings

    from apps.core.management.commands import backup_panel

    return str(backup_panel.make_backup(settings.PANEL_BACKUP_DIR))
