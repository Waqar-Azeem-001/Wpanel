from django.db import migrations


def seed(apps, schema_editor):
    NumberSequence = apps.get_model("billing", "NumberSequence")
    BillingSettings = apps.get_model("billing", "BillingSettings")
    for key in ("invoice", "quote"):
        NumberSequence.objects.get_or_create(key=key)
    BillingSettings.objects.get_or_create(pk=1)


class Migration(migrations.Migration):
    dependencies = [("billing", "0002_phase07_billing")]

    operations = [migrations.RunPython(seed, migrations.RunPython.noop)]
