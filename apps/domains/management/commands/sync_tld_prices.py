"""
Bring the domain (TLD) prices in step with the price list on the company website.

    python manage.py sync_tld_prices              # apply
    python manage.py sync_tld_prices --dry-run    # only say what would change
    python manage.py sync_tld_prices --force      # also apply prices that moved more than 3 times
"""
from django.core.management.base import BaseCommand, CommandError

from apps.core.exceptions import ServiceError
from apps.domains import price_sync


class Command(BaseCommand):
    help = "Sync TLD prices from the price list page (settings.TLD_PRICE_SOURCE_URL)."

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true")
        parser.add_argument("--force", action="store_true")
        parser.add_argument("--url", default=None)

    def handle(self, *args, **options):
        try:
            report = price_sync.sync(url=options["url"], dry_run=options["dry_run"], force=options["force"])
        except ServiceError as exc:
            raise CommandError(exc.message) from exc
        verb = "Would change" if report["dry_run"] else "Changed"
        self.stdout.write(f"{report['rows']} rows read from {report['source']}.")
        self.stdout.write(f"{verb}: {len(report['created'])} added, {len(report['updated'])} updated, {report['unchanged']} already right.")
        for item in report["held"]:
            self.stdout.write(self.style.WARNING(f"Held back (moved more than 3 times): {item['tld']} {item['old']} -> {item['new']}"))
