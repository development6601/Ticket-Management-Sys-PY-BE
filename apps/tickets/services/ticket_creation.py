from django.db import transaction
from django.db.models import F
from rest_framework.exceptions import NotFound, ValidationError

from apps.customers.models import Customer
from apps.tickets.models import Ticket


def allocate_ticket_number(*, customer_id):
    if not transaction.get_connection().in_atomic_block:
        raise RuntimeError(
            "Ticket number allocation requires an atomic transaction."
        )

    customer = (
        Customer.objects
        .select_for_update()
        .filter(
            pk=customer_id,
            deleted_at__isnull=True,
        )
        .first()
    )
    if customer is None:
        raise NotFound("Customer company not found.")

    if (
        not customer.is_active
        or customer.account_status != Customer.AccountStatus.ACTIVE
    ):
        raise ValidationError({
            "customer_id": (
                "Tickets cannot be created for an inactive "
                "or unverified customer company."
            )
        })

    if not customer.ticket_key:
        raise ValidationError({
            "customer_id": "Customer ticket key is not configured."
        })

    Customer.objects.filter(pk=customer.pk).update(
        ticket_counter=F("ticket_counter") + 1
    )
    customer.refresh_from_db(fields=["ticket_counter"])
    return f"{customer.ticket_key}-{customer.ticket_counter}"


class TicketCreationService:
    @staticmethod
    def create_ticket(
        *,
        customer_id,
        performed_by,
        subject,
        description,
        category,
        initial_status,
        priority=Ticket.Priority.MEDIUM,
        assigned_team=None,
        assigned_employee=None,
        due_at=None,
    ):
        with transaction.atomic():
            ticket_number = allocate_ticket_number(
                customer_id=customer_id,
            )
            customer = Customer.objects.get(pk=customer_id)
            # Validate the customer, category, initial status,
            # optional team and employee, and creator permissions.
            ticket = Ticket.objects.create(
                ticket_number=ticket_number,
                customer=customer,
                created_by=performed_by,
                subject=subject,
                description=description,
                category=category,
                status=initial_status,
                priority=priority,
                assigned_team=assigned_team,
                assigned_employee=assigned_employee,
                due_at=due_at,
            )
            return ticket
