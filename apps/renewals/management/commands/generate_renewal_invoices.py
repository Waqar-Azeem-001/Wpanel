from django.core.management.base import BaseCommand

from apps.renewals import services


class Command(BaseCommand):
    help = "Create renewal invoices for services expiring within the configured lead time."

    def add_arguments(self, parser):
        parser.add_argument("--days", type=int, default=None, help="Override the lead time (days).")

    def handle(self, *args, **options):
        result = services.generate_renewal_invoices(lead_days=options["days"])
        self.stdout.write(f"Hosting renewal invoices: {result['hosting']}; domain renewal invoices: {result['domains']}.")
        for error in result["errors"]:
            self.stderr.write(error)
