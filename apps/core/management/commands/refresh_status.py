"""Fill the public status page's answer now (the background job does it every 50 seconds; the deploy script calls this so the first visitor sees real figures)."""
from django.core.management.base import BaseCommand

from apps.core import status


class Command(BaseCommand):
    help = "Look at the portal, hosting servers, email and jobs and keep the answer for /status/."

    def handle(self, *args, **options):
        result = status.refresh()
        self.stdout.write(self.style.SUCCESS("Status refreshed: " + ", ".join(f"{n}: {state}" for n, state, _ in result["rows"])))
