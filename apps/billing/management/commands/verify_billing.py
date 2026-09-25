from django.core.management.base import BaseCommand, CommandError

from apps.billing import integrity


class Command(BaseCommand):
    help = "Re-derive every invoice/quote figure from its records and report any that disagree."

    def handle(self, *args, **options):
        problems = integrity.verify_all()
        if problems:
            for problem in problems:
                self.stderr.write(problem)
            raise CommandError(f"{len(problems)} billing integrity problem(s) found.")
        self.stdout.write("Billing records are consistent.")
