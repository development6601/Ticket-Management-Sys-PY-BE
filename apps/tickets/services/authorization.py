
from rest_framework.exceptions import PermissionDenied

from apps.accounts.permissions.rbac import (
    user_has_effective_permission,
)
from apps.customers.models import Customer
from apps.tickets.services.visibility import (
    can_view_ticket,
    get_customer_membership,
    is_active_team_member,
    is_system_admin,
    is_team_manager,
)
from apps.support.models import SupportTeam


PROTECTED_ACTIONS = {
    "resolve": "tickets.resolve",
    "close": "tickets.close",
    "reopen": "tickets.reopen",
}


def get_permission_code(permission):
    return (
        f"{permission.module}.{permission.action}"
    ).lower()


def get_business_action(source, target):
    """
    Classify the transition using lifecycle flags.
    """
    if target.is_closed:
        return "close"

    if target.is_resolved:
        return "resolve"

    if source.is_resolved or source.is_closed:
        return "reopen"

    if source.is_initial:
        return "start"

    return "transition"


def is_eligible_internal_agent(user, ticket):
    """
    Check whether an internal user can work on the
    ticket through its assigned support team.
    """
    if is_system_admin(user):
        return True

    if not ticket.assigned_team_id:
        return False

    if is_team_manager(user, ticket.assigned_team_id):
        return True

    return is_active_team_member(
        user,
        ticket.assigned_team_id,
    )


def can_work_on_ticket(user, ticket):
    """
    Resolve/start eligibility.

    A regular agent cannot work on another employee's
    ticket without a manager/admin override.
    """
    if not is_eligible_internal_agent(user, ticket):
        return False

    if is_system_admin(user):
        return True

    if is_team_manager(user, ticket.assigned_team_id):
        return True

    # Agent may work on their own assignment.
    if ticket.assigned_employee_id == user.pk:
        return True

    # Agent may claim an unassigned ticket.
    if ticket.assigned_employee_id is None:
        return True

    return False


def can_customer_close(user, ticket):
    membership = get_customer_membership(user)

    if membership is None:
        return False

    if membership.customer_id != ticket.customer_id:
        return False

    # Primary contact is the customer administrator
    # in the current CustomerUser data model.
    if membership.is_primary_contact:
        return True

    # Other customer users must be either the creator
    # or directly authorized, subject to the permission
    # check performed by the caller.
    return (
        ticket.created_by_id == user.pk
        or user_has_effective_permission(
            user, "tickets.close"
        )
    )


def can_customer_reopen(user, ticket):
    membership = get_customer_membership(user)

    if membership is None:
        return False

    if membership.customer_id != ticket.customer_id:
        return False

    if membership.is_primary_contact:
        return True

    return (
        ticket.created_by_id == user.pk
        or user_has_effective_permission(
            user, "tickets.reopen"
        )
    )


def authorize_ticket_transition(
    *,
    user,
    ticket,
    transition,
):
    """
    Authorize one configured transition.

    This does not update ticket status or write logs.
    """

    if not can_view_ticket(user, ticket):
        raise PermissionDenied(
            "You cannot access this ticket."
        )

    if (
        transition.from_status_id != ticket.status_id
        or not transition.is_active
        or transition.from_status.customer_id
        != ticket.customer_id
        or transition.to_status.customer_id
        != ticket.customer_id
        or not transition.from_status.is_active
        or not transition.to_status.is_active
    ):
        raise PermissionDenied(
            "This transition is not available."
        )

    action = get_business_action(
        transition.from_status,
        transition.to_status,
    )

    permission_code = get_permission_code(
        transition.required_permission
    )

    # A configurable permission cannot replace a
    # protected lifecycle permission.
    expected = PROTECTED_ACTIONS.get(action)

    if expected and permission_code != expected:
        raise PermissionDenied(
            f"This action requires {expected}."
        )

    if action == "start":
        # Default start requires tickets.start.
        # A custom ordinary transition may use its
        # own configured permission.
        if (
            transition.from_status.is_initial
            and transition.to_status.name.casefold()
            == "in progress"
            and permission_code != "tickets.start"
        ):
            raise PermissionDenied(
                "Starting work requires tickets.start."
            )

    if not user_has_effective_permission(
        user, permission_code
    ):
        raise PermissionDenied(
            "You do not have the required permission."
        )

    membership = get_customer_membership(user)

    if action in ("start", "resolve"):
        if membership is not None:
            raise PermissionDenied(
                "Customers cannot perform internal "
                "ticket work actions."
            )

        if not can_work_on_ticket(user, ticket):
            raise PermissionDenied(
                "Only the assigned agent, an eligible "
                "claiming agent, team manager or "
                "System Admin may perform this action."
            )

    elif action == "close":
        if is_system_admin(user):
            return action

        if not can_customer_close(user, ticket):
            raise PermissionDenied(
                "Only an authorized customer or "
                "System Admin may close this ticket."
            )

    elif action == "reopen":
        if is_system_admin(user):
            return action

        if membership is not None:
            if not can_customer_reopen(user, ticket):
                raise PermissionDenied(
                    "You cannot reopen this ticket."
                )
        else:
            if not is_eligible_internal_agent(
                user, ticket
            ):
                raise PermissionDenied(
                    "You are not eligible to reopen "
                    "this ticket."
                )

    else:
        # Ordinary configurable transitions.
        # Customers do not perform internal work
        # transitions merely by holding a permission.
        if membership is not None:
            raise PermissionDenied(
                "Customers cannot perform internal "
                "workflow transitions."
            )

        if not can_work_on_ticket(user, ticket):
            raise PermissionDenied(
                "You are not eligible to change "
                "this ticket's working status."
            )

    return action



# Add assignment authorization
ASSIGNMENT_PERMISSIONS = {
    "assign": "tickets.assign",
    "reassign": "tickets.reassign",
    "unassign": "tickets.unassign",
    "transfer": "tickets.transfer",
}

def validate_assignment_status(ticket):
    """
    Manual assignment changes are allowed only
    in New and In Progress.
    """
    status = ticket.status
    if (
        not status.is_active
        or status.is_resolved
        or status.is_closed
        or status.name.strip().casefold()
        not in {"new", "in progress"}
    ):
        raise PermissionDenied(
            "Assignment changes are allowed only "
            "in New or In Progress."
        )


def validate_assignment_employee(*, employee, team_id):
    """
    Validate that the selected employee is eligible
    for the specified support team.
    """
    if employee is None:
        return

    if not employee.is_authenticated:
        raise PermissionDenied(
            "The selected employee must be a valid user."
        )

    if (
        not employee.is_active
        or employee.account_status
        != employee.AccountStatus.ACTIVE
        or not employee.email_verified
    ):
        raise PermissionDenied(
            "The selected employee must have an active, "
            "verified account."
        )

    if get_customer_membership(employee) is not None:
        raise PermissionDenied(
            "Customer users cannot be assigned "
            "as support agents."
        )

    if not (
        is_active_team_member(employee, team_id)
        or is_team_manager(employee, team_id)
    ):
        raise PermissionDenied(
            "The selected employee must be an active "
            "member or manager of the support team."
        )



def authorize_ticket_assignment(
    *,
    user,
    ticket,
    action,
    target_team_id=None,
    target_employee=None,
):
    """
    Authorize assignment operations for internal and
    customer users.

    Customer users require explicit assignment permissions.
    Internal users retain the existing team-based rules.
    """

    if action not in ASSIGNMENT_PERMISSIONS:
        raise PermissionDenied("Invalid assignment action.")

    if not can_view_ticket(user, ticket):
        raise PermissionDenied(
            "You cannot access this ticket."
        )

    validate_assignment_status(ticket)

    membership = get_customer_membership(user)


    # CUSTOMER USER
    if membership is not None:

        # Customer users can only assign tickets belonging
        # to their own company.
        if membership.customer_id != ticket.customer_id:
            raise PermissionDenied(
                "You cannot assign tickets belonging "
                "to another company."
            )

        if action not in ("assign", "transfer"):
            raise PermissionDenied(
                "Customer users cannot perform this "
                "assignment operation."
            )

        # Selecting/changing a team requires a separate
        # permission.
        if action == "transfer":
            if not user_has_effective_permission(
                user, "tickets.assign_team"
            ):
                raise PermissionDenied(
                    "You do not have permission to "
                    "select a support team."
                )

        # Selecting an employee requires a separate
        # permission.
        if target_employee is not None:
            if not user_has_effective_permission(
                user, "tickets.assign_employee"
            ):
                raise PermissionDenied(
                    "You do not have permission to "
                    "select a support employee."
                )

        # Customers cannot assign customer users as agents.
        if target_employee is not None:
            validate_assignment_employee(
                employee=target_employee,
                team_id=(
                    target_team_id
                    if action == "transfer"
                    else ticket.assigned_team_id
                ),
            )

        if action == "transfer":
            if not target_team_id:
                raise PermissionDenied(
                    "A destination support team is required."
                )

            if str(target_team_id) == str(
                ticket.assigned_team_id
            ):
                raise PermissionDenied(
                    "Select a different support team."
                )

            if not SupportTeam.objects.filter(
                pk=target_team_id,
                is_active=True,
            ).exists():
                raise PermissionDenied(
                    "The destination support team must be active."
                )

        return

    # INTERNAL USER
    permission_code = ASSIGNMENT_PERMISSIONS[action]

    if not user_has_effective_permission(
        user, permission_code
    ):
        raise PermissionDenied(
            f"This action requires {permission_code}."
        )

    if not ticket.assigned_team_id:
        raise PermissionDenied(
            "The ticket must have a support team."
        )

    source_team_id = ticket.assigned_team_id

    if action == "transfer":
        if not target_team_id:
            raise PermissionDenied(
                "A destination support team is required."
            )

        if str(target_team_id) == str(source_team_id):
            raise PermissionDenied(
                "Select a different support team."
            )

    if not is_system_admin(user):
        if action == "transfer":
            if not (
                is_team_manager(user, source_team_id)
                and is_team_manager(user, target_team_id)
            ):
                raise PermissionDenied(
                    "You must manage both the source "
                    "and destination support teams."
                )

        elif not (
            is_team_manager(user, source_team_id)
            or is_active_team_member(user, source_team_id)
        ):
            raise PermissionDenied(
                "You cannot manage assignments "
                "for this support team."
            )

    if target_team_id is not None:
        if not SupportTeam.objects.filter(
            pk=target_team_id,
            is_active=True,
        ).exists():
            raise PermissionDenied(
                "The destination support team must be active."
            )

    if target_employee is not None:
        validate_assignment_employee(
            employee=target_employee,
            team_id=(
                target_team_id
                if action == "transfer"
                else source_team_id
            ),
        )
