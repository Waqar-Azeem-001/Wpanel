"""
Fetch a WHM server's hosting packages and bring the catalog in line with them: point products at packages that exist, copy each plan's features
(disk, bandwidth, mailboxes, databases...) onto its products and, with --create, add the server's other plans as hidden, unpriced products.
A server does not know prices, so prices are never touched. Safe to run again and again.

    python manage.py sync_server_plans                 # every WHM server, refresh the features of linked products
    python manage.py sync_server_plans --create        # also add the server's other plans as hidden products
    python manage.py sync_server_plans --server 1 --dry-run
"""
from django.core.management.base import BaseCommand, CommandError

from apps.accounts.models import User
from apps.hosting.adapters.base import HostingError
from apps.hosting.adapters.registry import build_adapter
from apps.products import services
from apps.products.models import Server, ServerKind


class Command(BaseCommand):
    help = "Copy a WHM server's plans (and their features) into the catalog."

    def add_arguments(self, parser):
        parser.add_argument("--server", type=int, help="Only this server (id). Default: every WHM server.")
        parser.add_argument("--create", action="store_true", help="Add the server's plans that no product uses yet (hidden, no price).")
        parser.add_argument("--dry-run", action="store_true", help="Only say what would change.")
        parser.add_argument("--as", dest="email", help="Staff e-mail address to record in the audit trail. Default: the first super admin.")

    def handle(self, *args, **options):
        actor = User.objects.filter(email=options["email"]).first() if options["email"] else User.objects.filter(is_superuser=True).order_by("pk").first()
        if actor is None:
            raise CommandError("No staff user found to record this as. Pass --as <email>.")
        servers = Server.objects.filter(kind=ServerKind.WHM_API)
        if options["server"]:
            servers = servers.filter(pk=options["server"])
        for server in servers:
            try:
                result = build_adapter(server).check_connection()
            except HostingError as exc:
                self.stderr.write(self.style.ERROR(f"{server.name}: {exc}"))
                continue
            packages, details = result["packages"], result.get("details") or {}
            report = services.plans_report(server, packages, details)
            stale = [p for link in report["linked"] for p in link["stale"]]
            new = [u["package"] for u in report["unlinked"] if u["type"]] if options["create"] else []
            self.stdout.write(f"{server.name}: {len(packages)} plans on the server, {len(stale)} product(s) differ, {len(new)} to add, "
                              f"{len(report['broken'])} pointing at a missing plan.")
            for product in report["broken"]:
                self.stdout.write(self.style.WARNING(f"  needs a plan chosen: {product.name} -> {product.whm_package_name!r} (use Servers > Sync plans)"))
            if options["dry_run"]:
                continue
            _, created, refreshed = services.apply_server_plans(actor, server, packages, create=new, details=details, refresh=[p.pk for p in stale])
            self.stdout.write(self.style.SUCCESS(f"  features copied onto {len(refreshed)} product(s); {len(created)} product(s) added (hidden, unpriced)."))
