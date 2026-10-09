"""Sample native nodes so the Native nodes screen has something to show: two enrolled (one healthy, one degraded) and one waiting for its agent. --remove deletes them."""
from django.core.management.base import BaseCommand

from apps.agent import enrollment, reference_agent
from apps.products.models import Server, ServerKind

NODES = [("Demo native 1", "eu1.native.invalid", "EU (Germany)", "healthy"), ("Demo native 2", "eu2.native.invalid", "EU (Finland)", "degraded"),
         ("Demo native 3 (waiting)", "us1.native.invalid", "US (Virginia)", "pending")]


class Command(BaseCommand):
    help = "Create (or with --remove, delete) sample native nodes. Nothing here talks to a real server."

    def add_arguments(self, parser):
        parser.add_argument("--remove", action="store_true")

    def handle(self, *args, **options):
        if options["remove"]:
            self.stdout.write(self.style.SUCCESS(f"Removed {Server.objects.filter(name__in=[n[0] for n in NODES]).delete()[0]} row(s)."))
            return
        for number, (name, host, region, state) in enumerate(NODES, 1):
            server, made = Server.objects.get_or_create(name=name, defaults={"hostname": host, "region": region, "kind": ServerKind.NATIVE})
            if not made:
                continue
            node, token = enrollment.invite(None, server)
            if state == "pending":
                self.stdout.write(f"{name}: waiting. Enrolment token (shown once): {token}")
                continue
            enrollment.enroll(token, reference_agent.make_csr()[1], {**reference_agent.INVENTORY, "hostname": host, "ip_addresses": [f"198.51.100.{number}"]}, "0.1.0")
            node.refresh_from_db()
            node.capacity_sites = 200
            node.save(update_fields=["capacity_sites"])
            enrollment.heartbeat(node, {"cpu_percent": 14 * number, "memory_used_mb": 6000 * number, "disk_used_gb": 120 * number, "sites": 37 * number, "load_1m": 0.6,
                                        "services": {"web": "up", "php": "up", "database": "up" if state == "healthy" else "degraded"}})
        self.stdout.write(self.style.SUCCESS("Sample native nodes ready: Staff - System - Providers - Native nodes."))
