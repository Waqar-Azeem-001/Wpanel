from django.db import migrations


def seed(apps, schema_editor):
    Entry = apps.get_model("assistant", "KnowledgeEntry")
    Entry.objects.get_or_create(
        title="Request a callback",
        defaults={"keywords": "call me\ncall me back\ncallback\nrequest a callback\nphone call\nplease call me\nmujhe call karo\ncall kar do\nphone par baat karni hai\nfon karo", "answer": "Of course, our team can phone you.", "action": "callback", "sort_order": 3})


class Migration(migrations.Migration):

    dependencies = [
        ("assistant", "0011_callbacks_whatsapp"),
    ]

    operations = [
        migrations.RunPython(seed, migrations.RunPython.noop),
    ]
