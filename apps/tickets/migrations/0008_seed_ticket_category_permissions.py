from django.db import migrations


def seed_category_permissions(apps, schema_editor):
    Permission = apps.get_model("accounts", "Permission")
    db_alias = schema_editor.connection.alias

    category_perms = [
        {
            "name": "Ticket Category View",
            "module": "ticket_category",
            "action": "view",
            "description": "View ticket categories",
        },
        {
            "name": "Ticket Category Create",
            "module": "ticket_category",
            "action": "create",
            "description": "Create ticket categories",
        },
        {
            "name": "Ticket Category Manage",
            "module": "ticket_category",
            "action": "manage",
            "description": "Manage ticket categories (update, deactivate, delete)",
        },
    ]

    for perm_data in category_perms:
        Permission.objects.using(db_alias).get_or_create(
            module__iexact=perm_data["module"],
            action__iexact=perm_data["action"],
            defaults=perm_data,
        )


def noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("tickets", "0007_ticketcategory_customer_position"),
        ("accounts", "0018_userpermission"),
    ]

    operations = [
        migrations.RunPython(seed_category_permissions, noop),
    ]
