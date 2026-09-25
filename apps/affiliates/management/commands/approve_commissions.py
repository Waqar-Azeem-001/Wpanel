from django.core.management.base import BaseCommand

from apps.affiliates import services


class Command(BaseCommand):
    help = "Approve pending commissions whose hold period has ended."

    def handle(self, *args, **options):
        result = services.run_approvals()
        self.stdout.write(f"Approved {result['approved']} commission(s).")
        for error in result["errors"]:
            self.stderr.write(f"  {error}")
