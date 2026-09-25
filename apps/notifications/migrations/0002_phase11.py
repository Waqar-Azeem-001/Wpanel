import uuid

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


def backfill_tokens(apps, schema_editor):
    """Every existing email gets its own unguessable open-tracking token (a unique column needs distinct values)."""
    EmailMessage = apps.get_model("notifications", "EmailMessage")
    for message in EmailMessage.objects.filter(track_token__isnull=True).iterator():
        message.track_token = uuid.uuid4()
        message.save(update_fields=["track_token"])


class Migration(migrations.Migration):
    dependencies = [
        ("notifications", "0001_initial"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(model_name="emailmessage", name="is_sensitive", field=models.BooleanField(default=False)),
        migrations.AddField(model_name="emailmessage", name="sensitive_body",
                            field=models.TextField(blank=True, editable=False)),
        migrations.AddField(model_name="emailmessage", name="opened_at",
                            field=models.DateTimeField(blank=True, null=True)),
        migrations.AddField(model_name="emailmessage", name="open_count",
                            field=models.PositiveIntegerField(default=0)),
        migrations.AddField(model_name="emailmessage", name="track_token",
                            field=models.UUIDField(editable=False, null=True)),
        migrations.RunPython(backfill_tokens, migrations.RunPython.noop),
        migrations.AlterField(model_name="emailmessage", name="track_token",
                              field=models.UUIDField(default=uuid.uuid4, editable=False, unique=True)),
        migrations.CreateModel(
            name="NotificationPreference",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True, db_index=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("category", models.CharField(max_length=20)),
                ("email_enabled", models.BooleanField(default=True)),
                ("in_app_enabled", models.BooleanField(default=True)),
                ("user", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE,
                                           related_name="notification_preferences", to=settings.AUTH_USER_MODEL)),
            ],
            options={},
        ),
        migrations.AddConstraint(
            model_name="notificationpreference",
            constraint=models.UniqueConstraint(fields=("user", "category"), name="unique_preference_per_category"),
        ),
    ]
