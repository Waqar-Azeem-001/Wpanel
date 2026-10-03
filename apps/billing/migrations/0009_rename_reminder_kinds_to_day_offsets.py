"""
InvoiceReminder.kind switches from named stages (due_soon/overdue_1/overdue_7/overdue_14) to the day offset itself
(-3/1/7/14 as text), now that the schedule is configurable (apps.billing.reminders) - any list of days needs a
naming scheme that works for an arbitrary day, not four fixed names. Renaming the existing rows (rather than
leaving them orphaned) keeps the "a reminder of a given kind is never sent to the same invoice again" guarantee
exactly as it already was for any invoice whose reminder history predates this change.
"""
from django.db import migrations

RENAME = {"due_soon": "-3", "overdue_1": "1", "overdue_7": "7", "overdue_14": "14"}


def rename_forward(apps, schema_editor):
    InvoiceReminder = apps.get_model("billing", "InvoiceReminder")
    for old, new in RENAME.items():
        InvoiceReminder.objects.filter(kind=old).update(kind=new)


def rename_backward(apps, schema_editor):
    InvoiceReminder = apps.get_model("billing", "InvoiceReminder")
    for old, new in RENAME.items():
        InvoiceReminder.objects.filter(kind=new).update(kind=old)


class Migration(migrations.Migration):

    dependencies = [
        ('billing', '0008_billingsettings_reminder_schedule_days'),
    ]

    operations = [
        migrations.RunPython(rename_forward, rename_backward),
    ]
