"""Prove the off-site key works: store one tiny note in the bucket under check/ (the bucket's lifecycle rules remove it after a day). Needs only the write permission.

    python manage.py offsite_check
"""
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from apps.core.management.commands import backup_panel


class Command(BaseCommand):
    help = "Write one small check note to the off-site bucket."

    def handle(self, *args, **options):
        if not backup_panel.offsite_configured():
            raise CommandError("No off-site storage is set (PANEL_BACKUP_S3_*).")
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        with tempfile.TemporaryDirectory() as folder:
            note = Path(folder) / f"check-{stamp}.txt"
            note.write_text(f"WHPanel off-site check {stamp}\n")
            key = backup_panel.upload_offsite(note, "check")
        self.stdout.write(self.style.SUCCESS(f"The off-site copy works: {key}"))
