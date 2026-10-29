from django.core.exceptions import ObjectDoesNotExist
from rest_framework.permissions import BasePermission

from apps.accounts.models import Role, RolePermission
from apps.accounts.permissions.constants import *
from apps.customers.models import Customer, CustomerUser


def role_has_permission(role, permission_name):
    """
    Check whether a role has a permission using
    the canonical module/action representation.
    """
    if role is None or not permission_name:
        return False

    module, separator, action = permission_name.partition(".")
    if not separator or not module or not action:
        return False

    return RolePermission.objects.filter(
        role=role,
        permission__module__iexact=module,
        permission__action__iexact=action,
    ).exists()


def primary_has_customer_permission(permission_name):
    customer_role = Role.objects.filter(
        code="CUSTOMER",
    ).first()
    return role_has_permission(
        customer_role,
        permission_name,
    )


class HasPermission(BasePermission):
    """
    Check whether the authenticated user has a specific permission.
    """

    required_permission = None

    def has_permission(self, request, view):
        if not request.user or not request.user.is_authenticated:
            return False

        if not request.user.is_active:
            return False

        #if customer company is deactivate customeruser doesn't have permission 
        if CustomerUser.objects.filter(
            user=request.user,
        ).exclude(
            customer__deleted_at__isnull=True,
            customer__is_active=True,
            customer__account_status=Customer.AccountStatus.ACTIVE,
        ).exists():
            return False

        if request.user.is_superuser:
            return True

        try:
            user_role = request.user.user_role
        except ObjectDoesNotExist:
            return False

        if user_role.role.code == "ADMIN":
            return True

        permission = getattr(
            view,
            "required_permission",
            self.required_permission,
        )

        if not permission:
            return False

        customer_membership = CustomerUser.objects.filter(
            user=request.user,
        ).first()

        if customer_membership is not None:
            if customer_membership.is_primary_contact:
                return primary_has_customer_permission(
                    permission
                )

            # Future: check individual user permissions here.
            return False

        # Internal users continue with existing role permissions.
        return role_has_permission(
            user_role.role,
            permission,
        )


class CanViewUsers(HasPermission):
    """
    Same as HasPermission(users.view), plus: the primary contact of a
    customer company may list their own company's users when users.view
    is enabled on the CUSTOMER role. Company scoping itself is applied
    in the view queryset.
    """

    required_permission = USERS_VIEW


class CanManageUsers(HasPermission):
    required_permission = USERS_VIEW


class CanManageRoles(HasPermission):
    required_permission = ROLES_VIEW


class CanManagePermissions(HasPermission):
    required_permission = PERMISSIONS_VIEW

class CanCreateUser(HasPermission):
    """
    Allows:
    - ADMIN
    - Primary customer contact
    - Customer user with USERS_CREATE_CUSTOMER
    - Internal user with USERS_CREATE_CUSTOMER

    This permission only authorizes access to the user-creation endpoint.
    The serializer must still validate which role/company can be created.
    """

    required_permission = USERS_CREATE_CUSTOMER

    def has_permission(self, request, view):
        if not request.user or not request.user.is_authenticated:
            return False

        user = request.user

        if not user.is_active:
            return False

        if CustomerUser.objects.filter(
            user=user,
        ).exclude(
            customer__deleted_at__isnull=True,
            customer__is_active=True,
            customer__account_status=Customer.AccountStatus.ACTIVE,
        ).exists():
            return False

        if user.is_superuser:
            return True

        try:
            user_role = user.user_role
        except ObjectDoesNotExist:
            return False

        # ADMIN can create users.
        if user_role.role.code == "ADMIN":
            return True

        # Check whether this user belongs to a customer company.
        customer_user = (
            CustomerUser.objects
            .filter(user=user)
            .select_related("customer")
            .first()
        )

        if customer_user is not None:
            if customer_user.is_primary_contact:
                return primary_has_customer_permission(
                    USERS_CREATE_CUSTOMER
                )

            # Future individual permissions.
            return False

        # Internal users require either customer or internal user creation permission.
        can_create_customer = role_has_permission(
            user_role.role,
            USERS_CREATE_CUSTOMER,
        )
        can_create_internal = role_has_permission(
            user_role.role,
            USERS_CREATE_INTERNAL,
        )
        return can_create_customer or can_create_internal



