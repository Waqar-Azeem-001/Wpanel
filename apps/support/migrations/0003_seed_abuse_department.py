from django.db import migrations


def seed(apps, schema_editor):
    Department = apps.get_model("support", "Department")
    Department.objects.get_or_create(slug="abuse", defaults={
        "name": "Abuse", "description": "Spam, malware, phishing or other abuse reports about a hosted site or domain.",
        "sort_order": 4})


class Migration(migrations.Migration):
    dependencies = [("support", "0002_seed_defaults")]

    operations = [migrations.RunPython(seed, migrations.RunPython.noop)]
