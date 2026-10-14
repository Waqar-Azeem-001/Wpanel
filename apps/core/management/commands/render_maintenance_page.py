from pathlib import Path

from django.core.management.base import BaseCommand
from django.template.loader import render_to_string


class Command(BaseCommand):
    help = ("Render templates/maintenance.html to deploy/nginx/maintenance.html - a plain static copy Nginx can "
           "serve on its own (deploy/nginx/whpanel.conf), without Django or the database being reachable at all. "
           "Re-run this whenever templates/maintenance.html changes, so the two never drift apart by hand.")

    def add_arguments(self, parser):
        parser.add_argument("--out", default=str(Path(__file__).resolve().parents[4] / "deploy" / "nginx" / "maintenance.html"))

    @staticmethod
    def with_logo(html):
        """Swap the plain marker for the brand logo (embedded, so the page still needs nothing), and drop the gear badge that stands in until there is one."""
        import base64
        import re

        from apps.branding import services

        def embed(kind):
            found = services.packaged_image(kind)
            if found is None:
                return ""
            data, content_type = found
            return f"data:{content_type};base64," + base64.b64encode(data).decode()

        logo, dark = embed("logo"), embed("logo_dark")
        if not logo:
            return html.replace("<!--BRAND_LOGO-->", "")
        picture = ('<picture>' + (f'<source media="(prefers-color-scheme: dark)" srcset="{dark}">' if dark else "")
                   + f'<img src="{logo}" alt="" style="max-width:min(18rem,80%);max-height:4rem;margin:0 auto 1.5rem;display:block"></picture>')
        html = re.sub(r'<div class="badge" aria-hidden="true">.*?</svg>\s*</div>', "", html, count=1, flags=re.S)
        return html.replace("<!--BRAND_LOGO-->", picture)

    def handle(self, *args, **options):
        out = Path(options["out"])
        out.write_text(self.with_logo(render_to_string("maintenance.html")), encoding="utf-8")
        self.stdout.write(self.style.SUCCESS(f"Wrote {out}"))
