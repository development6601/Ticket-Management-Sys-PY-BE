import uuid
from collections.abc import Mapping

from rest_framework.exceptions import (
    NotFound,
    PermissionDenied,
    ValidationError,
)
from rest_framework.permissions import BasePermission

from apps.accounts.permissions.constants import (
    TICKET_CATEGORY_CREATE,
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
from apps.customers.models import Customer, CustomerUser
from apps.tickets.models import TicketCategory

TICKET_CATEGORY_READ_PERMISSIONS = (
    TICKET_CATEGORY_MANAGE,
    TICKET_CATEGORY_CREATE,
    TICKET_CATEGORY_VIEW,
    TICKETS_CREATE,
    TICKETS_VIEW,
    TICKETS_UPDATE,
    TICKETS_MANAGE,
)


def is_customer_user(user):
    if not user or not user.is_authenticated:
        return False
    return CustomerUser.objects.filter(user=user).exists()


def is_customer_admin(user, customer):
    if not user or not user.is_authenticated or customer is None:
        return False
    return CustomerUser.objects.filter(
        user=user,
        customer=customer,
        role="ADMIN",
    ).exists()


def is_customer_member(user, customer):
    if not user or not user.is_authenticated or customer is None:
        return False
    return CustomerUser.objects.filter(
        user=user,
        customer=customer,
        role="MEMBER",
    ).exists()


def can_view_category(user, customer=None):
    if not is_customer_user(user):
        return any(
            user_has_effective_permission(user, perm)
            for perm in TICKET_CATEGORY_READ_PERMISSIONS
        )

    if customer is None:
        return False

    return (
        is_customer_admin(user, customer)
        or is_customer_member(user, customer)
    )


def can_create_category(user, customer=None):
    if not is_customer_user(user):
        return (
            user_has_effective_permission(user, TICKET_CATEGORY_CREATE)
            or user_has_effective_permission(user, TICKET_CATEGORY_MANAGE)
        )

    if is_customer_admin(user, customer):
        return True

    return (
        is_customer_member(user, customer)
        and (
            user_has_effective_permission(user, TICKET_CATEGORY_CREATE)
            or user_has_effective_permission(user, TICKET_CATEGORY_MANAGE)
        )
    )


def can_manage_category(user, customer=None):
    if not is_customer_user(user):
        return user_has_effective_permission(
            user,
            TICKET_CATEGORY_MANAGE,
        )

    if is_customer_admin(user, customer):
        return True

    return (
        is_customer_member(user, customer)
        and user_has_effective_permission(
            user,
            TICKET_CATEGORY_MANAGE,
        )
    )


user_can_view_ticket_categories = can_view_category
user_can_create_ticket_categories = can_create_category
user_can_manage_ticket_categories = can_manage_category


def _parse_customer_uuid(raw_id):
    try:
        return uuid.UUID(str(raw_id).strip())
    except (ValueError, AttributeError, TypeError):
        raise ValidationError({
            "customer_id": ["Enter a valid customer UUID."]
        })


def resolve_ticket_category_customer(request, view):
    if hasattr(request, "_resolved_ticket_category_customer"):
        return request._resolved_ticket_category_customer

    user = request.user
    membership = (
        CustomerUser.objects
        .select_related("customer")
        .filter(user=user)
        .first()
    )

    category_id = getattr(view, "kwargs", {}).get("id")

    query_customer_id = request.query_params.get("customer_id")
    body_customer_id = None
    if request.method in ("POST", "PATCH", "PUT") and isinstance(
        request.data, Mapping
    ):
        if "customer_id" in request.data:
            body_customer_id = request.data.get("customer_id")

    if membership:
        customer = membership.customer

        if (
            customer.deleted_at is not None
            or not customer.is_active
            or customer.account_status != Customer.AccountStatus.ACTIVE
        ):
            raise PermissionDenied(
                "Your customer company is not active."
            )

        if query_customer_id is not None:
            parsed_id = _parse_customer_uuid(query_customer_id)
            if parsed_id != customer.pk:
                raise PermissionDenied(
                    "You cannot access ticket categories for another company."
                )

        if body_customer_id is not None:
            parsed_id = _parse_customer_uuid(body_customer_id)
            if parsed_id != customer.pk:
                raise PermissionDenied(
                    "You cannot access ticket categories for another company."
                )

        if category_id is not None:
            category_customer_id = (
                TicketCategory.objects
                .filter(pk=category_id)
                .values_list("customer_id", flat=True)
                .first()
            )
            if (
                category_customer_id is not None
                and category_customer_id != customer.pk
            ):
                raise PermissionDenied(
                    "You do not have permission to access another company's ticket category."
                )

        request._resolved_ticket_category_customer = customer
        return customer

    # Internal user
    target_customer_id = None

    if category_id is not None:
        category = (
            TicketCategory.objects
            .select_related("customer")
            .filter(pk=category_id)
            .first()
        )
        if category is None:
            raise NotFound("Ticket category not found.")

        if query_customer_id is not None:
            parsed_id = _parse_customer_uuid(query_customer_id)
            if parsed_id != category.customer_id:
                raise NotFound("Ticket category not found.")

        if body_customer_id is not None:
            parsed_id = _parse_customer_uuid(body_customer_id)
            if parsed_id != category.customer_id:
                raise ValidationError({
                    "customer_id": [
                        "Customer ID does not match the ticket category's company."
                    ]
                })

        customer = category.customer
        if customer.deleted_at is not None:
            raise NotFound("Customer company not found.")

        request._resolved_ticket_category_customer = customer
        return customer

    if request.method in ("GET", "HEAD"):
        if query_customer_id is None or not str(query_customer_id).strip():
            raise ValidationError({
                "customer_id": ["This query parameter is required."]
            })
        target_customer_id = _parse_customer_uuid(query_customer_id)
    elif request.method == "POST":
        if not isinstance(request.data, Mapping):
            raise ValidationError({
                "non_field_errors": [
                    "Invalid data. Expected a JSON object."
                ]
            })
        if body_customer_id is None or (
            isinstance(body_customer_id, str) and not body_customer_id.strip()
        ):
            raise ValidationError({
                "customer_id": ["This field is required."]
            })
        target_customer_id = _parse_customer_uuid(body_customer_id)

    customer = Customer.objects.filter(pk=target_customer_id).first()
    if customer is None:
        raise NotFound("Customer company not found.")

    request._resolved_ticket_category_customer = customer
    return customer


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

        if request.method == "OPTIONS":
            return True

        if getattr(view, request.method.lower(), None) is None:
            target_customer = membership.customer if membership else None
            if request.method in ("GET", "HEAD"):
                if can_view_category(user, target_customer):
                    return True
                self.message = self.VIEW_DENIED_MESSAGE
                return False

            if (
                can_manage_category(user, target_customer)
                or can_create_category(user, target_customer)
            ):
                return True

            self.message = self.MANAGE_DENIED_MESSAGE
            return False

        if membership is None:
            if request.method in ("GET", "HEAD"):
                if not can_view_category(user):
                    self.message = self.VIEW_DENIED_MESSAGE
                    return False
            elif request.method == "POST":
                if not can_create_category(user):
                    self.message = self.MANAGE_DENIED_MESSAGE
                    return False
            elif request.method in ("PATCH", "DELETE"):
                if not can_manage_category(user):
                    self.message = self.MANAGE_DENIED_MESSAGE
                    return False
            else:
                return False

        customer = resolve_ticket_category_customer(request, view)

        can_manage = can_manage_category(user, customer)
        request._can_manage_ticket_categories = can_manage

        if request.method in ("GET", "HEAD"):
            if can_view_category(user, customer):
                return True
            self.message = self.VIEW_DENIED_MESSAGE
            return False

        if request.method == "POST":
            allowed = can_create_category(user, customer)
        elif request.method in ("PATCH", "DELETE"):
            allowed = can_manage
        else:
            return False

        if not allowed:
            self.message = self.MANAGE_DENIED_MESSAGE
            return False

        if (
            customer.deleted_at is not None
            or not customer.is_active
            or customer.account_status != Customer.AccountStatus.ACTIVE
        ):
            raise PermissionDenied(
                "Cannot modify ticket categories for an inactive company."
            )

        return True
