from django.core.exceptions import ObjectDoesNotExist
from django.db import transaction, IntegrityError
from rest_framework.exceptions import PermissionDenied, ValidationError

from apps.accounts.models import (
    User,
    Permission,
    Role,
    RolePermission,
    UserPermission,
)
from apps.customers.models import Customer, CustomerUser
from apps.accounts.permissions.rbac import (
    get_user_permission_manager,
)


class UserPermissionService:

    @staticmethod
    def _get_authorized_manager(actor):
        manager_type, manager_customer_id = (
            get_user_permission_manager(actor)
        )
        if manager_type is None:
            raise PermissionDenied(
                "You cannot manage user permissions."
            )
        return manager_type, manager_customer_id

    @staticmethod
    def _get_users_and_permissions(user_ids, permission_ids, for_update=False):
        users_qs = (
            User.objects.filter(id__in=user_ids)
            .select_related("user_role__role")
            .order_by("id")
        )
        if for_update:
            users_qs = users_qs.select_for_update(of=("self",))

        users = list(users_qs)
        permissions = list(
            Permission.objects.filter(
                id__in=permission_ids
            ).order_by("id")
        )

        found_users = {str(u.id) for u in users}
        found_permissions = {str(p.id) for p in permissions}

        errors = {}

        missing_users = [
            str(uid) for uid in user_ids
            if str(uid) not in found_users
        ]
        missing_permissions = [
            str(pid) for pid in permission_ids
            if str(pid) not in found_permissions
        ]

        if missing_users:
            errors["user_ids"] = [
                f"User does not exist: {uid}"
                for uid in missing_users
            ]

        if missing_permissions:
            errors["permission_ids"] = [
                f"Permission does not exist: {pid}"
                for pid in missing_permissions
            ]

        if errors:
            raise ValidationError(errors)

        return users, permissions

    @staticmethod
    def _validate_scope(
        *,
        users,
        permissions,
        manager_type,
        manager_customer_id,
        assigning,
    ):
        memberships = {
            m.user_id: m
            for m in CustomerUser.objects.select_related(
                "customer"
            ).filter(user__in=users)
        }

        if manager_type not in ("internal", "customer"):
            raise PermissionDenied(
                "You cannot manage user permissions."
            )

        customer_role = None
        allowed_customer_ids = set()
        has_customer_users = any(u.id in memberships for u in users)

        if manager_type == "customer" or (assigning and has_customer_users):
            customer_role = Role.objects.filter(
                code="CUSTOMER"
            ).first()

            if customer_role is None:
                if manager_type == "customer":
                    raise PermissionDenied(
                        "Customer permissions are not configured."
                    )
                else:
                    raise ValidationError(
                        "The system CUSTOMER role is not configured in the database."
                    )

            allowed_customer_ids = set(
                RolePermission.objects.filter(
                    role=customer_role
                ).values_list("permission_id", flat=True)
            )

        if manager_type == "customer":
            for permission in permissions:
                if permission.id not in allowed_customer_ids:
                    raise PermissionDenied(
                        f"Permission '{permission.module}."
                        f"{permission.action}' cannot be "
                        "managed by a customer primary contact."
                    )

        if assigning and has_customer_users:
            for permission in permissions:
                if permission.id not in allowed_customer_ids:
                    raise PermissionDenied(
                        "Customer users can receive only "
                        "permissions configured on "
                        "the CUSTOMER role."
                    )

        for user in users:
            membership = memberships.get(user.id)

            if manager_type == "customer":
                if (
                    membership is None
                    or membership.customer_id
                    != manager_customer_id
                ):
                    raise PermissionDenied(
                        "You can manage permissions only "
                        "for users of your own company."
                    )

                if membership.is_primary_contact:
                    raise PermissionDenied(
                        "The primary contact's permissions "
                        "are managed through the CUSTOMER role."
                    )

            if assigning:
                if (
                    not user.is_active
                    or user.account_status
                    != User.AccountStatus.ACTIVE
                ):
                    raise ValidationError({
                        "user_ids": [
                            f"Cannot assign permissions to "
                            f"inactive user: {user.email}"
                        ]
                    })

                if membership is not None:
                    customer = membership.customer

                    if (
                        customer.deleted_at is not None
                        or not customer.is_active
                        or customer.account_status
                        != Customer.AccountStatus.ACTIVE
                    ):
                        raise ValidationError({
                            "user_ids": [
                                f"Cannot assign permissions "
                                f"to a user of an inactive "
                                f"company: {user.email}"
                            ]
                        })
                else:
                    try:
                        user_role = user.user_role
                    except ObjectDoesNotExist:
                        user_role = None

                    if user_role is None or user_role.role is None:
                        raise ValidationError({
                            "user_ids": [
                                f"Cannot assign permissions to an internal user without an assigned role: {user.email}"
                            ]
                        })

    @staticmethod
    @transaction.atomic
    def assign(
        *,
        actor,
        user_ids,
        permission_ids,
    ):
        manager_type, manager_customer_id = (
            UserPermissionService._get_authorized_manager(actor)
        )

        users, permissions = (
            UserPermissionService._get_users_and_permissions(
                user_ids, permission_ids, for_update=True
            )
        )

        UserPermissionService._validate_scope(
            users=users,
            permissions=permissions,
            manager_type=manager_type,
            manager_customer_id=manager_customer_id,
            assigning=True,
        )

        existing = set(
            UserPermission.objects.filter(
                user_id__in=user_ids,
                permission_id__in=permission_ids,
            ).values_list("user_id", "permission_id")
        )

        assigned = []
        already_assigned = []
        new_records = []

        for user in users:
            for permission in permissions:
                result = {
                    "user_id": user.id,
                    "user_email": user.email,
                    "permission_id": permission.id,
                    "permission_name": permission.name,
                    "permission_code": f"{permission.module}.{permission.action}",
                }

                if (user.id, permission.id) in existing:
                    already_assigned.append({
                        **result,
                        "reason": (
                            "Permission is already assigned "
                            "to this user."
                        ),
                    })
                else:
                    new_records.append(
                        UserPermission(
                            user=user,
                            permission=permission,
                        )
                    )
                    assigned.append(result)

        if new_records:
            try:
                UserPermission.objects.bulk_create(new_records)
            except IntegrityError:
                raise ValidationError(
                    "One or more permissions could not be assigned due to a concurrent conflict. Please refresh and try again."
                )

        return {
            "summary": {
                "requested": len(users) * len(permissions),
                "assigned": len(assigned),
                "already_assigned": len(already_assigned),
            },
            "assigned": assigned,
            "already_assigned": already_assigned,
        }

    @staticmethod
    @transaction.atomic
    def remove(
        *,
        actor,
        user_ids,
        permission_ids,
    ):
        manager_type, manager_customer_id = (
            UserPermissionService._get_authorized_manager(actor)
        )

        users, permissions = (
            UserPermissionService._get_users_and_permissions(
                user_ids, permission_ids, for_update=True
            )
        )

        UserPermissionService._validate_scope(
            users=users,
            permissions=permissions,
            manager_type=manager_type,
            manager_customer_id=manager_customer_id,
            assigning=False,
        )

        existing = set(
            UserPermission.objects.filter(
                user_id__in=user_ids,
                permission_id__in=permission_ids,
            ).values_list("user_id", "permission_id")
        )

        removed = []
        not_assigned = []

        for user in users:
            for permission in permissions:
                result = {
                    "user_id": user.id,
                    "user_email": user.email,
                    "permission_id": permission.id,
                    "permission_name": permission.name,
                    "permission_code": f"{permission.module}.{permission.action}",
                }

                if (user.id, permission.id) in existing:
                    removed.append(result)
                else:
                    not_assigned.append({
                        **result,
                        "reason": (
                            "Permission is not assigned "
                            "to this user."
                        ),
                    })

        if removed:
            UserPermission.objects.filter(
                user_id__in=user_ids,
                permission_id__in=permission_ids,
            ).delete()

        return {
            "summary": {
                "requested": len(users) * len(permissions),
                "removed": len(removed),
                "not_assigned": len(not_assigned),
            },
            "removed": removed,
            "not_assigned": not_assigned,
        }
