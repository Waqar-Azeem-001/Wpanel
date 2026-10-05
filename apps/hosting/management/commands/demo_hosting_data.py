"""
Put sample hosting accounts in the local database so the Websites dashboard (and later hosting screens) can be seen
with every state: normal, nearly full, full, unlimited bandwidth, no SSL, suspended and still being set up.

    python manage.py demo_hosting_data            # add the sample accounts for the first client that has a user
    python manage.py demo_hosting_data --client 5 # ...for a chosen client id
    python manage.py demo_hosting_data --remove   # take them away again

Development only. The accounts live on a "Demo (sample data)" server of kind ``demo`` that makes no network calls and
reports sample figures. Real accounts are never touched; demo accounts are recognised by that server.
"""
from datetime import timedelta

import shutil

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from apps.clients.models import Client
from apps.hosting.adapters.demo import files_root
from apps.hosting.models import HostingAccount, HostingStatus
from apps.products.models import Product, ProductType, Server, ServerKind

DEMO_SERVER_NAME = "Demo (sample data)"

# domain, username, status, disk used/limit MB, bandwidth used/limit MB
SAMPLES = [
    ("demo-shop.example.com", "demoshop", HostingStatus.ACTIVE, (4915, 20480), (38000, 102400)),
    ("demo-blog.example.com", "demoblog", HostingStatus.ACTIVE, (17500, 20480), (91000, 102400)),
    ("demo-agency.example.com", "demoagency", HostingStatus.ACTIVE, (20100, 20480), (12000, None)),
    ("demo-club.example.com", "democlub", HostingStatus.SUSPENDED, (800, 20480), (1500, 102400)),
    ("demo-new.example.com", "demonew", HostingStatus.PENDING, (None, None), (None, None)),
]


class Command(BaseCommand):
    help = "Add (or --remove) sample hosting accounts for design and testing. Development only."

    def add_arguments(self, parser):
        parser.add_argument("--client", type=int, help="Client id to give the sample accounts to.")
        parser.add_argument("--remove", action="store_true", help="Delete the sample accounts and server.")

    def handle(self, *args, **options):
        server = Server.objects.filter(name=DEMO_SERVER_NAME).first()
        if options["remove"]:
            if server:
                for username in HostingAccount.objects.filter(server=server).values_list("username", flat=True):
                    shutil.rmtree(files_root() / username, ignore_errors=True)  # the sample website files too
            removed = HostingAccount.objects.filter(server=server).delete()[0] if server else 0
            if server:
                server.delete()
            self.stdout.write(self.style.SUCCESS(f"Removed {removed} sample account(s) and the demo server."))
            return

        clients = Client.objects.filter(contacts__user__isnull=False).distinct()
        client = clients.filter(pk=options["client"]).first() if options["client"] else clients.order_by("pk").first()
        if client is None:
            raise CommandError("No client with a sign-in user was found. Create one first, or pass --client.")
        product = (Product.objects.filter(type__in=[ProductType.SHARED_HOSTING, ProductType.WORDPRESS_HOSTING,
                                                    ProductType.BUSINESS_HOSTING]).order_by("pk").first()
                   or Product.objects.order_by("pk").first())
        if product is None:
            raise CommandError("No product exists yet. Create a hosting plan first.")
        server, _ = Server.objects.get_or_create(
            name=DEMO_SERVER_NAME, defaults={"hostname": "demo.invalid", "kind": ServerKind.DEMO})
        created = 0
        for domain, username, status, disk, bandwidth in SAMPLES:
            _, was_created = HostingAccount.objects.get_or_create(
                server=server, domain=domain,
                defaults={"client": client, "product": product, "username": username, "status": status,
                          "package_name": "demo", "disk_used_mb": disk[0], "disk_limit_mb": disk[1],
                          "bandwidth_used_mb": bandwidth[0], "bandwidth_limit_mb": bandwidth[1],
                          "expires_at": timezone.now() + timedelta(days=200),
                          "suspend_reason": "Sample suspension" if status == HostingStatus.SUSPENDED else ""})
            created += was_created
        user = client.contacts.filter(user__isnull=False).first().user
        self.stdout.write(self.style.SUCCESS(
            f"{created} sample account(s) added for client #{client.pk}. Sign in as {user.email} and open "
            f"My Services. Remove them with: python manage.py demo_hosting_data --remove"))
