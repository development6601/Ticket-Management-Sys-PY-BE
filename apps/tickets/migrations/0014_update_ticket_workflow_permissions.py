from django.db import migrations


def update_ticket_workflow_permissions(apps, schema_editor):
    Permission = apps.get_model("accounts", "Permission")
    db_alias = schema_editor.connection.alias
    permissions = Permission.objects.using(db_alias)

    # Find the existing permission created by migration 0011.
    confirm_permission = permissions.filter(
        module__iexact="tickets",
        action__iexact="confirm",
    ).first()

    # Check whether the new permission already exists.
    close_permission = permissions.filter(
        module__iexact="tickets",
        action__iexact="close",
    ).first()

    if confirm_permission and close_permission:
        # Do not silently delete either record.
        # Existing RolePermission, UserPermission and
        # TicketStatusTransition rows may reference them.
        raise RuntimeError(
            "Both tickets.confirm and tickets.close exist. "
            "Review existing permission assignments before "
            "running this migration."
        )

    if confirm_permission:
        # Check whether another permission already has the name "Ticket Close"
        # because Permission.name is unique.
        conflicting_name = (
            permissions.filter(name__iexact="Ticket Close")
            .exclude(pk=confirm_permission.pk)
            .first()
        )
        if conflicting_name:
            raise RuntimeError(
                f"Permission with name 'Ticket Close' already exists "
                f"(module={conflicting_name.module}, action={conflicting_name.action})."
            )

        # Update the existing row, preserving its primary key.
        confirm_permission.action = "close"
        confirm_permission.name = "Ticket Close"
        confirm_permission.description = (
            "Close a resolved ticket (Resolved to Done)."
        )
        confirm_permission.save(
            using=db_alias,
            update_fields=[
                "action",
                "name",
                "description",
            ],
        )
    elif close_permission is None:
        # Check if Ticket Close name conflicts with some other module/action
        conflicting_name = permissions.filter(
            name__iexact="Ticket Close"
        ).first()
        if conflicting_name:
            raise RuntimeError(
                f"Permission with name 'Ticket Close' already exists "
                f"(module={conflicting_name.module}, action={conflicting_name.action})."
            )

        # Supports a fresh database or an installation
        # where the old permission was never created.
        permissions.create(
            name="Ticket Close",
            module="tickets",
            action="close",
            description=(
                "Close a resolved ticket "
                "(Resolved to Done)."
            ),
        )

    # Part D — Create tickets.transition
    permissions.get_or_create(
        module__iexact="tickets",
        action__iexact="transition",
        defaults={
            "name": "Ticket Transition",
            "module": "tickets",
            "action": "transition",
            "description": "Perform an ordinary ticket status transition.",
        },
    )


class Migration(migrations.Migration):

    dependencies = [
        ("tickets", "0013_remove_ticketstatus_status_cannot_be_resolved_and_closed_and_more"),
        ("accounts", "0018_userpermission"),
    ]

    operations = [
        migrations.RunPython(
            update_ticket_workflow_permissions,
            reverse_code=migrations.RunPython.noop,
        ),
    ]
