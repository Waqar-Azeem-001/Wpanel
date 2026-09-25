from django.db import migrations

DEPARTMENTS = [
    ("Technical Support", "technical", "Hosting, websites, email and DNS problems."),
    ("Billing", "billing", "Invoices, payments, renewals and refunds."),
    ("Sales", "sales", "Questions before you buy."),
    ("General", "general", "Anything else."),
]
# The knowledgebase sections named in the roadmap.
CATEGORIES = ["Getting Started", "Hosting", "Domains", "cPanel", "DNS", "Email", "WordPress", "Billing"]


def seed(apps, schema_editor):
    Department = apps.get_model("support", "Department")
    KBCategory = apps.get_model("support", "KBCategory")
    for order, (name, slug, description) in enumerate(DEPARTMENTS):
        Department.objects.get_or_create(slug=slug, defaults={"name": name, "description": description,
                                                              "sort_order": order})
    for order, name in enumerate(CATEGORIES):
        slug = name.lower().replace(" ", "-")
        KBCategory.objects.get_or_create(slug=slug, defaults={"name": name, "sort_order": order})


class Migration(migrations.Migration):
    dependencies = [("support", "0001_initial")]

    operations = [migrations.RunPython(seed, migrations.RunPython.noop)]
