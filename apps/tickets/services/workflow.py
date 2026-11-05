from rest_framework.exceptions import ValidationError

from apps.tickets.models import TicketStatusTransition

def get_transition_action(from_status, to_status):
    """
    Determine the business action from status lifecycle flags.

    Ordinary transitions return 'transition'.
    """
    if to_status.is_cancelled:
        return "cancel"

    if to_status.is_closed:
        return "close"

    if to_status.is_resolved:
        return "resolve"

    if from_status.is_resolved or from_status.is_closed:
        return "reopen"

    if from_status.is_initial:
        return "start"

    return "transition"

def get_configured_transition(*, ticket, to_status):
    if ticket.status.customer_id != ticket.customer_id:
        raise ValidationError({
            "status": "Ticket status does not belong to its customer."
        })

    if to_status.customer_id != ticket.customer_id:
        raise ValidationError({
            "status": "Destination status belongs to another customer."
        })

    if not ticket.status.is_active or not to_status.is_active:
        raise ValidationError({
            "status": "Inactive statuses cannot be used."
        })

    transition = (
        TicketStatusTransition.objects
        .select_related("required_permission")
        .filter(
            from_status_id=ticket.status_id,
            to_status_id=to_status.id,
            is_active=True,
        )
        .first()
    )

    if transition is None:
        raise ValidationError({
            "status": "This status transition is not allowed."
        })

    return transition
