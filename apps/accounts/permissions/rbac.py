from django.core.exceptions import ObjectDoesNotExist
from rest_framework.exceptions import PermissionDenied
from rest_framework.permissions import BasePermission

from apps.accounts.models import (
    Role,
    RolePermission,
    UserPermission,
    UserRole,
)
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

def user_has_direct_permission(user, permission_name):
    if not user or not permission_name:
        return False

    module, separator, action = permission_name.partition(".")

    if not separator or not module or not action:
        return False

    return UserPermission.objects.filter(
        user=user,
        permission__module__iexact=module,
        permission__action__iexact=action,
    ).exists()

def user_has_effective_permission(user, permission_name):
    if not user or not user.is_active:
        return False

    if user.account_status != user.AccountStatus.ACTIVE:
        return False

    membership = (
        CustomerUser.objects
        .select_related("customer")
        .filter(user=user)
        .first()
    )

    if membership:
        customer = membership.customer

        if (
            customer.deleted_at is not None
            or not customer.is_active
            or customer.account_status
            != Customer.AccountStatus.ACTIVE
        ):
            return False

        if membership.is_primary_contact:
            return (
                primary_has_customer_permission(permission_name)
                or user_has_direct_permission(user, permission_name)
            )

        return user_has_direct_permission(
            user, permission_name
        )

    if user.is_superuser:
        return True

    try:
        user_role = user.user_role
    except ObjectDoesNotExist:
        user_role = None

    if user_role is not None and user_role.role.code == "ADMIN":
        return True

    role_perm = (
        role_has_permission(user_role.role, permission_name)
        if user_role is not None
        else False
    )

    return (
        role_perm
        or user_has_direct_permission(user, permission_name)
    )


def get_user_permission_manager(user, required_permission=None):
    """
    Returns:
        ("internal", None)
        ("customer", customer_id)
        (None, None)
    """
    if not user or not user.is_authenticated:
        return None, None

    if (
        not user.is_active
        or user.account_status != user.AccountStatus.ACTIVE
    ):
        return None, None

    membership = (
        CustomerUser.objects
        .select_related("customer")
        .filter(user=user)
        .first()
    )

    if membership is not None:
        customer = membership.customer
        if (
            not membership.is_primary_contact
            or customer.deleted_at is not None
            or not customer.is_active
            or customer.account_status
            != Customer.AccountStatus.ACTIVE
        ):
            return None, None

        return "customer", customer.id

    if user.is_superuser:
        return "internal", None

    if UserRole.objects.filter(
        user=user,
        role__code="ADMIN",
    ).exists():
        return "internal", None

    if required_permission:
        if user_has_effective_permission(user, required_permission):
            return "internal", None
    else:
        if (
            user_has_effective_permission(user, ROLES_ASSIGN)
            or user_has_effective_permission(user, PERMISSIONS_UPDATE)
            or user_has_effective_permission(user, USER_PERMISSIONS_MANAGE)
            or user_has_effective_permission(user, PERMISSIONS_VIEW)
            or user_has_effective_permission(user, USERS_VIEW)
            or user_has_effective_permission(user, USER_PERMISSIONS_VIEW)
        ):
            return "internal", None

    return None, None


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
        if request.method in ("OPTIONS", "HEAD"):
            return True

        if not request.user or not request.user.is_authenticated:
            return False

        # If the requested method is not implemented on the view, allow the request
        # through so that DRF can dispatch to http_method_not_allowed (returning HTTP 405)
        handler = getattr(view, request.method.lower(), None)
        if handler is None:
            return True

        if (
            not request.user.is_active
            or request.user.account_status != request.user.AccountStatus.ACTIVE
        ):
            raise PermissionDenied(
                "Your account has been deactivated. Please contact your system administrator."
            )

        customer_membership = (
            CustomerUser.objects
            .select_related("customer")
            .filter(user=request.user)
            .first()
        )
        if customer_membership is not None:
            customer = customer_membership.customer
            if (
                customer.deleted_at is not None
                or not customer.is_active
                or customer.account_status != Customer.AccountStatus.ACTIVE
            ):
                raise PermissionDenied(
                    "Your customer company is not active. Please contact your administrator."
                )

        permission = getattr(
            view,
            "required_permission",
            self.required_permission,
        )

        if not permission:
            return False

        return user_has_effective_permission(
            request.user,
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

        if (
            not user.is_active
            or user.account_status != user.AccountStatus.ACTIVE
        ):
            raise PermissionDenied(
                "Your account has been deactivated. Please contact your system administrator."
            )

        # Check whether this user belongs to a customer company.
        customer_user = (
            CustomerUser.objects
            .filter(user=user)
            .select_related("customer")
            .first()
        )

        if customer_user is not None:
            customer = customer_user.customer
            if (
                customer.deleted_at is not None
                or not customer.is_active
                or customer.account_status != Customer.AccountStatus.ACTIVE
            ):
                raise PermissionDenied(
                    "Your customer company is not active. Please contact your administrator."
                )

            if customer_user.is_primary_contact:
                return (
                    primary_has_customer_permission(
                        USERS_CREATE_CUSTOMER
                    ) or user_has_direct_permission(
                        user, USERS_CREATE_CUSTOMER
                    )
                )

            return user_has_direct_permission(
                user, USERS_CREATE_CUSTOMER
            )

        if user.is_superuser:
            return True

        try:
            user_role = user.user_role
        except ObjectDoesNotExist:
            return False

        # ADMIN can create users.
        if user_role.role.code == "ADMIN":
            return True

        # Internal users require either customer or internal user creation permission.
        can_create_customer = role_has_permission(
            user_role.role,
            USERS_CREATE_CUSTOMER,
        )
        can_create_internal = role_has_permission(
            user_role.role,
            USERS_CREATE_INTERNAL,
        )
        return (
            can_create_customer 
            or can_create_internal
            or user_has_direct_permission(user, USERS_CREATE_CUSTOMER)
            or user_has_direct_permission(user, USERS_CREATE_INTERNAL)
        )
