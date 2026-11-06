
from django.db import migrations


def seed_assignment_permissions(apps, schema_editor):
    Permission = apps.get_model("accounts", "Permission")
    db_alias = schema_editor.connection.alias

    assignment_permissions = [
        (
            "Ticket Assign",
            "assign",
            "Assign an unassigned ticket to an eligible employee.",
        ),
        (
            "Ticket Reassign",
            "reassign",
            "Reassign a ticket to another eligible employee.",
        ),
        (
            "Ticket Unassign",
            "unassign",
            "Remove the current ticket assignee.",
        ),
        (
            "Ticket Transfer",
            "transfer",
            "Transfer a ticket to another support team.",
        ),
    ]

    for name, action, description in assignment_permissions:
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
        ("tickets", "0015_seed_default_ticket_transitions"),
    ]

    operations = [
        migrations.RunPython(
            seed_assignment_permissions,
            reverse_code=migrations.RunPython.noop,
        ),
    ]
