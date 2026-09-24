from django.core.management.base import BaseCommand, CommandError

from apps.core.tasks import ping


class Command(BaseCommand):
    help = "Send a ping task through the broker and wait for a worker to answer."

    def add_arguments(self, parser):
        parser.add_argument("--timeout", type=int, default=30)

    def handle(self, *args, **options):
        try:
            result = ping.delay().get(timeout=options["timeout"])
        except Exception as exc:
            raise CommandError(f"Celery check failed: {type(exc).__name__}: {exc}") from exc
        if result != "pong":
            raise CommandError(f"Unexpected reply from worker: {result!r}")
        self.stdout.write(self.style.SUCCESS("Celery worker answered: pong"))
