from django.db import transaction, IntegrityError
from rest_framework.exceptions import ValidationError
from django.db.models.deletion import ProtectedError

from apps.accounts.models import Role, Permission, RolePermission

class RoleService:

    @staticmethod
    def create_roles(*, validated_data):
        try: 
            with transaction.atomic():
                if isinstance(validated_data, list):
                    roles = [
                        Role(
                            **data,
                            is_system_role=False,
                        )
                        for data in validated_data
                    ]

                    return Role.objects.bulk_create(roles)

                return Role.objects.create(
                    **validated_data,
                    is_system_role = False,
                )

        except IntegrityError:
            raise ValidationError(
                "A role with the same code or name already exists."
            )

    @staticmethod
    def update_role(*, role, validated_data):
        if role.is_system_role:
            raise ValidationError(
                "System roles cannot be modified."
            )

        changed_fields = []

        for field, value in validated_data.items():
            if getattr(role, field) != value:
                setattr(role, field, value)
                changed_fields.append(field)

        if not changed_fields:
            return role

        try: 
            with transaction.atomic():
                role.save(
                    update_fields=[
                        *changed_fields,
                        "updated_at",
                    ]
                )
        except IntegrityError:
            raise ValidationError(
                "A role with the same name already exists."
            )

        return role

    @staticmethod
    def delete_roles(*, role_ids):
        try:
            with transaction.atomic():
                roles = list(
                    Role.objects.select_for_update().filter(id__in=role_ids)
                )

                found_ids = {str(role.id) for role in roles}
                missing_ids = [
                    str(role_id)
                    for role_id in role_ids
                    if str(role_id) not in found_ids
                ]

                if missing_ids:
                    raise ValidationError({
                        "ids": [
                            f"Role does not exist: {role_id}"
                            for role_id in missing_ids
                        ]
                    })

                system_roles = [
                    role.code for role in roles if role.is_system_role
                ]
                if system_roles:
                    raise ValidationError({
                        "ids": [
                            f"System roles cannot be deleted: {', '.join(system_roles)}."
                        ]
                    })

                deleted_ids = [str(role.id) for role in roles]

                Role.objects.filter(id__in=role_ids).delete()

                return deleted_ids

        except ProtectedError:
            raise ValidationError(
                "Cannot delete roles because one or more selected roles are assigned to users or permissions."
            )

    @staticmethod
    def delete_all_roles():
        try:
            with transaction.atomic():
                custom_roles = list(
                    Role.objects.select_for_update().filter(is_system_role=False)
                )

                if not custom_roles:
                    raise ValidationError(
                        "No custom roles available to delete."
                    )

                deleted_ids = [str(role.id) for role in custom_roles]

                Role.objects.filter(
                    id__in=[role.id for role in custom_roles]
                ).delete()

                return deleted_ids

        except ProtectedError:
            raise ValidationError(
                "Cannot delete all custom roles because one or more roles are assigned to users or permissions."
            )
        

    #assigning 1 or more permission to role 
    @staticmethod
    @transaction.atomic
    def assign_permissions_to_roles(*, role_ids, permission_ids):

        roles = Role.objects.filter(id__in=role_ids)

        found_role_ids = {
            str(role.id)
            for role in roles
        }

        requested_role_ids = {
            str(role_id)
            for role_id in role_ids
        }

        missing_role_ids = requested_role_ids - found_role_ids

        if missing_role_ids:
            raise ValidationError({
                "role_ids": [
                    f"Role does not exist: {role_id}"
                    for role_id in missing_role_ids
                ]
            })

        # Prevent modifying permissions of system roles
        system_roles = [
            role.code or role.name
            for role in roles
            if role.is_system_role
        ]
        if system_roles:
            raise ValidationError({
                "role_ids": [
                    f"Cannot modify permissions of system roles: {', '.join(system_roles)}."
                ]
            })

        permissions = Permission.objects.filter(
            id__in=permission_ids
        )

        found_permission_ids = {
            str(permission.id)
            for permission in permissions
        }

        requested_permission_ids = {
            str(permission_id)
            for permission_id in permission_ids
        }

        missing_permission_ids = (
            requested_permission_ids - found_permission_ids
        )

        if missing_permission_ids:
            raise ValidationError({
                "permission_ids": [
                    f"Permission does not exist: {permission_id}"
                    for permission_id in missing_permission_ids
                ]
            })

        existing_assignments = RolePermission.objects.filter(
            role_id__in=role_ids,
            permission_id__in=permission_ids,
        ).values_list(
            "role_id",
            "permission_id",
        )

        existing_pairs = {
            (str(role_id), str(permission_id))
            for role_id, permission_id in existing_assignments
        }

        assigned = []
        already_assigned = []
        new_assignments = []

        for role in roles:
            for permission in permissions:

                result = {
                    "role_id": role.id,
                    "role_name": role.name,
                    "permission_id": permission.id,
                    "permission_name": permission.name,
                }

                pair = (
                    str(role.id),
                    str(permission.id),
                )

                if pair in existing_pairs:
                    already_assigned.append({
                        **result,
                        "reason": "Permission is already assigned to this role.",
                    })
                else:
                    new_assignments.append(
                        RolePermission(
                            role=role,
                            permission=permission,
                        )
                    )

                    assigned.append(result)

        if new_assignments:
            RolePermission.objects.bulk_create(new_assignments, ignore_conflicts=True)

        requested_count = len(roles) * len(permissions)

        return {
            "summary": {
                "requested": requested_count,
                "assigned": len(assigned),
                "already_assigned": len(already_assigned),
            },
            "assigned": assigned,
            "already_assigned": already_assigned,
        }
        

    @staticmethod
    @transaction.atomic
    def remove_permissions_from_roles(*, role_ids, permission_ids):

        roles = Role.objects.filter(id__in=role_ids)

        found_role_ids = {
            str(role.id)
            for role in roles
        }

        requested_role_ids = {
            str(role_id)
            for role_id in role_ids
        }

        missing_role_ids = requested_role_ids - found_role_ids

        if missing_role_ids:
            raise ValidationError({
                "role_ids": [
                    f"Role does not exist: {role_id}"
                    for role_id in missing_role_ids
                ]
            })

        # Prevent modifying permissions of system roles
        system_roles = [
            role.code or role.name
            for role in roles
            if role.is_system_role
        ]
        if system_roles:
            raise ValidationError({
                "role_ids": [
                    f"Cannot modify permissions of system roles: {', '.join(system_roles)}."
                ]
            })

        permissions = Permission.objects.filter(
            id__in=permission_ids
        )

        found_permission_ids = {
            str(permission.id)
            for permission in permissions
        }

        requested_permission_ids = {
            str(permission_id)
            for permission_id in permission_ids
        }

        missing_permission_ids = (
            requested_permission_ids - found_permission_ids
        )

        if missing_permission_ids:
            raise ValidationError({
                "permission_ids": [
                    f"Permission does not exist: {permission_id}"
                    for permission_id in missing_permission_ids
                ]
            })

        existing_assignments = RolePermission.objects.filter(
            role_id__in=role_ids,
            permission_id__in=permission_ids,
        ).select_related(
            "role",
            "permission",
        )

        existing_pairs = {
            (
                str(item.role_id),
                str(item.permission_id),
            )
            for item in existing_assignments
        }

        removed = []
        not_assigned = []

        for role in roles:
            for permission in permissions:

                result = {
                    "role_id": role.id,
                    "role_name": role.name,
                    "permission_id": permission.id,
                    "permission_name": permission.name,
                }

                pair = (
                    str(role.id),
                    str(permission.id),
                )

                if pair in existing_pairs:
                    removed.append(result)
                else:
                    not_assigned.append({
                        **result,
                        "reason": "Permission is not assigned to this role.",
                    })

        if removed:
            RolePermission.objects.filter(
                role_id__in=role_ids,
                permission_id__in=permission_ids,
            ).delete()

        requested_count = len(roles) * len(permissions)

        return {
            "summary": {
                "requested": requested_count,
                "removed": len(removed),
                "not_assigned": len(not_assigned),
            },
            "removed": removed,
            "not_assigned": not_assigned,
        }