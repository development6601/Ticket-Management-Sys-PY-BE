from django.db import IntegrityError, transaction
from django.db.models import Max
from django.db.models.deletion import ProtectedError
from rest_framework import status
from rest_framework.exceptions import (
    APIException,
    NotFound,
    PermissionDenied,
    ValidationError,
)

from apps.audit.models import AuditLog
from apps.customers.models import Customer
from apps.tickets.models import TicketCategory
from apps.tickets.permissions import (
    can_create_category,
    can_manage_category,
)

DEFAULT_TICKET_CATEGORIES = [
    "Bug",
    "Feature Request",
    "Technical Issue",
    "Billing",
    "Account",
    "General Question",
]


def seed_customer_categories(customer):
    for position, name in enumerate(
        DEFAULT_TICKET_CATEGORIES,
        start=1,
    ):
        TicketCategory.objects.get_or_create(
            customer=customer,
            name__iexact=name,
            defaults={
                "name": name,
                "description": "",
                "is_active": True,
                "position": position,
                "is_default": name == "General Question",
            },
        )


class CategoryInUse(APIException):
    status_code = status.HTTP_409_CONFLICT
    default_detail = (
        "This category is already used by tickets. "
        "Deactivate it instead."
    )
    default_code = "category_in_use"


def is_duplicate_category_error(exc):
    cause = exc.__cause__
    diagnostic = getattr(cause, "diag", None)
    return (
        getattr(diagnostic, "constraint_name", None)
        == "unique_customer_ticket_category"
    )


class TicketCategoryService:

    @staticmethod
    def _ensure_actor_authorized(performed_by, customer, *, action="manage"):
        if (
            performed_by is None
            or not getattr(performed_by, "is_authenticated", False)
        ):
            raise PermissionDenied(
                "Authentication is required to manage ticket categories."
            )

        if (
            not performed_by.is_active
            or performed_by.account_status
            != performed_by.AccountStatus.ACTIVE
        ):
            raise PermissionDenied(
                "Your account has been deactivated."
            )

        if action == "create":
            allowed = can_create_category(performed_by, customer)
        else:
            allowed = can_manage_category(performed_by, customer)

        if not allowed:
            raise PermissionDenied(
                "You do not have permission to manage ticket categories."
            )

    @staticmethod
    def _lock_active_customer(customer):
        customer_id = getattr(customer, "pk", customer)
        locked_customer = (
            Customer.all_objects
            .select_for_update()
            .filter(pk=customer_id)
            .first()
        )
        if locked_customer is None or locked_customer.deleted_at is not None:
            raise NotFound("Customer company not found.")

        if (
            not locked_customer.is_active
            or locked_customer.account_status
            != Customer.AccountStatus.ACTIVE
        ):
            raise PermissionDenied(
                "Cannot modify ticket categories for an inactive company."
            )

        return locked_customer

    @staticmethod
    @transaction.atomic
    def create(
        *,
        customer,
        validated_data,
        performed_by,
        ip_address=None,
        user_agent=None,
    ):
        locked_customer = TicketCategoryService._lock_active_customer(
            customer
        )
        TicketCategoryService._ensure_actor_authorized(
            performed_by,
            locked_customer,
            action="create",
        )

        data = dict(validated_data)
        if "position" not in data:
            max_position = (
                TicketCategory.objects
                .filter(customer=locked_customer)
                .aggregate(max_pos=Max("position"))["max_pos"]
            )
            data["position"] = (max_position or 0) + 1

        try:
            # Savepoint allows us to handle a database uniqueness
            # error without breaking the outer transaction.
            with transaction.atomic():
                category = TicketCategory.objects.create(
                    customer=locked_customer,
                    **data,
                )
        except IntegrityError as exc:
            if not is_duplicate_category_error(exc):
                raise

            raise ValidationError({
                "name": [
                    "A category with this name already exists for this company."
                ]
            }) from exc

        AuditLog.objects.create(
            user=performed_by,
            action="ticket_category.created",
            entity_type="TicketCategory",
            entity_id=category.pk,
            new_values={
                "customer_id": str(category.customer_id),
                "name": category.name,
                "description": category.description,
                "is_active": category.is_active,
            },
            ip_address=ip_address,
            user_agent=user_agent,
        )

        return category

    @staticmethod
    @transaction.atomic
    def update(
        *,
        customer=None,
        category,
        validated_data,
        performed_by,
        ip_address=None,
        user_agent=None,
    ):
        if customer is None:
            customer = category.customer

        locked_customer = TicketCategoryService._lock_active_customer(
            customer
        )
        TicketCategoryService._ensure_actor_authorized(
            performed_by,
            locked_customer,
            action="manage",
        )

        try:
            category = TicketCategory.objects.select_for_update().get(
                pk=category.pk,
                customer=locked_customer,
            )
        except TicketCategory.DoesNotExist:
            raise NotFound("Ticket category not found.")

        old_values = {
            "customer_id": str(category.customer_id),
            "name": category.name,
            "description": category.description,
            "is_active": category.is_active,
        }

        if (
            category.is_active
            and "is_active" in validated_data
            and validated_data["is_active"] is False
        ):
            other_active_exists = (
                TicketCategory.objects
                .filter(
                    customer=locked_customer,
                    is_active=True,
                )
                .exclude(pk=category.pk)
                .exists()
            )
            if not other_active_exists:
                raise ValidationError({
                    "is_active": [
                        "At least one active category must remain for this company."
                    ]
                })

        for field, value in validated_data.items():
            setattr(category, field, value)

        new_values = {
            "customer_id": str(category.customer_id),
            "name": category.name,
            "description": category.description,
            "is_active": category.is_active,
        }

        if old_values == new_values:
            return category

        try:
            with transaction.atomic():
                category.save()
        except IntegrityError as exc:
            if not is_duplicate_category_error(exc):
                raise

            raise ValidationError({
                "name": [
                    "A category with this name already exists for this company."
                ]
            }) from exc

        AuditLog.objects.create(
            user=performed_by,
            action="ticket_category.updated",
            entity_type="TicketCategory",
            entity_id=category.pk,
            old_values=old_values,
            new_values=new_values,
            ip_address=ip_address,
            user_agent=user_agent,
        )

        return category

    @staticmethod
    @transaction.atomic
    def delete(
        *,
        customer=None,
        category,
        performed_by,
        ip_address=None,
        user_agent=None,
    ):
        if customer is None:
            customer = category.customer

        locked_customer = TicketCategoryService._lock_active_customer(
            customer
        )
        TicketCategoryService._ensure_actor_authorized(
            performed_by,
            locked_customer,
            action="manage",
        )

        try:
            category = TicketCategory.objects.select_for_update().get(
                pk=category.pk,
                customer=locked_customer,
            )
        except TicketCategory.DoesNotExist:
            raise NotFound("Ticket category not found.")

        if category.is_active:
            other_active_exists = (
                TicketCategory.objects
                .filter(
                    customer=locked_customer,
                    is_active=True,
                )
                .exclude(pk=category.pk)
                .exists()
            )
            if not other_active_exists:
                raise ValidationError({
                    "detail": (
                        "Cannot delete the last active category for this company."
                    )
                })

        category_id = category.pk
        category_name = category.name
        old_values = {
            "customer_id": str(category.customer_id),
            "name": category_name,
            "description": category.description,
            "is_active": category.is_active,
        }

        try:
            with transaction.atomic():
                category.delete()
        except ProtectedError:
            raise CategoryInUse()

        AuditLog.objects.create(
            user=performed_by,
            action="ticket_category.deleted",
            entity_type="TicketCategory",
            entity_id=category_id,
            old_values=old_values,
            ip_address=ip_address,
            user_agent=user_agent,
        )

        return {
            "id": str(category_id),
            "name": category_name,
        }
