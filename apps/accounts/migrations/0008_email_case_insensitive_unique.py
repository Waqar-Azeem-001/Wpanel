from django.db import migrations, models
from django.db.models.functions import Lower


def refuse_if_duplicates(apps, schema_editor):
    """Two accounts whose emails differ only by capital letters cannot both stay. Say which, and stop, rather than choose one to lose."""
    User = apps.get_model("accounts", "User")
    seen, clashes = {}, []
    for pk, email in User.objects.order_by("pk").values_list("pk", "email"):
        key = email.lower()
        if key in seen:
            clashes.append(f"{email} (account {pk}) and account {seen[key]}")
        else:
            seen[key] = pk
    if clashes:
        raise RuntimeError("These accounts share an email ignoring capital letters; merge or rename them, then migrate again: " + "; ".join(clashes))


class Migration(migrations.Migration):
    dependencies = [("accounts", "0007_signin_devices")]

    operations = [
        migrations.RunPython(refuse_if_duplicates, migrations.RunPython.noop),
        migrations.AddConstraint(model_name="user", constraint=models.UniqueConstraint(Lower("email"), name="accounts_user_email_ci_unique")),
    ]
