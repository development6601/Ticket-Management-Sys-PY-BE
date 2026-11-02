from rest_framework.exceptions import PermissionDenied
from rest_framework.permissions import BasePermission

from apps.accounts.permissions.constants import (
    TICKET_CATEGORY_MANAGE,
    TICKET_CATEGORY_VIEW,
    TICKETS_CREATE,
    TICKETS_MANAGE,
    TICKETS_UPDATE,
    TICKETS_VIEW,
)
from apps.accounts.permissions.rbac import (
    user_has_effective_permission,
)
from apps.customers.models import CustomerUser

TICKET_CATEGORY_READ_PERMISSIONS = (
    TICKET_CATEGORY_MANAGE,
    TICKET_CATEGORY_VIEW,
    TICKETS_CREATE,
    TICKETS_VIEW,
    TICKETS_UPDATE,
    TICKETS_MANAGE,
    "ticket.create",
    "ticket.view",
    "ticket.update",
    "ticket.manage",
)


def user_can_manage_ticket_categories(user):
    return user_has_effective_permission(
        user,
        TICKET_CATEGORY_MANAGE,
    )


def user_can_view_ticket_categories(user):
    return any(
        user_has_effective_permission(user, perm)
        for perm in TICKET_CATEGORY_READ_PERMISSIONS
    )


class TicketCategoryPermission(BasePermission):
    VIEW_DENIED_MESSAGE = (
        "You do not have permission to view ticket categories."
    )
    MANAGE_DENIED_MESSAGE = (
        "You do not have permission to manage ticket categories."
    )
    message = MANAGE_DENIED_MESSAGE

    def has_permission(self, request, view):
        user = request.user

        if not user or not user.is_authenticated:
            return False

        if (
            not user.is_active
            or user.account_status != user.AccountStatus.ACTIVE
        ):
            raise PermissionDenied(
                "Your account has been deactivated."
            )

        membership = CustomerUser.objects.select_related(
            "customer"
        ).filter(user=user).first()

        if membership:
            customer = membership.customer

            if (
                customer.deleted_at is not None
                or not customer.is_active
                or customer.account_status
                != customer.AccountStatus.ACTIVE
            ):
                raise PermissionDenied(
                    "Your customer company is not active."
                )

        if getattr(view, request.method.lower(), None) is None:
            return True

        if request.method == "OPTIONS":
            return True

        can_manage = user_can_manage_ticket_categories(user)
        request._can_manage_ticket_categories = can_manage

        if request.method in ("GET", "HEAD"):
            if can_manage or user_can_view_ticket_categories(user):
                return True
            self.message = self.VIEW_DENIED_MESSAGE
            return False

        self.message = self.MANAGE_DENIED_MESSAGE
        return can_manage
