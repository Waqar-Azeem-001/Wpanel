from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("products", "0010_server_infrastructure_provider")]

    operations = [
        migrations.AddField(
            model_name="server",
            name="region",
            field=models.CharField(blank=True, help_text="Where the machine is, for example EU (Germany). Shown on the infrastructure dashboard.", max_length=60),
        ),
    ]
