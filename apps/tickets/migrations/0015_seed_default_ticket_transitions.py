from django.db import migrations


DEFAULT_TRANSITIONS = (
    ("New", "In Progress", "start"),
    ("New", "Resolved", "resolve"),
    ("In Progress", "Resolved", "resolve"),
    ("Resolved", "Done", "close"),
    ("Resolved", "In Progress", "reopen"),
    ("Done", "In Progress", "reopen"),
)


def seed_default_ticket_transitions(apps, schema_editor):
    Customer = apps.get_model("customers", "Customer")
    TicketStatus = apps.get_model("tickets", "TicketStatus")
    Transition = apps.get_model(
        "tickets", "TicketStatusTransition"
    )
    Permission = apps.get_model(
        "accounts", "Permission"
    )
    db = schema_editor.connection.alias

    permissions = {
        permission.action.lower(): permission
        for permission in Permission.objects.using(db).filter(
            module__iexact="tickets",
            action__in=(
                "start",
                "resolve",
                "close",
                "reopen",
            ),
        )
    }

    for _, _, action in DEFAULT_TRANSITIONS:
        if action not in permissions:
            raise RuntimeError(
                f"Missing tickets.{action} permission."
            )

    for customer_id in (
        Customer.objects.using(db)
        .values_list("pk", flat=True)
        .iterator()
    ):
        statuses = {
            status.name.casefold(): status
            for status in TicketStatus.objects.using(db)
            .filter(customer_id=customer_id)
        }

        if not statuses:
            # Existing customer without statuses.
            # Do not invent a partial workflow.
            continue

        expected = {
            "new": (True, False, False),
            "in progress": (False, False, False),
            "resolved": (False, True, False),
            "done": (False, False, True),
        }

        for name, flags in expected.items():
            status = statuses.get(name)
            if status is None or not status.is_active:
                raise RuntimeError(
                    f"Customer {customer_id}: missing "
                    f"active status '{name}'."
                )

            actual = (
                status.is_initial,
                status.is_resolved,
                status.is_closed,
            )

            if actual != flags:
                raise RuntimeError(
                    f"Customer {customer_id}: status '{name}' has an "
                    f"invalid lifecycle configuration."
                )

        for source_name, target_name, action in DEFAULT_TRANSITIONS:
            permission = permissions[action]
            source = statuses[source_name.casefold()]
            target = statuses[target_name.casefold()]

            transition, created = (
                Transition.objects.using(db).get_or_create(
                    from_status=source,
                    to_status=target,
                    defaults={
                        "required_permission": permission,
                        "is_active": True,
                    },
                )
            )

            if not created:
                # Never silently overwrite a customer-specific
                # transition or reactivate a disabled transition.
                if (
                    transition.required_permission_id != permission.pk
                    or not transition.is_active
                ):
                    raise RuntimeError(
                        f"Existing transition "
                        f"'{source.name} -> {target.name}' "
                        "differs from the default. "
                        "Review it manually."
                    )


class Migration(migrations.Migration):

    dependencies = [
        ("tickets", "0014_update_ticket_workflow_permissions"),
    ]

    operations = [
        migrations.RunPython(
            seed_default_ticket_transitions,
            reverse_code=migrations.RunPython.noop,
        ),
    ]
