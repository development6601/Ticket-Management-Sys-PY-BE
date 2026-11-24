from django.db import migrations


def seed_assignment_permissions(apps, schema_editor):
    Permission = apps.get_model("accounts", "Permission")
    db_alias = schema_editor.connection.alias
    permissions = [
        (
            "Ticket Assign Team",
            "assign_team",
            "Select a support team when creating or transferring a ticket.",
        ),
        (
            "Ticket Assign Employee",
            "assign_employee",
            "Select a support employee when creating or assigning a ticket.",
        ),
    ]

    for name, action, description in permissions:
        Permission.objects.using(db_alias).get_or_create(
            module__iexact="tickets",
            action__iexact=action,
            defaults={
                "name": name,
                "module": "tickets",
                "action": action,
                "description": description,
            },
        )


class Migration(migrations.Migration):
    dependencies = [
        ("tickets", "0019_ticketcategory_is_default_and_more"),
    ]

    operations = [
        migrations.RunPython(
            seed_assignment_permissions,
            migrations.RunPython.noop,
        ),
    ]
