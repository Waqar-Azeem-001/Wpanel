from django.db import migrations

# (slug, name, short name, blurb, icon, group, kind, sort order)
DEFAULTS = [
    ("wordpress", "WordPress hosting", "WordPress", "Tuned for WordPress, ready in minutes.", "bi-wordpress", "Hosting", "plans", 10),
    ("shared", "Shared hosting", "Shared", "Everything a first website needs, at the lowest price.", "bi-hdd-stack", "Hosting", "plans", 20),
    ("business", "Business hosting", "Business", "More power and headroom for a growing business site.", "bi-briefcase", "Hosting", "plans", 30),
    ("ecommerce", "E-commerce hosting", "E-commerce", "Built for online shops: fast pages, safe checkouts.", "bi-bag-check", "Hosting", "plans", 40),
    ("reseller", "Reseller hosting", "Reseller", "Host your own clients under your brand.", "bi-people", "Hosting", "plans", 50),
    ("vps", "VPS servers", "VPS", "Your own virtual server with full control.", "bi-cpu", "Servers", "plans", 60),
    ("dedicated", "Dedicated servers", "Dedicated", "A whole machine, only yours.", "bi-server", "Servers", "plans", 70),
    ("ssl", "SSL certificates", "SSL", "Encrypt your site and show the padlock.", "bi-shield-lock", "Security and domains", "addons", 80),
    ("whois", "WHOIS privacy", "WHOIS privacy", "Keep your personal details out of the public domain record.", "bi-eye-slash", "Security and domains", "addons", 90),
    ("domains", "Domains", "Domains", "Search, register or transfer a domain name.", "bi-globe2", "Security and domains", "domains", 100),
]
BY_TYPE = {"wordpress_hosting": "wordpress", "shared_hosting": "shared", "business_hosting": "business",
           "ecommerce_hosting": "ecommerce", "reseller_hosting": "reseller", "vps": "vps", "dedicated_server": "dedicated"}
BY_KIND = {"ssl": "ssl", "whois_privacy": "whois"}


def seed(apps, schema_editor):
    StoreCategory = apps.get_model("products", "StoreCategory")
    Product = apps.get_model("products", "Product")
    Addon = apps.get_model("products", "Addon")
    made = {}
    for slug, name, short, blurb, icon, group, kind, order in DEFAULTS:
        made[slug], _ = StoreCategory.objects.get_or_create(slug=slug, defaults={
            "name": name, "short_name": short, "blurb": blurb, "icon": icon, "group": group, "kind": kind, "sort_order": order})
    for product_type, slug in BY_TYPE.items():
        Product.objects.filter(type=product_type, category__isnull=True).update(category=made[slug])
    for kind, slug in BY_KIND.items():
        Addon.objects.filter(kind=kind, category__isnull=True).update(category=made[slug])


class Migration(migrations.Migration):
    dependencies = [("products", "0005_store_categories")]
    operations = [migrations.RunPython(seed, migrations.RunPython.noop)]
