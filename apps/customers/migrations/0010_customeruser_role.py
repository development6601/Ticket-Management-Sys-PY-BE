import django.db.models.functions.text
from django.db import migrations, models


def backfill_customer_user_roles(apps, schema_editor):
    CustomerUser = apps.get_model("customers", "CustomerUser")
    CustomerUser.objects.filter(is_primary_contact=True).update(
        role="ADMIN"
    )


class Migration(migrations.Migration):

    dependencies = [
        ("customers", "0009_alter_customer_options_alter_customer_managers"),
    ]

    operations = [
        migrations.AddField(
            model_name="customeruser",
            name="role",
            field=models.CharField(
                choices=[("ADMIN", "Admin"), ("MEMBER", "Member")],
                default="MEMBER",
                max_length=20,
            ),
        ),
        migrations.RunPython(
            backfill_customer_user_roles,
            migrations.RunPython.noop,
        ),
    ]
