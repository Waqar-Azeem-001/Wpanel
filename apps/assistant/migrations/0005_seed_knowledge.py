from django.db import migrations

from apps.assistant.defaults import ENTRIES


def seed(apps, schema_editor):
    Entry = apps.get_model("assistant", "KnowledgeEntry")
    if Entry.objects.exists():
        return
    for position, (title, keywords, answer, action) in enumerate(ENTRIES):
        Entry.objects.create(title=title, keywords=keywords, answer=answer, action=action, sort_order=(position + 1) * 10)


class Migration(migrations.Migration):

    dependencies = [
        ("assistant", "0004_knowledge_mode"),
    ]

    operations = [
        migrations.RunPython(seed, migrations.RunPython.noop),
    ]
