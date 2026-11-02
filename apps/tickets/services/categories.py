from django.db import IntegrityError, transaction
from django.db.models.deletion import ProtectedError
from rest_framework import status
from rest_framework.exceptions import (
    APIException,
    NotFound,
    ValidationError,
)

from apps.audit.models import AuditLog
from apps.tickets.models import TicketCategory


class CategoryInUse(APIException):
    status_code = status.HTTP_409_CONFLICT
    default_detail = (
        "This category is already used by tickets. "
        "Deactivate it instead."
    )
    default_code = "category_in_use"


class TicketCategoryService:

    @staticmethod
    @transaction.atomic
    def create(
        *,
        validated_data,
        performed_by,
        ip_address=None,
        user_agent=None,
    ):
        try:
            # Savepoint allows us to handle a database uniqueness
            # error without breaking the outer transaction.
            with transaction.atomic():
                category = TicketCategory.objects.create(
                    **validated_data
                )
        except IntegrityError:
            raise ValidationError({
                "name": ["A category with this name already exists."]
            })

        AuditLog.objects.create(
            user=performed_by,
            action="ticket_category.created",
            entity_type="TicketCategory",
            entity_id=category.pk,
            new_values={
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
        category,
        validated_data,
        performed_by,
        ip_address=None,
        user_agent=None,
    ):
        try:
            category = TicketCategory.objects.select_for_update().get(
                pk=category.pk
            )
        except TicketCategory.DoesNotExist:
            raise NotFound("Ticket category not found.")

        old_values = {
            "name": category.name,
            "description": category.description,
            "is_active": category.is_active,
        }

        for field, value in validated_data.items():
            setattr(category, field, value)

        new_values = {
            "name": category.name,
            "description": category.description,
            "is_active": category.is_active,
        }

        if old_values == new_values:
            return category

        try:
            with transaction.atomic():
                category.save()
        except IntegrityError:
            raise ValidationError({
                "name": ["A category with this name already exists."]
            })

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
        category,
        performed_by,
        ip_address=None,
        user_agent=None,
    ):
        try:
            category = TicketCategory.objects.select_for_update().get(
                pk=category.pk
            )
        except TicketCategory.DoesNotExist:
            raise NotFound("Ticket category not found.")

        category_id = category.pk
        old_values = {
            "name": category.name,
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
