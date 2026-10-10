"""Ask the public blacklists about every mail node's addresses and save the answer (run it hourly)."""
from django.core.management.base import BaseCommand

from apps.agent import reputation


class Command(BaseCommand):
    help = "Check the blacklist status and queue size of every mail node."

    def handle(self, *args, **options):
        results = reputation.check_all()
        if not results:
            self.stdout.write("No mail nodes.")
        for name, r in results.items():
            state = "OK" if r["ok"] else "PROBLEM"
            self.stdout.write(f"{name}: {state} listed={r['listed'] or 'none'} unchecked={r['unchecked'] or 'none'} queue={r['queue']}")
