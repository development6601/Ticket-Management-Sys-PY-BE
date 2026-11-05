from django.db import migrations


def seed_transition_permissions(apps, schema_editor):
    Permission = apps.get_model("accounts", "Permission")
    db_alias = schema_editor.connection.alias

    transition_perms = [
        {
            "name": "Ticket Start",
            "module": "tickets",
            "action": "start",
            "description": "Start work on a ticket (New to In Progress)",
        },
        {
            "name": "Ticket Resolve",
            "module": "tickets",
            "action": "resolve",
            "description": "Resolve a ticket (New or In Progress to Resolved)",
        },
        {
            "name": "Ticket Confirm",
            "module": "tickets",
            "action": "confirm",
            "description": "Confirm resolution of a ticket (Resolved to Done)",
        },
        {
            "name": "Ticket Reopen",
            "module": "tickets",
            "action": "reopen",
            "description": "Reopen a ticket (Resolved or Done to In Progress)",
        },
    ]

    for perm_data in transition_perms:
        Permission.objects.using(db_alias).get_or_create(
            module__iexact=perm_data["module"],
            action__iexact=perm_data["action"],
            defaults=perm_data,
        )


def noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("tickets", "0010_alter_ticketstatus_customer"),
        ("accounts", "0018_userpermission"),
    ]

    operations = [
        migrations.RunPython(seed_transition_permissions, noop),
    ]
