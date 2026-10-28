import accounts.models
import django.core.validators
from django.db import migrations, models


def backfill_usernames(apps, schema_editor):
    User = apps.get_model("accounts", "User")
    for user in User.objects.all():
        if not getattr(user, "username", None):
            user.username = user.mobile_number
            user.save(update_fields=["username"])


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0002_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="user",
            name="username",
            field=models.CharField(
                max_length=150,
                null=True,
                validators=[
                    django.core.validators.RegexValidator(
                        message="Enter a valid username. Letters, digits, and @/./+/-/_ only.",
                        regex=r"^[\w.@+-]+$",
                    )
                ],
            ),
        ),
        migrations.RunPython(backfill_usernames, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="user",
            name="username",
            field=models.CharField(
                max_length=150,
                unique=True,
                validators=[
                    django.core.validators.RegexValidator(
                        message="Enter a valid username. Letters, digits, and @/./+/-/_ only.",
                        regex=r"^[\w.@+-]+$",
                    )
                ],
            ),
        ),
        migrations.AlterModelManagers(
            name="user",
            managers=[
                ("objects", accounts.models.UserManager()),
            ],
        ),
    ]
