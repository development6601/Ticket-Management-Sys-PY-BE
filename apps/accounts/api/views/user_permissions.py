from uuid import UUID
from django.core.exceptions import ObjectDoesNotExist
from django.db.models import Prefetch, Q
from drf_spectacular.utils import (
    extend_schema,
    OpenApiParameter,
    OpenApiTypes,
    OpenApiExample,
    PolymorphicProxySerializer,
)
from rest_framework import status
from rest_framework.pagination import PageNumberPagination
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.models import (
    Permission,
    Role,
    User,
    UserPermission,
    RolePermission,
)
from apps.customers.models import Customer, CustomerUser
from apps.accounts.permissions.rbac import (
    get_user_permission_manager,
)
from apps.accounts.api.serializers.permissions import (
    UserPermissionBulkSerializer,
    AssignUserPermissionResponseSerializer,
    RemoveUserPermissionResponseSerializer,
    UserPermissionListResponseSerializer,
    UserPermissionDetailResponseSerializer,
)
from apps.accounts.services.user_permissions import (
    UserPermissionService,
)


class UserPermissionPagination(PageNumberPagination):
    page_size = 10
    page_size_query_param = "page_size"
    max_page_size = 100


def build_user_permission_data(user):
    memberships = getattr(
        user,
        "prefetched_customer_memberships",
        None,
    )
    if memberships is None:
        membership = (
            CustomerUser.objects
            .select_related("customer")
            .filter(user=user)
            .first()
        )
    else:
        membership = memberships[0] if memberships else None

    try:
        user_role = user.user_role
    except ObjectDoesNotExist:
        user_role = None
    role = user_role.role if user_role else None

    direct_assignments = getattr(
        user,
        "prefetched_direct_permissions",
        None,
    )
    if direct_assignments is None:
        direct_assignments = (
            UserPermission.objects
            .select_related("permission")
            .filter(user=user)
        )

    direct_permissions = {
        (
            item.permission.module,
            item.permission.action,
        )
        for item in direct_assignments
    }

    role_permissions = set()

    is_admin = bool(
        membership is None
        and (
            user.is_superuser
            or (role is not None and role.code == "ADMIN")
        )
    )

    if is_admin:
        all_perms = getattr(
            user,
            "prefetched_all_system_permissions",
            None,
        )
        if all_perms is None:
            all_perms = set(
                Permission.objects.values_list(
                    "module",
                    "action",
                )
            )
        role_permissions.update(all_perms)
    elif membership is None and role is not None:
        if (
            hasattr(role, "_prefetched_objects_cache")
            and "role_permissions" in role._prefetched_objects_cache
        ):
            role_permissions.update(
                (
                    rp.permission.module,
                    rp.permission.action,
                )
                for rp in role.role_permissions.all()
            )
        else:
            role_permissions.update(
                role.role_permissions.values_list(
                    "permission__module",
                    "permission__action",
                )
            )

    if membership and membership.is_primary_contact:
        prefetched_customer_role_perms = getattr(
            user,
            "prefetched_customer_role_permissions",
            None,
        )
        if prefetched_customer_role_perms is not None:
            role_permissions.update(
                (
                    rp.permission.module,
                    rp.permission.action,
                )
                for rp in prefetched_customer_role_perms
            )
        else:
            role_permissions.update(
                RolePermission.objects.filter(
                    role__code="CUSTOMER"
                ).values_list(
                    "permission__module",
                    "permission__action",
                )
            )

    def names(items):
        return sorted(
            f"{module}.{action}"
            for module, action in items
        )

    account_active = (
        user.is_active
        and user.account_status == User.AccountStatus.ACTIVE
    )

    if membership:
        company = membership.customer
        account_active = (
            account_active
            and company.deleted_at is None
            and company.is_active
            and company.account_status
            == Customer.AccountStatus.ACTIVE
        )

    return {
        "user": {
            "id": user.id,
            "email": user.email,
            "account_status": user.account_status,
            "is_active": user.is_active,
            "is_primary_contact": bool(
                membership and membership.is_primary_contact
            ),
        },
        "role_permissions": names(role_permissions),
        "direct_permissions": names(direct_permissions),
        "effective_permissions": (
            names(role_permissions | direct_permissions)
            if account_active
            else []
        ),
        "access_enabled": account_active,
    }


class UserPermissionAssignView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(
        tags=["User Permissions"],
        request=UserPermissionBulkSerializer,
        responses={201: AssignUserPermissionResponseSerializer},
        summary="Assign permissions to users",
        description=(
            "Assign one or more permissions directly to one or more users in bulk. "
            "Internal administrators can assign permissions across all users. "
            "Customer primary contacts can assign permissions only to users of their own company, "
            "and only permissions configured on the CUSTOMER role."
        ),
        examples=[
            OpenApiExample(
                name="Assign Permissions Request Example",
                summary="Bulk assignment request payload",
                description="List of target user UUIDs and permission UUIDs to assign.",
                request_only=True,
                value={
                    "user_ids": [
                        "c8a1b2c3-d4e5-4f6a-8b9c-0d1e2f3a4b5c",
                        "a1b2c3d4-e5f6-4a7b-8c9d-0e1f2a3b4c5d"
                    ],
                    "permission_ids": [
                        "e1f2a3b4-c5d6-4e7f-8a9b-0c1d2e3f4a5b",
                        "f2a3b4c5-d6e7-4f8a-9b0c-1d2e3f4a5b6c"
                    ]
                },
            ),
            OpenApiExample(
                name="Assign Permissions Response Example",
                summary="Successful assignment response",
                description="Summary breakdown of newly assigned and already assigned permissions.",
                response_only=True,
                status_codes=["201"],
                value={
                    "success": True,
                    "message": "Permissions assigned to users successfully.",
                    "data": {
                        "summary": {
                            "requested": 4,
                            "assigned": 3,
                            "already_assigned": 1
                        },
                        "assigned": [
                            {
                                "user_id": "c8a1b2c3-d4e5-4f6a-8b9c-0d1e2f3a4b5c",
                                "user_email": "user1@company.com",
                                "permission_id": "e1f2a3b4-c5d6-4e7f-8a9b-0c1d2e3f4a5b",
                                "permission_name": "tickets.create",
                                "permission_code": "tickets.create"
                            },
                            {
                                "user_id": "a1b2c3d4-e5f6-4a7b-8c9d-0e1f2a3b4c5d",
                                "user_email": "user2@company.com",
                                "permission_id": "e1f2a3b4-c5d6-4e7f-8a9b-0c1d2e3f4a5b",
                                "permission_name": "tickets.create",
                                "permission_code": "tickets.create"
                            },
                            {
                                "user_id": "a1b2c3d4-e5f6-4a7b-8c9d-0e1f2a3b4c5d",
                                "user_email": "user2@company.com",
                                "permission_id": "f2a3b4c5-d6e7-4f8a-9b0c-1d2e3f4a5b6c",
                                "permission_name": "tickets.update",
                                "permission_code": "tickets.update"
                            }
                        ],
                        "already_assigned": [
                            {
                                "user_id": "c8a1b2c3-d4e5-4f6a-8b9c-0d1e2f3a4b5c",
                                "user_email": "user1@company.com",
                                "permission_id": "f2a3b4c5-d6e7-4f8a-9b0c-1d2e3f4a5b6c",
                                "permission_name": "tickets.update",
                                "permission_code": "tickets.update",
                                "reason": "Permission is already assigned to this user."
                            }
                        ]
                    }
                },
            ),
        ],
    )
    def post(self, request):
        manager_type, manager_customer_id = (
            get_user_permission_manager(request.user)
        )
        if manager_type is None:
            return Response({
                "success": False,
                "message": "You cannot assign user permissions.",
                "data": None,
            }, status=status.HTTP_403_FORBIDDEN)

        serializer = UserPermissionBulkSerializer(
            data=request.data
        )
        serializer.is_valid(raise_exception=True)

        result = UserPermissionService.assign(
            actor=request.user,
            **serializer.validated_data,
        )

        return Response({
            "success": True,
            "message": "Permissions assigned to users successfully.",
            "data": result,
        }, status=status.HTTP_201_CREATED)


class UserPermissionRemoveView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(
        tags=["User Permissions"],
        request=UserPermissionBulkSerializer,
        responses={200: RemoveUserPermissionResponseSerializer},
        summary="Remove permissions from users",
        description=(
            "Remove one or more direct permissions from one or more users in bulk. "
            "Internal administrators can remove permissions across all users. "
            "Customer primary contacts can remove permissions only from users of their own company."
        ),
        examples=[
            OpenApiExample(
                name="Remove Permissions Request Example",
                summary="Bulk removal request payload",
                description="List of target user UUIDs and permission UUIDs to revoke.",
                request_only=True,
                value={
                    "user_ids": [
                        "c8a1b2c3-d4e5-4f6a-8b9c-0d1e2f3a4b5c"
                    ],
                    "permission_ids": [
                        "e1f2a3b4-c5d6-4e7f-8a9b-0c1d2e3f4a5b"
                    ]
                },
            ),
            OpenApiExample(
                name="Remove Permissions Response Example",
                summary="Successful removal response",
                description="Summary breakdown of removed and non-assigned permissions.",
                response_only=True,
                status_codes=["200"],
                value={
                    "success": True,
                    "message": "Permissions removed successfully.",
                    "data": {
                        "summary": {
                            "requested": 1,
                            "removed": 1,
                            "not_assigned": 0
                        },
                        "removed": [
                            {
                                "user_id": "c8a1b2c3-d4e5-4f6a-8b9c-0d1e2f3a4b5c",
                                "user_email": "user1@company.com",
                                "permission_id": "e1f2a3b4-c5d6-4e7f-8a9b-0c1d2e3f4a5b",
                                "permission_name": "tickets.create",
                                "permission_code": "tickets.create"
                            }
                        ],
                        "not_assigned": []
                    }
                },
            ),
        ],
    )
    def delete(self, request):
        manager_type, manager_customer_id = (
            get_user_permission_manager(request.user)
        )
        if manager_type is None:
            return Response({
                "success": False,
                "message": "You cannot remove user permissions.",
                "data": None,
            }, status=status.HTTP_403_FORBIDDEN)

        serializer = UserPermissionBulkSerializer(
            data=request.data
        )
        serializer.is_valid(raise_exception=True)

        result = UserPermissionService.remove(
            actor=request.user,
            **serializer.validated_data,
        )

        removed_count = result["summary"]["removed"]

        return Response({
            "success": True,
            "message": (
                "Permissions removed successfully."
                if removed_count
                else "No permissions were removed."
            ),
            "data": result,
        }, status=status.HTTP_200_OK)


class UserPermissionListView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(
        tags=["User Permissions"],
        parameters=[
            OpenApiParameter(
                name="user_id",
                type=OpenApiTypes.UUID,
                required=False,
                location=OpenApiParameter.QUERY,
                description="Optional. When supplied, returns detailed permission breakdown for this specific user.",
            ),
            OpenApiParameter(
                name="page",
                type=OpenApiTypes.INT,
                required=False,
                location=OpenApiParameter.QUERY,
                description="Page number for paginated list (default: 1).",
            ),
            OpenApiParameter(
                name="page_size",
                type=OpenApiTypes.INT,
                required=False,
                location=OpenApiParameter.QUERY,
                description="Number of results per page (default: 10, max: 100).",
            ),
        ],
        responses={
            200: PolymorphicProxySerializer(
                component_name="UserPermissionsResponse",
                serializers=[
                    UserPermissionListResponseSerializer,
                    UserPermissionDetailResponseSerializer,
                ],
                resource_type_field_name=None,
            )
        },
        summary="List or retrieve user permissions",
        description=(
            "Retrieve user permissions with dual behavior:\n"
            "- **Without `user_id` query param**: Returns a paginated list of users with their permission breakdowns. "
            "Customer primary contacts see only their company's users; internal admins see all users.\n"
            "- **With `?user_id=<uuid>`**: Returns the permission detail object directly for the specified user."
        ),
        examples=[
            OpenApiExample(
                name="Paginated User Permissions List Example",
                summary="List all users with permission breakdowns",
                description="Returned when query param 'user_id' is omitted.",
                response_only=True,
                status_codes=["200"],
                value={
                    "success": True,
                    "message": "User permissions retrieved successfully.",
                    "data": {
                        "count": 2,
                        "next": None,
                        "previous": None,
                        "results": [
                            {
                                "user": {
                                    "id": "11111111-1111-4111-8111-111111111111",
                                    "email": "employee@example.com",
                                    "account_status": "ACTIVE",
                                    "is_active": True,
                                    "is_primary_contact": False
                                },
                                "role_permissions": [],
                                "direct_permissions": [
                                    "users.view",
                                    "tickets.create"
                                ],
                                "effective_permissions": [
                                    "tickets.create",
                                    "users.view"
                                ],
                                "access_enabled": True
                            },
                            {
                                "user": {
                                    "id": "22222222-2222-4222-8222-222222222222",
                                    "email": "primary@example.com",
                                    "account_status": "ACTIVE",
                                    "is_active": True,
                                    "is_primary_contact": True
                                },
                                "role_permissions": [
                                    "tickets.create",
                                    "tickets.view",
                                    "users.view"
                                ],
                                "direct_permissions": [],
                                "effective_permissions": [
                                    "tickets.create",
                                    "tickets.view",
                                    "users.view"
                                ],
                                "access_enabled": True
                            }
                        ]
                    }
                },
            ),
            OpenApiExample(
                name="Single User Permission Detail Example",
                summary="Individual user permission detail",
                description="Returned when querying '?user_id=<uuid>'.",
                response_only=True,
                status_codes=["200"],
                value={
                    "success": True,
                    "message": "User permissions retrieved successfully.",
                    "data": {
                        "user": {
                            "id": "11111111-1111-4111-8111-111111111111",
                            "email": "employee@example.com",
                            "account_status": "ACTIVE",
                            "is_active": True,
                            "is_primary_contact": False
                        },
                        "role_permissions": [],
                        "direct_permissions": [
                            "users.view",
                            "tickets.create"
                        ],
                        "effective_permissions": [
                            "tickets.create",
                            "users.view"
                        ],
                        "access_enabled": True
                    }
                },
            ),
        ],
    )
    def get(self, request):
        manager_type, manager_customer_id = (
            get_user_permission_manager(request.user)
        )
        if manager_type is None:
            return Response({
                "success": False,
                "message": "You cannot view user permissions.",
                "data": None,
            }, status=status.HTTP_403_FORBIDDEN)

        if "user_id" in request.query_params:
            raw_user_id = request.query_params.get("user_id", "").strip()
            try:
                user_id = UUID(raw_user_id)
            except (ValueError, TypeError, AttributeError):
                return Response({
                    "success": False,
                    "message": "Invalid user_id.",
                    "data": None,
                }, status=status.HTTP_400_BAD_REQUEST)

            user = (
                User.objects.filter(id=user_id)
                .select_related("user_role__role")
                .prefetch_related(
                    Prefetch(
                        "customer_members",
                        queryset=CustomerUser.objects.select_related(
                            "customer"
                        ),
                        to_attr="prefetched_customer_memberships",
                    ),
                    Prefetch(
                        "direct_permissions",
                        queryset=UserPermission.objects.select_related(
                            "permission"
                        ),
                        to_attr="prefetched_direct_permissions",
                    ),
                    "user_role__role__role_permissions__permission",
                )
                .first()
            )
            if user is None:
                return Response({
                    "success": False,
                    "message": "User not found.",
                    "data": None,
                }, status=status.HTTP_404_NOT_FOUND)

            if manager_type == "customer":
                is_company_user = CustomerUser.objects.filter(
                    user=user,
                    customer_id=manager_customer_id,
                ).exists()
                if not is_company_user:
                    return Response({
                        "success": False,
                        "message": "You cannot view permissions for a user outside your company.",
                        "data": None,
                    }, status=status.HTTP_403_FORBIDDEN)

            user.prefetched_customer_role_permissions = list(
                RolePermission.objects
                .select_related("permission")
                .filter(role__code="CUSTOMER")
            )
            user.prefetched_all_system_permissions = set(
                Permission.objects.values_list(
                    "module",
                    "action",
                )
            )

            return Response({
                "success": True,
                "message": "User permissions retrieved successfully.",
                "data": build_user_permission_data(user),
            }, status=status.HTTP_200_OK)

        if manager_type == "internal":
            queryset = User.objects.all()
        else:
            company_user_ids = CustomerUser.objects.filter(
                customer_id=manager_customer_id
            ).values_list("user_id", flat=True)
            queryset = User.objects.filter(
                id__in=company_user_ids
            )

        queryset = (
            queryset
            .select_related("user_role__role")
            .prefetch_related(
                Prefetch(
                    "customer_members",
                    queryset=CustomerUser.objects.select_related(
                        "customer"
                    ),
                    to_attr="prefetched_customer_memberships",
                ),
                Prefetch(
                    "direct_permissions",
                    queryset=UserPermission.objects.select_related(
                        "permission"
                    ),
                    to_attr="prefetched_direct_permissions",
                ),
                "user_role__role__role_permissions__permission",
            )
            .order_by("id")
        )

        paginator = UserPermissionPagination()
        page = paginator.paginate_queryset(queryset, request)

        customer_role_assignments = list(
            RolePermission.objects
            .select_related("permission")
            .filter(role__code="CUSTOMER")
        )
        all_system_permissions = set(
            Permission.objects.values_list(
                "module",
                "action",
            )
        )
        for user in page:
            user.prefetched_customer_role_permissions = (
                customer_role_assignments
            )
            user.prefetched_all_system_permissions = (
                all_system_permissions
            )

        results = [build_user_permission_data(u) for u in page]

        return Response({
            "success": True,
            "message": "User permissions retrieved successfully.",
            "data": {
                "count": paginator.page.paginator.count,
                "next": paginator.get_next_link(),
                "previous": paginator.get_previous_link(),
                "results": results,
            },
        }, status=status.HTTP_200_OK)
