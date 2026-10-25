"""Download the newest (or a named) file from the off-site bucket, for a restore or a restore drill. Needs a key that may READ. The key on the server may only write, so give the
reading key for this one command and never store it:

    docker compose exec -e PANEL_BACKUP_S3_ACCESS_KEY=... -e PANEL_BACKUP_S3_SECRET_KEY=... web \
        python manage.py offsite_fetch --folder daily --latest --to /app/host_backups/drill
"""
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from apps.core.management.commands import backup_panel


class Command(BaseCommand):
    help = "Download a backup from the off-site bucket."

    def add_arguments(self, parser):
        parser.add_argument("--folder", required=True, choices=("daily", "monthly", "predeploy"))
        parser.add_argument("--latest", action="store_true")
        parser.add_argument("--name", default="")
        parser.add_argument("--to", required=True)

    def handle(self, *args, **options):
        if not backup_panel.offsite_configured():
            raise CommandError("No off-site storage is set (PANEL_BACKUP_S3_*).")
        client, bucket = backup_panel._s3(), settings.PANEL_BACKUP_S3_BUCKET
        prefix = backup_panel.offsite_key(options["folder"], "")
        found = []
        for page in client.get_paginator("list_objects_v2").paginate(Bucket=bucket, Prefix=prefix):
            found += page.get("Contents", [])
        if options["name"]:
            found = [o for o in found if o["Key"] == prefix + options["name"]]
        elif options["latest"]:
            found = sorted(found, key=lambda o: o["LastModified"], reverse=True)[:1]
        else:
            raise CommandError("Say which one: --latest or --name <file>.")
        if not found:
            raise CommandError(f"Nothing found under {prefix}.")
        target = Path(options["to"])
        target.mkdir(parents=True, exist_ok=True)
        key = found[0]["Key"]
        destination = target / key.rsplit("/", 1)[-1]
        client.download_file(bucket, key, str(destination))
        self.stdout.write(self.style.SUCCESS(f"Downloaded {key} ({destination.stat().st_size} bytes) to {destination}"))
