"""Copy one file to the off-site bucket (the deploy script uses this for the encrypted pre-deploy database dump).

    python manage.py offsite_upload --file /app/host_backups/predeploy-2026....dump.gpg --folder predeploy
"""
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from apps.core.management.commands import backup_panel

FOLDERS = ("daily", "monthly", "predeploy")


class Command(BaseCommand):
    help = "Copy one (already encrypted) file to the off-site bucket."

    def add_arguments(self, parser):
        parser.add_argument("--file", required=True)
        parser.add_argument("--folder", required=True, choices=FOLDERS)

    def handle(self, *args, **options):
        if not backup_panel.offsite_configured():
            raise CommandError("No off-site storage is set (PANEL_BACKUP_S3_*).")
        path = Path(options["file"])
        if not path.is_file():
            raise CommandError(f"{path} is not a file.")
        if options["folder"] == "predeploy" and path.suffix != ".gpg":
            raise CommandError("A pre-deploy dump is only sent off the server once it is encrypted (a .gpg file).")
        key = backup_panel.upload_offsite(path, options["folder"])
        self.stdout.write(self.style.SUCCESS(f"Stored off-site: {key}"))
