from django.core.exceptions import ValidationError
from django.db import transaction

from apps.accounts.models import Permission
from apps.customers.models import Customer
from apps.tickets.models import (
    TicketStatus,
    TicketStatusTransition,
)


# From status, to status, required permission.
DEFAULT_TRANSITIONS = (
    ("New", "In Progress", "tickets.start"),
    ("New", "Resolved", "tickets.resolve"),
    ("In Progress", "Resolved", "tickets.resolve"),
    ("Resolved", "Done", "tickets.close"),
    ("Resolved", "In Progress", "tickets.reopen"),
    ("Done", "In Progress", "tickets.reopen"),
)


@transaction.atomic
def seed_customer_transitions(customer):
    """
    Create missing default transitions for one customer.

    Existing transitions are never overwritten.
    Existing status names and custom workflows are preserved.
    """
    customer_id = getattr(customer, "pk", customer)

    # Serialize configuration changes for this customer.
    Customer.all_objects.select_for_update().get(
        pk=customer_id
    )

    statuses = {
        status.name.casefold(): status
        for status in TicketStatus.objects.filter(
            customer_id=customer_id,
        )
    }

    required_names = {
        name.casefold()
        for source, target, _ in DEFAULT_TRANSITIONS
        for name in (source, target)
    }

    missing = required_names - statuses.keys()

    if missing:
        raise ValidationError(
            "Missing default ticket statuses: "
            + ", ".join(sorted(missing))
        )

    # Verify the expected lifecycle configuration.
    expected_flags = {
        "new": (True, False, False),
        "in progress": (False, False, False),
        "resolved": (False, True, False),
        "done": (False, False, True),
    }

    for name, flags in expected_flags.items():
        status = statuses[name]

        actual = (
            status.is_initial,
            status.is_resolved,
            status.is_closed,
        )

        if actual != flags or not status.is_active:
            raise ValidationError(
                f"Status '{status.name}' has an invalid "
                "or inactive lifecycle configuration."
            )

    for source_name, target_name, permission_code in (
        DEFAULT_TRANSITIONS
    ):
        module, action = permission_code.split(".", 1)

        permission = Permission.objects.filter(
            module__iexact=module,
            action__iexact=action,
        ).first()

        if permission is None:
            raise ValidationError(
                f"Missing permission: {permission_code}"
            )

        source = statuses[source_name.casefold()]
        target = statuses[target_name.casefold()]

        transition, created = (
            TicketStatusTransition.objects.get_or_create(
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
                transition.required_permission_id
                != permission.pk
                or not transition.is_active
            ):
                raise ValidationError(
                    f"Existing transition "
                    f"'{source.name} -> {target.name}' "
                    "differs from the default. "
                    "Review it manually."
                )

    return True
