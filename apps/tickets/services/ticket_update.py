from django.db import transaction

from rest_framework.exceptions import (
    NotFound,
    PermissionDenied,
    ValidationError,
)

from apps.accounts.permissions.constants import TICKETS_UPDATE
from apps.accounts.permissions.rbac import (
    user_has_effective_permission,
)
from apps.tickets.models import (
    Ticket,
    TicketActivityLog,
    TicketCategory,
)
from apps.tickets.services.visibility import (
    get_customer_membership,
    visible_tickets_queryset,
)


class TicketUpdateService:

    @staticmethod
    def update_ticket(
        *,
        ticket_id,
        user,
        validated_data,
    ):
        """
        Update permitted ticket information.

        Responsibilities:
        1. Check ticket visibility.
        2. Check update permission.
        3. Lock the ticket during the update.
        4. Validate category ownership and activity.
        5. Update only changed fields.
        6. Record old and new values in the activity log.

        Status and assignment changes are intentionally excluded.
        """

        with transaction.atomic():

            # STEP 1: Retrieve and lock the ticket.
            # Use the existing visibility service so that users
            # cannot update tickets outside their permitted scope.
            ticket = (
                visible_tickets_queryset(user)
                .select_for_update()
                .filter(pk=ticket_id)
                .first()
            )

            if ticket is None :
                raise NotFound("Ticket not found.")

            # STEP 2: Customer users cannot edit ticket information
            # through this internal ticket update endpoint.
            membership = get_customer_membership(user)

            if membership is not None:
                raise PermissionDenied(
                    "Customer users cannot update ticket information."
                )

            # STEP 3: Check the existing RBAC permission.
            if not user_has_effective_permission(
                user,
                TICKETS_UPDATE,
            ):
                raise PermissionDenied(
                    "You do not have permission to update tickets."
                )

            old_values = {}
            new_values = {}
            update_fields = []

            # STEP 4: Update ordinary ticket fields.
            simple_fields = (
                "subject",
                "description",
                "priority",
                "due_at",
            )

            for field in simple_fields:

                # Ignore fields not supplied in the PATCH request.
                if field not in validated_data:
                    continue

                old_value = getattr(ticket, field)
                new_value = validated_data[field]

                # Avoid unnecessary database writes and activity logs.
                if old_value == new_value:
                    continue

                old_values[field] = (
                    old_value.isoformat()
                    if hasattr(old_value, "isoformat")
                    and old_value is not None
                    else old_value
                )

                new_values[field] = (
                    new_value.isoformat()
                    if hasattr(new_value, "isoformat")
                    and new_value is not None
                    else new_value
                )

                setattr(ticket, field, new_value)
                update_fields.append(field)


            # STEP 5: Validate and update the ticket category.
            if "category_id" in validated_data:

                category = TicketCategory.objects.filter(
                    pk=validated_data["category_id"],
                    customer_id=ticket.customer_id,
                    is_active=True,
                ).first()

                if category is None:
                    raise ValidationError({
                        "category_id": (
                            "Select an active category belonging "
                            "to this ticket's customer."
                        )
                    })

                if ticket.category_id != category.pk:

                    old_values["category_id"] = str(
                        ticket.category_id
                    )

                    new_values["category_id"] = str(
                        category.pk
                    )

                    ticket.category = category
                    update_fields.append("category")

            # STEP 6: If nothing changed, return without creating
            # an unnecessary database update or activity log.
            if not update_fields:
                return ticket

            # STEP 7: Save the ticket and update updated_at.
            # Ticket.save() also executes the model's clean() method.
            ticket.save(
                update_fields=[
                    *update_fields,
                    "updated_at",
                ]
            )

            # STEP 8: Record the update in the same transacion.
            # If the activity log fails, the ticket update rolls back.
            TicketActivityLog.objects.create(
                ticket=ticket,
                user=user,
                activity_type="TICKET_UPDATED",
                old_value=old_values,
                new_value=new_values,
                metadata={
                    "source": "ticket_update",
                    "changed_fields": list(old_values.keys()),
                },
            )

            return ticket



