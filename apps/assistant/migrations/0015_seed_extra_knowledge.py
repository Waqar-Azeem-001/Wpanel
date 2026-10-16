from django.db import migrations

from apps.assistant.defaults import EXTRA_ENTRIES


def seed(apps, schema_editor):
    Entry = apps.get_model("assistant", "KnowledgeEntry")
    for position, (title, keywords, answer, action) in enumerate(EXTRA_ENTRIES):
        Entry.objects.get_or_create(title=title, defaults={"keywords": keywords, "answer": answer, "action": action, "sort_order": 70 + position})


class Migration(migrations.Migration):

    dependencies = [
        ("assistant", "0014_seed_howto"),
    ]

    operations = [
        migrations.RunPython(seed, migrations.RunPython.noop),
    ]
