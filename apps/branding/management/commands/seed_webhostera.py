"""
Load the Web Host Era brand, plans and domain prices (transcribed from the public webhostera.pk website) into a fresh install.

    python manage.py seed_webhostera            # brand, plans, domain prices
    python manage.py seed_webhostera --brand    # only the brand (name, colours, logo, favicon, footer)

Everything here is ordinary configuration written through the same services the staff screens use, so it can be
changed afterwards in Setup (Brand, Products, Domain Pricing). It is safe to run again: existing rows are updated, not
duplicated. Prices are the public "from" monthly prices on the site; longer-term prices are a business decision and are
added in Setup.
"""
from decimal import Decimal
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand

from apps.branding import services as branding
from apps.core.system import SYSTEM
from apps.domains import services as domains
from apps.domains.models import RegistrarProvider
from apps.products import services as products
from apps.products.models import BillingCycle, Product, ProductType, Server

SEED = Path(__file__).resolve().parents[2] / "seed"

BRAND = {
    "site_name": "Web Host Era",
    "primary_color": "#2a6af2",
    "accent_color": "#c8fc35",
    "footer_text": "© 2002-2026 Web Host Era Hosting Ltd. All rights reserved.",
}

MB = 1024
# name, type, monthly price, feature lines (shown on the plan card), resource limits (None = unlimited)
PLANS = [
    ("Shared Starter", ProductType.SHARED_HOSTING, "600",
     ["10 GB NVMe storage", "50 GB bandwidth", "4 databases", "8 email accounts", "5 subdomains", "Free domain name",
      "Free SSL certificate", "cPanel and webmail", "1-click app installer"],
     {"disk_mb": 10 * MB, "bandwidth_mb": 50 * MB, "databases": 4, "email_accounts": 8, "subdomains": 5}),
    ("Shared Grow", ProductType.SHARED_HOSTING, "750",
     ["50 GB NVMe storage", "100 GB bandwidth", "10 databases", "20 email accounts", "Free domain name",
      "Free SSL certificate", "cPanel and webmail", "1-click app installer"],
     {"disk_mb": 50 * MB, "bandwidth_mb": 100 * MB, "databases": 10, "email_accounts": 20}),
    ("Shared Digital", ProductType.SHARED_HOSTING, "975",
     ["300 GB NVMe storage", "Unlimited bandwidth", "Unlimited databases", "Unlimited email accounts",
      "Free domain name", "Free SSL certificate", "cPanel and webmail", "1-click app installer"],
     {"disk_mb": 300 * MB, "bandwidth_mb": None, "databases": None, "email_accounts": None}),
    ("WordPress Hosting", ProductType.WORDPRESS_HOSTING, "708",
     ["1 WordPress website", "50 GB SSD storage", "1 GB RAM", "100 GB bandwidth", "1 database", "15 email accounts",
      "Free domain name", "SSL enabled"],
     {"disk_mb": 50 * MB, "bandwidth_mb": 100 * MB, "databases": 1, "email_accounts": 15, "websites": 1}),
    ("Business Hosting", ProductType.SHARED_HOSTING, "750",
     ["100 GB NVMe storage", "Unlimited bandwidth", "40 databases", "Unlimited email accounts", "Free domain name",
      "Free SSL certificate", "cPanel and webmail", "Increased resources"],
     {"disk_mb": 100 * MB, "bandwidth_mb": None, "databases": 40, "email_accounts": None}),
    ("Reseller Basic", ProductType.RESELLER_HOSTING, "1099",
     ["20 GB web space", "50 GB bandwidth", "10 domains", "200 email accounts", "20 databases", "SSL enabled",
      "Virus and spam protection", "24/7 support"],
     {"disk_mb": 20 * MB, "bandwidth_mb": 50 * MB, "addon_domains": 10, "email_accounts": 200, "databases": 20}),
    ("Reseller Improved", ProductType.RESELLER_HOSTING, "2049",
     ["40 GB web space", "100 GB bandwidth", "20 domains", "300 email accounts", "40 databases", "SSL enabled",
      "Virus and spam protection", "24/7 support"],
     {"disk_mb": 40 * MB, "bandwidth_mb": 100 * MB, "addon_domains": 20, "email_accounts": 300, "databases": 40}),
    ("Reseller Maximized", ProductType.RESELLER_HOSTING, "2524",
     ["60 GB web space", "250 GB bandwidth", "30 domains", "500 email accounts", "Unlimited databases", "SSL enabled",
      "Virus and spam protection", "24/7 support"],
     {"disk_mb": 60 * MB, "bandwidth_mb": 250 * MB, "addon_domains": 30, "email_accounts": 500, "databases": None}),
]

# extension, price (register = renew = transfer on the public price list), minimum years
TLDS = [
    (".com", 4849, 1), (".net", 4950, 1), (".org", 4350, 1), (".biz", 6900, 1), (".info", 6399, 1), (".us", 3999, 1),
    (".uk", 5890, 1), (".co.uk", 5890, 1), (".org.uk", 5890, 1), (".tv", 13980, 1), (".com.pk", 4200, 2),
    (".pk", 4200, 2), (".net.pk", 4200, 2), (".org.pk", 4200, 2), (".biz.pk", 4200, 2), (".me.pk", 4200, 2),
    (".in", 3790, 1), (".co.in", 3790, 1), (".net.in", 3790, 1), (".org.in", 3790, 1), (".tel", 4750, 1),
    (".mobi", 5690, 1), (".asia", 5700, 1), (".me", 9900, 1), (".cc", 9800, 1), (".eu", 4599, 1), (".name", 4199, 1),
    (".club", 5999, 1), (".cricket", 5899, 1), (".church", 9850, 1), (".xyz", 3590, 1), (".community", 8599, 1),
    (".af", 23999, 1), (".expert", 16900, 1), (".vip", 5499, 1),
]


class Command(BaseCommand):
    help = "Load the Web Host Era brand, plans and domain prices (see the module docstring)."

    def add_arguments(self, parser):
        parser.add_argument("--brand", action="store_true", help="Only the brand: name, colours, logo, favicon, footer.")

    def handle(self, *args, **options):
        self.brand()
        if not options["brand"]:
            self.plans()
            self.domain_prices()
        if settings.STORE_CURRENCY != "PKR":
            self.stdout.write(self.style.WARNING(
                f"STORE_CURRENCY is {settings.STORE_CURRENCY}: these prices are in rupees. Set STORE_CURRENCY=PKR in .env "
                "(before taking any orders) so invoices and clients use PKR."))

    def brand(self):
        branding.save_settings(SYSTEM, values=BRAND, logo=(SEED / "webhostera-logo.png").read_bytes(),
                               favicon=(SEED / "webhostera-icon.png").read_bytes())
        self.stdout.write("Brand: Web Host Era (colours, logo, favicon, footer).")

    def plans(self):
        server = Server.objects.first() or products.create_server(SYSTEM, {
            "name": "Server 1 (replace me)", "hostname": "server1.invalid",
            "notes": "Placeholder created by seed_webhostera: set the real hostname and WHM details in Setup > Servers."})
        for name, kind, monthly, lines, limits in PLANS:
            product = Product.objects.filter(name=name).first()
            data = {"name": name, "type": kind, "description": "\n".join(lines), "resource_limits": limits,
                    "whm_package_name": name.lower().replace(" ", "_")}
            if product is None:
                product = products.create_product(SYSTEM, data)
            else:
                products.update_product(SYSTEM, product, data)
            products.set_price(SYSTEM, product, billing_cycle=BillingCycle.MONTHLY, price=Decimal(monthly))
            products.set_product_servers(SYSTEM, product, [server.pk])
            if product.status != "active":
                products.set_product_status(SYSTEM, product, "active")
        self.stdout.write(f"Plans: {len(PLANS)} (monthly prices).")

    def domain_prices(self):
        if not RegistrarProvider.objects.filter(is_active=True).exists():
            RegistrarProvider.objects.create(name="Manual registrar", kind="manual", is_active=True)
        for tld, price, min_years in TLDS:
            domains.set_tld_pricing(SYSTEM, tld, register_price=price, renew_price=price, transfer_price=price,
                                    min_years=min_years)
        self.stdout.write(f"Domain prices: {len(TLDS)} extensions.")
