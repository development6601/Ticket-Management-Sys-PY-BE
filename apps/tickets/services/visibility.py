from django.db.models import Q

from apps.accounts.models import UserRole
from apps.customers.models import CustomerUser
from apps.support.models import TeamMember
from apps.tickets.models import Ticket


def is_system_admin(user):
    if not user or not user.is_authenticated:
        return False

    if user.is_superuser:
        return True

    return UserRole.objects.filter(
        user=user,
        role__code="ADMIN",
    ).exists()


def get_customer_membership(user):
    if not user or not user.is_authenticated:
        return None

    return (
        CustomerUser.objects
        .select_related("customer")
        .filter(user=user)
        .first()
    )


def is_active_team_member(user, team_id):
    if not team_id:
        return False

    return TeamMember.objects.filter(
        user=user,
        team_id=team_id,
        is_active=True,
        team__is_active=True,
    ).exists()


def is_team_manager(user, team_id):
    if not team_id:
        return False

    from apps.support.models import SupportTeam

    return SupportTeam.objects.filter(
        pk=team_id,
        manager=user,
        is_active=True,
    ).exists()


def visible_tickets_queryset(user):
    """
    Return tickets visible to this user.

    This controls visibility, not transition permission.
    """
    queryset = Ticket.objects.all()

    if (
        not user
        or not user.is_authenticated
        or not user.is_active
        or user.account_status
        != user.AccountStatus.ACTIVE
    ):
        return queryset.none()

    membership = get_customer_membership(user)

    if membership is not None:
        customer = membership.customer

        if (
            customer.deleted_at is not None
            or not customer.is_active
            or customer.account_status
            != customer.AccountStatus.ACTIVE
        ):
            return queryset.none()

        company_tickets = queryset.filter(
            customer_id=customer.pk
        )

        if membership.is_primary_contact:
            return company_tickets

        from apps.accounts.permissions.rbac import (
            user_has_direct_permission,
        )

        # Members explicitly granted company-wide workflow
        # authority may access company tickets for those actions.
        if (
            user_has_direct_permission(user, "tickets.close")
            or user_has_direct_permission(user, "tickets.reopen")
        ):
            return company_tickets

        return company_tickets.filter(created_by=user)

    if is_system_admin(user):
        return queryset

    # Internal staff can see their assigned tickets,
    # tickets belonging to teams they manage, and
    # tickets belonging to teams where they are active.
    member_team_ids = TeamMember.objects.filter(
        user=user,
        is_active=True,
        team__is_active=True,
    ).values("team_id")

    from apps.support.models import SupportTeam

    managed_team_ids = SupportTeam.objects.filter(
        manager=user,
        is_active=True,
    ).values("pk")

    return queryset.filter(
        Q(assigned_employee=user)
        | Q(assigned_team_id__in=member_team_ids)
        | Q(assigned_team_id__in=managed_team_ids)
    ).distinct()


def can_view_ticket(user, ticket):
    return visible_tickets_queryset(user).filter(
        pk=ticket.pk
    ).exists()
