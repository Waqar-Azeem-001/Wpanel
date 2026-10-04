from pathlib import Path

from django.core.management.base import BaseCommand
from django.template.loader import render_to_string


class Command(BaseCommand):
    help = ("Render templates/maintenance.html to deploy/nginx/maintenance.html - a plain static copy Nginx can "
           "serve on its own (deploy/nginx/whpanel.conf), without Django or the database being reachable at all. "
           "Re-run this whenever templates/maintenance.html changes, so the two never drift apart by hand.")

    def add_arguments(self, parser):
        parser.add_argument("--out", default=str(Path(__file__).resolve().parents[4] / "deploy" / "nginx" / "maintenance.html"))

    def handle(self, *args, **options):
        out = Path(options["out"])
        out.write_text(render_to_string("maintenance.html"), encoding="utf-8")
        self.stdout.write(self.style.SUCCESS(f"Wrote {out}"))
