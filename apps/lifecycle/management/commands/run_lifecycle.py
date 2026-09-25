from django.core.management.base import BaseCommand

from apps.lifecycle import services


class Command(BaseCommand):
    help = "Run the daily service lifecycle sweep (cancellations, final notices, suspension, termination)."

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true", help="Report what would happen and change nothing.")

    def handle(self, *args, dry_run=False, **options):
        result = services.run_lifecycle(dry_run=dry_run)
        prefix = "Would have: " if dry_run else ""
        self.stdout.write(f"{prefix}cancelled {result['cancelled']}, final notices {result['notices']}, "
                          f"suspended {result['suspended']}, terminated {result['terminated']}")
        for error in result["errors"]:
            self.stderr.write(f"  {error}")
