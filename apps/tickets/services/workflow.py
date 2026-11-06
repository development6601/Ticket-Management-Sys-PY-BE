from django.db import transaction
from django.utils import timezone

from apps.tickets.models import (
    TicketStatusTransition,
    Ticket,
    TicketStatus,
    TicketActivityLog,
)
from apps.tickets.services.assignment import (
    claim_unassigned_ticket,
)
from apps.tickets.services.resolution import (
    apply_resolution,
    apply_closure,
    apply_reopen,
    validate_reopen_reason,
)
from apps.tickets.services.visibility import (
    is_active_team_member,
    is_system_admin,
    is_team_manager,
)
from rest_framework.exceptions import ValidationError


def get_transition_action(from_status, to_status):
    """
    Identify the business action from lifecycle flags.
    This does not authorize or execute the transition.
    """
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


from apps.tickets.services.authorization import (
    authorize_ticket_transition,
)


def validate_requested_transition(
    *,
    user,
    ticket,
    to_status,
):
    """
    Find and authorize a transition.
    Does not change the ticket.
    """
    transition = get_configured_transition(
        ticket=ticket,
        to_status=to_status,
    )
    action = authorize_ticket_transition(
        user=user,
        ticket=ticket,
        transition=transition,
    )
    return transition, action


# Atomic transition service
def get_workflow_snapshot(ticket):
    """
    Capture the important workflow fields.

    JSONField needs serializable values, so UUIDs and
    datetimes are converted to strings.
    """
    def value(v):
        return str(v) if v is not None else None

    return {
        "status_id": value(ticket.status_id),
        "status_name": ticket.status.name,
        "assigned_team_id": value(ticket.assigned_team_id),
        "assigned_employee_id": value(
            ticket.assigned_employee_id
        ),
        "resolution_type": ticket.resolution_type,
        "resolution_notes": ticket.resolution_notes,
        "resolved_at": value(ticket.resolved_at),
        "resolved_by_id": value(ticket.resolved_by_id),
        "closed_at": value(ticket.closed_at),
        "closed_by_id": value(ticket.closed_by_id),
        "resolution_confirmed_at": value(
            ticket.resolution_confirmed_at
        ),
        "resolution_confirmed_by_id": value(
            ticket.resolution_confirmed_by_id
        ),
        "reopen_count": ticket.reopen_count,
    }


# Executor
@transaction.atomic
def execute_ticket_transition(
    *,
    ticket_id,
    to_status_id,
    user,
    resolution_type=None,
    resolution_notes=None,
    reason=None,
):
    """
    Atomically execute an authorized ticket transition.

    The ticket is locked before authorization so that
    concurrent requests cannot act on stale state.
    """

    # Step 1: Lock and load the current ticket.
    ticket = (
        Ticket.objects
        .select_for_update(of=("self",))
        .select_related(
            "status",
            "customer",
            "assigned_team",
            "assigned_employee",
        )
        .get(pk=ticket_id)
    )

    # Step 2: Load the requested destination.
    to_status = TicketStatus.objects.get(
        pk=to_status_id
    )

    # Step 3: Revalidate configuration and authorization
    # against the locked ticket's current state.
    transition, action = validate_requested_transition(
        user=user,
        ticket=ticket,
        to_status=to_status,
    )

    # Step 4: Validate all action-specific inputs before making any changes.
    if action == "resolve":
        from apps.tickets.services.resolution import (
            validate_resolution_details,
        )

        resolution_type, resolution_notes = (
            validate_resolution_details(
                resolution_type=resolution_type,
                resolution_notes=resolution_notes,
            )
        )

    elif action == "reopen":
        reason = validate_reopen_reason(reason)

    # Reject action-specific data on unrelated actions.
    if action != "resolve" and (
        resolution_type is not None
        or resolution_notes is not None
    ): 
        raise ValidationError({
            "resolution": (
                "Resolution details are only accepted "
                "when resolving a ticket."
            )
        })

    if action != "reopen" and reason is not None:
        raise ValidationError({
            "reason": (
                "A reopening reason is only accepted "
                "when reopening a ticket."
            )
        })


    # STEP 5: Capture old values before modifying them.
    old_snapshot = get_workflow_snapshot(ticket)
    old_status_name = ticket.status.name

    now = timezone.now()

    # STEP 6: Claim only when an eligible agent starts
    # or resolves an unassigned ticket.
    # A manager or System Admin can override workflow
    # authorization without becoming the assignee.
    if (
        action in ("start", "resolve")
        and ticket.assigned_employee_id is None
        and not is_system_admin(user)
        and not is_team_manager(
            user, ticket.assigned_team_id
        )
    ):
        if not is_active_team_member(
            user, ticket.assigned_team_id
        ):
            from rest_framework.exceptions import ValidationError

            raise ValidationError({
                "assigned_employee": (
                    "Only an eligible team agent "
                    "may claim this ticket."
                )
            })

        claim_unassigned_ticket(
            ticket=ticket,
            user=user,
        )

    # STEP 7: Update action-specific fields.
    if action == "resolve":
        apply_resolution(
            ticket=ticket,
            user=user,
            now=now,
            resolution_type=resolution_type,
            resolution_notes=resolution_notes,
        )

    elif action == "close":
        apply_closure(
            ticket=ticket,
            user=user,
            now=now,
        )

    elif action == "reopen":
        apply_reopen(ticket=ticket)

    # STEP 8: Change the current status.
    ticket.status = to_status

    # STEP 9: Persist the ticket.
    # Ticket.save() already calls Ticket.clean().
    ticket.save()

    # STEP 10: Capture new values.
    new_snapshot = get_workflow_snapshot(ticket)

    # STEP 11: Record the transition in the same
    # transaction as the ticket and assignment changes.
    TicketActivityLog.objects.create(
        ticket=ticket,
        user=user,
        activity_type=f"TICKET_{action.upper()}",
        old_value=old_snapshot,
        new_value=new_snapshot,
        metadata={
            "action": action,
            "from_status": old_status_name,
            "to_status": to_status.name,
            "transition_id": str(transition.pk),
            "reason": reason,
        },
    )

    return ticket
