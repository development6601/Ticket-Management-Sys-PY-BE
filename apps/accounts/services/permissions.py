from django.utils import timezone
from django.db import transaction, IntegrityError
from django.db.models.deletion import ProtectedError
from rest_framework.exceptions import ValidationError

from apps.accounts.models import Permission

class PermissionService:

    @staticmethod
    @transaction.atomic
    def create_permission(*, validated_data):
        try : 
            if isinstance(validated_data, list):
                now = timezone.now()
                permissions = [
                    Permission(**data, created_at=now, updated_at=now)
                    for data in validated_data
                ]

                return Permission.objects.bulk_create(
                    permissions
                )
            
            return Permission.objects.create(
                **validated_data,
            )

        except IntegrityError:
            raise ValidationError(
                "A permission with the same name or module and action already exists."
            )
            
    @staticmethod
    @transaction.atomic
    def update_permission(*, permission, validated_data):
        changed_fields = []

        for field, value in validated_data.items():
            if getattr(permission, field) != value:
                setattr(permission, field, value)
                changed_fields.append(field)

        if not changed_fields:
            return permission

        try: 
            permission.save(
                update_fields=[
                    *changed_fields,
                    "updated_at",
                ]
            )
        except IntegrityError:
            raise ValidationError(
                "A permission with the same name or module and action already exists."
            )

        return permission

    @staticmethod
    @transaction.atomic
    def delete_permission(*, permission_ids):
        permissions = list(
            Permission.objects.filter(
                id__in=permission_ids
            )
        )

        found_ids = {
            str(permission.id)
            for permission in permissions
        }

        missing_ids = [
            permission_id
            for permission_id in permission_ids
            if str(permission_id) not in found_ids
        ]

        if missing_ids:
            raise ValidationError({
                "ids": [
                    f"Permission does not exist: {permission_id}"
                    for permission_id in missing_ids
                ]
            })

        deleted_ids = [
            str(permission.id)
            for permission in permissions
        ]

        try:
            Permission.objects.filter(
                id__in=permission_ids
            ).delete()

        except ProtectedError:
            raise ValidationError(
                "One or more permissions cannot be deleted because they are assigned to roles or users."
            )

        return deleted_ids
