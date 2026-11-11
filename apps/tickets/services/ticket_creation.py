from django.contrib.auth import get_user_model
from django.db import transaction
from django.db.models import F
from rest_framework.exceptions import (
    NotFound,
    PermissionDenied,
    ValidationError,
)

from apps.accounts.permissions.rbac import (
    user_has_effective_permission,
)
from apps.customers.models import Customer, CustomerUser
from apps.support.models import TeamMember
from apps.tickets.models import (
    Ticket,
    TicketActivityLog,
    TicketAssignment,
    TicketCategory,
    TicketStatus,
)
from apps.tickets.services.routing import (
    get_ticket_support_team,
)

User = get_user_model()


def allocate_ticket_number(*, customer_id):
    """
    Allocate the next ticket number safely.

    Must run inside transaction.atomic().
    """
    if not transaction.get_connection().in_atomic_block:
        raise RuntimeError(
            "Ticket number allocation requires an atomic transaction."
        )

    customer = (
        Customer.all_objects
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

    Customer.all_objects.filter(pk=customer.pk).update(
        ticket_counter=F("ticket_counter") + 1
    )

    customer.refresh_from_db(fields=["ticket_counter"])

    return f"{customer.ticket_key}-{customer.ticket_counter}"


def validate_ticket_creator(*, user, customer):
    """
    Validate whether the authenticated user can create
    a ticket for this company.
    """
    if (
        user is None
        or not getattr(user, "is_authenticated", False)
    ):
        raise PermissionDenied(
            "Authentication is required to create a ticket."
        )

    if (
        not user.is_active
        or user.account_status != user.AccountStatus.ACTIVE
    ):
        raise PermissionDenied(
            "Your account is inactive."
        )

    # System administrators can create tickets for any
    # active customer company.
    if user.is_superuser or user_has_effective_permission(
        user, "tickets.manage"
    ):
        return

    membership = (
        CustomerUser.objects
        .filter(
            user=user,
            customer=customer,
        )
        .first()
    )

    if membership is not None:
        # Customer admins can create tickets.
        if membership.role == CustomerUser.Role.ADMIN:
            return

        # Customer members need the explicit create permission.
        if user_has_effective_permission(
            user, "tickets.create"
        ):
            return

        raise PermissionDenied(
            "You do not have permission to create tickets."
        )

    # Internal users need the create permission.
    if not user_has_effective_permission(
        user, "tickets.create"
    ):
        raise PermissionDenied(
            "You do not have permission to create tickets."
        )


def validate_ticket_assignment(
    *,
    team,
    employee,
):
    """
    Validate an optional initial employee assignment.
    """
    if employee is None:
        return

    if team is None:
        raise ValidationError({
            "assigned_employee": (
                "An employee cannot be assigned without a support team."
            )
        })

    if (
        not employee.is_active
        or employee.account_status != employee.AccountStatus.ACTIVE
    ):
        raise ValidationError({
            "assigned_employee": (
                "The selected employee is inactive."
            )
        })

    is_eligible = TeamMember.objects.filter(
        team=team,
        user=employee,
        is_active=True,
        team__is_active=True,
    ).exists()

    is_manager = (
        team.manager_id == employee.pk
        and team.is_active
    )

    if not is_eligible and not is_manager:
        raise ValidationError({
            "assigned_employee": (
                "The selected employee must be an active "
                "member or manager of the selected team."
            )
        })



class TicketCreationService:

    @staticmethod
    def create_ticket(
        *,
        customer_id,
        performed_by,
        subject,
        category=None,
        description="",
        initial_status=None,
        priority=Ticket.Priority.MEDIUM,
        assigned_team=None,
        assigned_employee=None,
        due_at=None,
    ):
        with transaction.atomic():

            # 1. Lock and validate the customer company.
            customer = (
                Customer.all_objects
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
                or customer.account_status
                != Customer.AccountStatus.ACTIVE
            ):
                raise ValidationError({
                    "customer_id": (
                        "Tickets cannot be created for an inactive "
                        "or unverified company."
                    )
                })

            # 2. Validate creator authorization.
            validate_ticket_creator(
                user=performed_by,
                customer=customer,
            )

            # 3. Validate subject.
            if not isinstance(subject, str) or not subject.strip():
                raise ValidationError({
                    "subject": "Ticket subject is required."
                })

            subject = subject.strip()

            if len(subject) > 255:
                raise ValidationError({
                    "subject": "Subject cannot exceed 255 characters."
                })

            # 4. Validate description.
            if description is None:
                description = ""

            if not isinstance(description, str):
                raise ValidationError({
                    "description": "Description must be text."
                })

            # 5. Resolve and validate category.
            category_id = getattr(category, "pk", category)

            category_queryset = (
                TicketCategory.objects
                .select_for_update()
                .filter(
                    customer=customer,
                    is_active=True,
                )
            )

            if category_id:
                category = category_queryset.filter(
                    pk=category_id
                ).first()
            else:
                category = category_queryset.filter(
                    is_default=True
                ).first()

            if category is None:
                raise ValidationError({
                    "category": (
                        "Select an active category. "
                        "No default category is configured for this company."
                    )
                })

            # 6. Resolve the company's initial status.
            if initial_status is None:
                initial_status = (
                    TicketStatus.objects
                    .filter(
                        customer=customer,
                        is_initial=True,
                        is_active=True,
                    )
                    .first()
                )
            else:
                status_id = getattr(
                    initial_status,
                    "pk",
                    initial_status,
                )

                initial_status = (
                    TicketStatus.objects
                    .filter(
                        pk=status_id,
                        customer=customer,
                        is_initial=True,
                        is_active=True,
                    )
                    .first()
                )

            if initial_status is None:
                raise ValidationError({
                    "status": (
                        "The company does not have an active "
                        "initial ticket status."
                    )
                })

            # 7. Validate priority.
            valid_priorities = {
                value for value, _label in Ticket.Priority.choices
            }

            if priority not in valid_priorities:
                raise ValidationError({
                    "priority": "Select a valid ticket priority."
                })

            # 8. Resolve automatic support team routing.
            # Category-specific team -> company default team.
            routed_team = get_ticket_support_team(
                customer=customer,
                category=category,
            )

            # 9. Validate an explicitly selected support team.
            if assigned_team is not None:

                if (
                    not performed_by.is_superuser
                    and not user_has_effective_permission(
                        performed_by,
                        "tickets.assign_team",
                    )
                ):
                    raise PermissionDenied(
                        "You do not have permission to select "
                        "a support team."
                    )

                team_id = getattr(
                    assigned_team,
                    "pk",
                    assigned_team,
                )

                from apps.support.models import SupportTeam

                assigned_team = (
                    SupportTeam.objects
                    .filter(
                        pk=team_id,
                        is_active=True,
                    )
                    .first()
                )

                if assigned_team is None:
                    raise ValidationError({
                        "assigned_team": (
                            "Select an active support team."
                        )
                    })

                routed_team = assigned_team

            # 10. Resolve and validate optional employee assignment.
            if assigned_employee is not None:

                if (
                    not performed_by.is_superuser
                    and not user_has_effective_permission(
                        performed_by,
                        "tickets.assign_employee",
                    )
                ):
                    raise PermissionDenied(
                        "You do not have permission to assign "
                        "an employee."
                    )

                employee_id = getattr(
                    assigned_employee,
                    "pk",
                    assigned_employee,
                )

                assigned_employee = (
                    User.objects
                    .filter(pk=employee_id)
                    .first()
                )

                if assigned_employee is None:
                    raise ValidationError({
                        "assigned_employee": "Employee not found."
                    })

            validate_ticket_assignment(
                team=routed_team,
                employee=assigned_employee,
            )

            # 11. Allocate ticket number and create ticket.
            ticket_number = allocate_ticket_number(
                customer_id=customer.pk,
            )

            ticket = Ticket.objects.create(
                ticket_number=ticket_number,
                customer=customer,
                created_by=performed_by,
                subject=subject,
                description=description,
                category=category,
                status=initial_status,
                priority=priority,
                assigned_team=routed_team,
                assigned_employee=assigned_employee,
                due_at=due_at,
            )

            # 12. Record initial assignment history.
            # Team-only routing must also be recorded.
            if routed_team is not None:
                TicketAssignment.objects.create(
                    ticket=ticket,
                    team=routed_team,
                    employee=assigned_employee,
                    assigned_by=performed_by,
                    reason="Initial ticket routing.",
                )

            # 13. Record ticket creation activity.
            TicketActivityLog.objects.create(
                ticket=ticket,
                user=performed_by,
                activity_type="TICKET_CREATED",
                old_value=None,
                new_value={
                    "ticket_number": ticket.ticket_number,
                    "customer_id": str(customer.pk),
                    "category_id": str(category.pk),
                    "priority": ticket.priority,
                    "status": initial_status.name,
                    "assigned_team_id": (
                        str(routed_team.pk)
                        if routed_team else None
                    ),
                    "assigned_employee_id": (
                        str(assigned_employee.pk)
                        if assigned_employee else None
                    ),
                },
                metadata={
                    "source": "ticket_creation",
                },
            )

            return ticket
