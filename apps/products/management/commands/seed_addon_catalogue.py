"""
Load the add-on catalogue: six store categories and the extras that go under them, as draft add-ons with NO price.

Everything is created hidden (add-ons) and switched off (categories): nothing shows on the store until staff set a price on an add-on,
make it active and switch its category on (Products > Categories, Products > Add-ons). Safe to run again: it only adds what is missing and never changes
a price, a status or a name that someone already edited.
"""
from django.core.management.base import BaseCommand

from apps.core.system import SYSTEM
from apps.products import services as products
from apps.products.models import Addon, AddonKind, CategoryKind, StoreCategory

SECURITY_GROUP = "Security and domains"
SERVICES_GROUP = "Add-ons and services"

# slug, name, blurb, icon, group, sort order, [(add-on name, kind, description)]
CATALOGUE = [
    ("domain-addons", "Domain add-ons", "Protect and extend your domain names.", "bi-globe2", SECURITY_GROUP, 60, [
        ("Domain Protection", AddonKind.DOMAIN, "Registrar lock and renewal reminders so a domain is not lost, stolen or transferred by mistake."),
        ("Extensions bundle", AddonKind.DOMAIN, "Your .com together with the .pk and .com.pk, at a discount, so your brand stays yours."),
    ]),
    ("security-addons", "Security add-ons", "Keep your website safe and recoverable.", "bi-shield-lock", SECURITY_GROUP, 61, [
        ("Malware removal", AddonKind.GENERAL, "We clean a hacked website and protect it again. One-time service."),
        ("Extended backups", AddonKind.GENERAL, "30 to 90 days of backups, kept on separate storage."),
        ("Website firewall and CDN", AddonKind.GENERAL, "Blocks hackers and bots and makes the website faster for visitors."),
        ("Dedicated IP", AddonKind.GENERAL, "A website IP address that is only yours."),
    ]),
    ("performance-addons", "Performance add-ons", "More power when your website grows.", "bi-lightning-charge", SERVICES_GROUP, 70, [
        ("Resource boost", AddonKind.GENERAL, "More CPU, memory and processes for a sale or a busy season."),
        ("Extra storage and website slots", AddonKind.GENERAL, "More space or more websites without changing your plan."),
        ("Speed optimization", AddonKind.GENERAL, "We fix images, caching and plugins so the website loads faster. One-time service."),
    ]),
    ("email-addons", "Email add-ons", "Professional email for your business.", "bi-envelope", SERVICES_GROUP, 71, [
        ("Extra mailboxes and storage", AddonKind.GENERAL, "More mailboxes or more space than your plan includes."),
        ("Google Workspace or Microsoft 365", AddonKind.GENERAL, "Professional email and office tools, billed in rupees."),
        ("Email setup service", AddonKind.GENERAL, "We set up your phone and Outlook and move your old emails."),
    ]),
    ("website-services", "Website services", "We build, move and look after your website.", "bi-tools", SERVICES_GROUP, 72, [
        ("Website care plan (WHE Guard)", AddonKind.GENERAL, "Monitoring, updates, backups, alerts and a monthly report."),
        ("Premium migration", AddonKind.GENERAL, "We move a large or difficult website for you, with a guarantee."),
        ("WordPress setup and theme", AddonKind.GENERAL, "A ready website: you only add your content."),
        ("Online store pack", AddonKind.GENERAL, "WooCommerce with cash on delivery, JazzCash and Easypaisa, and courier links (TCS, Leopards, PostEx)."),
        ("Business Online pack", AddonKind.GENERAL, "Google Business Profile, a WhatsApp Business link and your policy pages."),
        ("SEO setup, logo and content", AddonKind.GENERAL, "The basics a small business needs, in Urdu or English."),
    ]),
    ("support-addons", "Support add-ons", "Faster help and a person who knows your account.", "bi-people", SERVICES_GROUP, 73, [
        ("Priority support", AddonKind.GENERAL, "A first reply within an hour and a WhatsApp line of its own."),
        ("Dedicated account manager", AddonKind.GENERAL, "One named person for agencies and larger clients."),
        ("SMS and WhatsApp credits", AddonKind.GENERAL, "Messages for you to send to your own customers, sold as credits."),
    ]),
]


class Command(BaseCommand):
    help = "Create the add-on categories (switched off) and draft add-ons (hidden, unpriced). Safe to run again."

    def handle(self, *args, **options):
        made_categories = made_addons = 0
        for slug, name, blurb, icon, group, order, items in CATALOGUE:
            category, created = StoreCategory.objects.get_or_create(slug=slug, defaults={
                "name": name, "blurb": blurb, "icon": icon, "group": group, "kind": CategoryKind.ADDONS, "sort_order": order, "is_active": False})
            made_categories += created
            for addon_name, kind, text in items:
                if Addon.objects.filter(name=addon_name).exists():
                    continue
                products.create_addon(SYSTEM, {"name": addon_name, "kind": kind, "description": text, "category": category})
                made_addons += 1
        self.stdout.write(f"Categories added: {made_categories}. Add-ons added: {made_addons} (hidden, no price). "
                          "Set a price, make an add-on active, and switch its category on to sell it.")
