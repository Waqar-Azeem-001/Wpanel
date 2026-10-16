from django.db import migrations

from apps.assistant.defaults import HOWTO


def seed(apps, schema_editor):
    Entry = apps.get_model("assistant", "KnowledgeEntry")
    for action, steps in HOWTO.items():
        Entry.objects.filter(action=action, howto="").update(howto=steps)


class Migration(migrations.Migration):

    dependencies = [
        ("assistant", "0013_knowledge_howto"),
    ]

    operations = [
        migrations.RunPython(seed, migrations.RunPython.noop),
    ]
