from django.db import transaction
from rest_framework.exceptions import ValidationError

from apps.customers.models import Customer
from apps.tickets.models import TicketStatus


DEFAULT_TICKET_STATUSES = (
    {
        "name": "New",
        "description": "The ticket has been created and awaits review.",
        "position": 1,
        "is_initial": True,
        "is_resolved": False,
        "is_closed": False,
    },
    {
        "name": "In Progress",
        "description": "Work is actively underway to resolve the issue.",
        "position": 2,
        "is_initial": False,
        "is_resolved": False,
        "is_closed": False,
    },
    {
        "name": "Resolved",
        "description": "The issue has been resolved and awaits confirmation.",
        "position": 3,
        "is_initial": False,
        "is_resolved": True,
        "is_closed": False,
    },
    {
        "name": "Done",
        "description": "The resolution has been confirmed and the ticket is closed.",
        "position": 4,
        "is_initial": False,
        "is_resolved": False,
        "is_closed": True,
    },
)


@transaction.atomic
def seed_customer_statuses(customer):
    customer_id = getattr(customer, "pk", customer)
    locked_customer = (
        Customer.all_objects
        .select_for_update()
        .get(pk=customer_id)
    )
    existing_statuses = TicketStatus.objects.filter(
        customer=locked_customer
    )
    # A company with existing statuses may already have a
    # customized workflow. Never overwrite that configuration.
    if existing_statuses.exists():
        required_flags = (
            "is_initial",
            "is_resolved",
            "is_closed",
        )
        for flag in required_flags:
            if not existing_statuses.filter(
                **{flag: True},
                is_active=True,
            ).exists():
                raise ValidationError({
                    "statuses": (
                        f"Customer already has statuses but "
                        f"no active {flag} status. "
                        f"Review the existing workflow."
                    )
                })
        return

    for config in DEFAULT_TICKET_STATUSES:
        TicketStatus.objects.create(
            customer=locked_customer,
            name=config["name"],
            description=config["description"],
            position=config["position"],
            is_system=True,
            is_active=True,
            is_initial=config["is_initial"],
            is_resolved=config["is_resolved"],
            is_closed=config["is_closed"],
            created_by=None,
        )
