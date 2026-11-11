# perform assignment change only after ticket locked and user authorized.

from django.contrib.auth import get_user_model
from django.db import transaction
from django.utils import timezone
from rest_framework.exceptions import PermissionDenied, ValidationError

from apps.support.models import SupportTeam
from apps.tickets.models import Ticket, TicketActivityLog, TicketAssignment
from apps.tickets.services.authorization import (
    authorize_ticket_assignment,
)


def claim_unassigned_ticket(*, ticket, user):
    """
    Claim a ticket for an already-authorized agent.

    Must be called inside a transaction after locking
    the Ticket row and validationg workflow permissions.

    Updates the in-memory Ticket instance and creates
    assignment history. The caller saves the Ticket.
    """
    if not transaction.get_connection().in_atomic_block:
        raise RuntimeError(
            "Ticket claiming requires an atomic transaction."
        )

    if ticket.assigned_employee_id is not None:
        raise ValidationError({
            "assigned_employee": (
                "This ticket is already assigned."
            )
        })

    if ticket.assigned_team_id is None:
        raise ValidationError({
            "assigned_team": (
                "A support team is required before claiming."
            )
        })

    # Defence in depth: ensure the user remains eligible
    # for the ticket's assigned team.
    from apps.tickets.services.visibility import (
        is_active_team_member,
    )

    if not is_active_team_member(
        user, ticket.assigned_team_id
    ):
        raise ValidationError({
            "assigned_employee": (
                "You must be an active member of the "
                "assigned team to claim this ticket."
            )
        })

    # An unassigned ticket should not have an open
    # employee-assignment history entry.
    if TicketAssignment.objects.filter(
        ticket=ticket,
        unassigned_at__isnull=True,
        employee__isnull=False,
    ).exists():
        raise ValidationError({
            "assigned_employee": (
                "Assignment history is inconsistent "
                "with the current ticket assignment."
            )
        })

    ticket.assigned_employee = user

    TicketAssignment.objects.create(
        ticket=ticket,
        team=ticket.assigned_team,
        employee=user,
        assigned_by=user,
        reason="Claimed ticket from team queue.",
    )

    TicketActivityLog.objects.create(
        ticket=ticket,
        user=user,
        activity_type="TICKET_CLAIMED",
        old_value={
            "assigned_employee_id": None,
        },
        new_value={
            "assigned_employee_id": str(user.pk),
        },
        metadata={
            "action": "claim",
            "assigned_team_id": str(
                ticket.assigned_team_id
            ),
        },
    )

    return ticket


def validate_assignment_reason(reason, *, required=False):
    if reason is None:
        reason = ""
    if not isinstance(reason, str):
        raise ValidationError({
            "reason": "Reason must be text."
        })
    reason = reason.strip()
    if required and not reason:
        raise ValidationError({
            "reason": "A reason is required."
        })
    return reason



def get_active_assignment(ticket):
    """
    Ensure active assignment history matches
    the current ticket assignment.
    """
    records = list(
        TicketAssignment.objects.filter(
            ticket=ticket,
            employee__isnull=False,
            unassigned_at__isnull=True,
        ).order_by("assigned_at", "pk")[:2]
    )

    if len(records) > 1:
        raise ValidationError({
            "assigned_employee": (
                "Multiple active employee assignments found."
            )
        })

    record = records[0] if records else None

    if ticket.assigned_employee_id is None:
        if record is not None:
            raise ValidationError({
                "assigned_employee": (
                    "An unassigned ticket has an active "
                    "employee assignment record."
                )
            })
        return None

    if (
        record is None
        or record.employee_id != ticket.assigned_employee_id
        or record.team_id != ticket.assigned_team_id
    ):
        raise ValidationError({
            "assigned_employee": (
                "Active assignment history does not "
                "match the ticket assignment."
            )
        })

    return record


@transaction.atomic
def change_ticket_assignment(
    *,
    ticket_id=None,
    user,
    action,
    employee_id=None,
    team_id=None,
    reason=None,
    ticket=None,
    target_employee_id=None,
    target_team_id=None,
    **kwargs,
):
    """
    Atomically execute an authorized manual ticket assignment operation.
    """
    if ticket_id is None:
        if ticket is not None:
            ticket_id = getattr(ticket, "pk", ticket)
        elif "ticket" in kwargs:
            ticket_obj = kwargs.pop("ticket")
            ticket_id = getattr(ticket_obj, "pk", ticket_obj)
        else:
            raise ValidationError({"ticket_id": "Ticket ID is required."})
    elif hasattr(ticket_id, "pk"):
        ticket_id = ticket_id.pk

    if employee_id is None:
        if target_employee_id is not None:
            employee_id = target_employee_id
        elif "employee" in kwargs:
            emp = kwargs.pop("employee")
            employee_id = getattr(emp, "pk", emp)
        elif "target_employee" in kwargs:
            emp = kwargs.pop("target_employee")
            employee_id = getattr(emp, "pk", emp)

    if team_id is None:
        if target_team_id is not None:
            team_id = target_team_id
        elif "team" in kwargs:
            tm = kwargs.pop("team")
            team_id = getattr(tm, "pk", tm)
        elif "target_team" in kwargs:
            tm = kwargs.pop("target_team")
            team_id = getattr(tm, "pk", tm)

    if action not in ("assign", "reassign", "unassign", "transfer"):
        raise PermissionDenied("Invalid assignment action.")

    
    if action == "unassign" and employee_id is not None:
        raise ValidationError({
            "assigned_employee": (
                "Unassign does not accept an employee."
            )
        })

    if action != "transfer" and team_id is not None:
        raise ValidationError({
            "assigned_team": (
                "Use transfer to change the support team."
            )
        })

    if action == "transfer" and team_id is None:
        raise ValidationError({
            "assigned_team": (
                "Destination support team is required."
            )
        })

    # 1. Lock ticket
    locked_ticket = (
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

    # 2. Resolve target employee
    target_employee = None
    if employee_id is not None:
        target_employee = get_user_model().objects.filter(
            pk=employee_id
        ).first()
        if target_employee is None:
            raise ValidationError({
                "assigned_employee": "Selected employee does not exist."
            })

    # 3. Authorize action (checks permissions, status, employee validity)
    authorize_ticket_assignment(
        user=user,
        ticket=locked_ticket,
        action=action,
        target_team_id=team_id,
        target_employee=target_employee,
    )

    # 4. Reason validation
    reason = validate_assignment_reason(
        reason,
        required=action in ("reassign", "transfer"),
    )

    # 5. Action-specific state validation
    if action in ("assign", "reassign") and target_employee is None:
        raise ValidationError({
            "assigned_employee": "An employee must be selected."
        })

    if action == "assign" and locked_ticket.assigned_employee_id is not None:
        raise ValidationError({
            "assigned_employee": "This ticket is already assigned."
        })

    if action == "reassign":
        if locked_ticket.assigned_employee_id is None:
            raise ValidationError({
                "assigned_employee": "This ticket is not currently assigned."
            })
        if locked_ticket.assigned_employee_id == target_employee.pk:
            raise ValidationError({
                "assigned_employee": (
                    "The selected employee is already assigned to this ticket."
                )
            })

    if action == "unassign" and locked_ticket.assigned_employee_id is None:
        raise ValidationError({
            "assigned_employee": "This ticket is not currently assigned."
        })

    # 6. Retrieve active assignment record
    active_assignment = get_active_assignment(locked_ticket)

    now = timezone.now()

    old_assigned_employee_id = (
        str(locked_ticket.assigned_employee_id)
        if locked_ticket.assigned_employee_id
        else None
    )
    old_assigned_team_id = (
        str(locked_ticket.assigned_team_id)
        if locked_ticket.assigned_team_id
        else None
    )

    # Close active assignment record if any
    if active_assignment is not None:
        active_assignment.unassigned_at = now
        active_assignment.save(update_fields=["unassigned_at", "updated_at"])

    # Resolve new team
    if action == "transfer":
        new_team = SupportTeam.objects.get(pk=team_id)
    else:
        new_team = locked_ticket.assigned_team

    # Record the new assignment state, including team-only
    # assignments where no employee has been selected.
    if new_team is not None:
        TicketAssignment.objects.create(
            ticket=locked_ticket,
            team=new_team,
            employee=target_employee,
            assigned_by=user,
            reason=reason or "Assignment updated.",
        )

    # Update ticket fields
    if action == "transfer":
        locked_ticket.assigned_team = new_team
        locked_ticket.assigned_employee = target_employee
        locked_ticket.save(update_fields=["assigned_team", "assigned_employee", "updated_at"])
    elif action == "unassign":
        locked_ticket.assigned_employee = None
        locked_ticket.save(update_fields=["assigned_employee", "updated_at"])
    else:
        locked_ticket.assigned_employee = target_employee
        locked_ticket.save(update_fields=["assigned_employee", "updated_at"])

    new_assigned_employee_id = (
        str(locked_ticket.assigned_employee_id)
        if locked_ticket.assigned_employee_id
        else None
    )
    new_assigned_team_id = (
        str(locked_ticket.assigned_team_id)
        if locked_ticket.assigned_team_id
        else None
    )

    TicketActivityLog.objects.create(
        ticket=locked_ticket,
        user=user,
        activity_type=f"TICKET_{action.upper()}",
        old_value={
            "assigned_team_id": old_assigned_team_id,
            "assigned_employee_id": old_assigned_employee_id,
        },
        new_value={
            "assigned_team_id": new_assigned_team_id,
            "assigned_employee_id": new_assigned_employee_id,
        },
        metadata={
            "action": action,
            "reason": reason,
        },
    )

    return locked_ticket


# Aliases
execute_ticket_assignment = change_ticket_assignment


def assign_ticket(*args, **kwargs):
    if "action" in kwargs or (
        len(args) >= 3 and isinstance(args[2], str) and args[2] in ("assign", "reassign", "unassign", "transfer")
    ):
        return change_ticket_assignment(*args, **kwargs)
    kwargs["action"] = "assign"
    return change_ticket_assignment(*args, **kwargs)


def reassign_ticket(*args, **kwargs):
    kwargs["action"] = "reassign"
    return change_ticket_assignment(*args, **kwargs)


def unassign_ticket(*args, **kwargs):
    kwargs["action"] = "unassign"
    return change_ticket_assignment(*args, **kwargs)


def transfer_ticket(*args, **kwargs):
    kwargs["action"] = "transfer"
    return change_ticket_assignment(*args, **kwargs)
