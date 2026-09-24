from django.core.management.base import BaseCommand

from apps.accounts.roles import sync_roles


class Command(BaseCommand):
    help = "Create/update role groups and portal permissions from apps.accounts.roles."

    def handle(self, *args, **options):
        sync_roles()
        self.stdout.write(self.style.SUCCESS("Roles and permissions synchronised."))
