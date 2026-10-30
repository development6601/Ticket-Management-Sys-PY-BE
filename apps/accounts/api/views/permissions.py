import uuid
from drf_spectacular.utils import extend_schema, OpenApiParameter, OpenApiTypes, OpenApiRequest, PolymorphicProxySerializer
from rest_framework import status
from rest_framework.exceptions import ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.api.serializers.permissions import *

from apps.accounts.models import Permission, Role
from apps.accounts.permissions.rbac import CanManagePermissions, HasPermission
from apps.accounts.permissions.constants import *
from apps.accounts.services.roles import RoleService
from apps.accounts.services.permissions import PermissionService

from rest_framework.pagination import PageNumberPagination
from django.db.models import Q


def role_permission_error_response(exc):
    errors = exc.detail
    if isinstance(errors, dict):
        errors = next(iter(errors.values()))
    if isinstance(errors, list):
        errors = errors[0]
    return Response(
        {
            "success": False,
            "message": str(errors),
            "data": None,
        },
        status=status.HTTP_400_BAD_REQUEST,
    )


class PermissionListPagination(PageNumberPagination):
    page_size = 10
    page_size_query_param = "page_size"
    max_page_size = 100


class PermissionListView(APIView):
    permission_classes = [
        IsAuthenticated,
        HasPermission,
    ]

    def get_permissions(self):
        if self.request.method in ("OPTIONS", "HEAD"):
            return [IsAuthenticated()]

        if self.request.method == "GET":
            self.required_permission = PERMISSIONS_VIEW
        elif self.request.method == "POST":
            self.required_permission = PERMISSIONS_CREATE
        elif self.request.method == "DELETE":
            self.required_permission = PERMISSIONS_DELETE

        return super().get_permissions()

    def http_method_not_allowed(self, request, *args, **kwargs):
        allowed = [m.upper() for m in self.allowed_methods if m.upper() not in ("OPTIONS", "HEAD")]
        return Response(
            {
                "success": False,
                "message": f"Method '{request.method}' not allowed on this endpoint. Supported methods: {', '.join(allowed)}.",
                "data": None,
            },
            status=status.HTTP_405_METHOD_NOT_ALLOWED,
            headers={"Allow": ", ".join(self.allowed_methods)},
        )

    @extend_schema(
        operation_id="permissions_list",
        tags=["Permissions"],
        parameters=[
            OpenApiParameter(
                name="search",
                type=OpenApiTypes.STR,
                location=OpenApiParameter.QUERY,
                required=False,
                description="Search by permission name, module, action, or description.",
            ),
            OpenApiParameter(
                name="module",
                type=OpenApiTypes.STR,
                location=OpenApiParameter.QUERY,
                required=False,
                description="Filter permissions by module.",
            ),
            OpenApiParameter(
                name="action",
                type=OpenApiTypes.STR,
                location=OpenApiParameter.QUERY,
                required=False,
                description="Filter permissions by action.",
            ),
            OpenApiParameter(
                name="ordering",
                type=OpenApiTypes.STR,
                location=OpenApiParameter.QUERY,
                required=False,
                description=(
                    "Sort results. Allowed values: "
                    "name, -name, module, -module, action, -action, "
                    "created_at, -created_at."
                ),
            ),
            OpenApiParameter(
                name="page",
                type=OpenApiTypes.INT,
                location=OpenApiParameter.QUERY,
                required=False,
                description="Page number.",
            ),
            OpenApiParameter(
                name="page_size",
                type=OpenApiTypes.INT,
                location=OpenApiParameter.QUERY,
                required=False,
                description="Number of results per page. Maximum 100.",
            ),
        ],
        responses={200: PermissionListResponseSerializer},
        summary="List permissions",
        description="Retrieve permissions with pagination, search, filtering, and ordering.",
    )
    def get(self, request):
        permissions = Permission.objects.all()

        # search
        search = request.query_params.get("search")

        if search:
            search = search.strip()

            if search:
                permissions = permissions.filter(
                    Q(name__icontains=search)
                    | Q(module__icontains=search)
                    | Q(action__icontains=search)
                    | Q(description__icontains=search)
                )

        # module filter
        module = request.query_params.get("module")

        if module:
            permissions = permissions.filter(
                module__iexact=module.strip()
            )

        # action filter
        action = request.query_params.get("action")

        if action:
            permissions = permissions.filter(
                action__iexact=action.strip()
            )

        # ordering: default ordering by name, with deterministic tie-breaker
        ordering = request.query_params.get(
            "ordering",
            "name",
        )

        allowed_ordering = {
            "name",
            "-name",
            "module",
            "-module",
            "action",
            "-action",
            "created_at",
            "-created_at",
        }

        if ordering not in allowed_ordering:
            return Response(
                {
                    "success": False,
                    "message": (
                        "Invalid ordering field. Allowed values are: "
                        "name, -name, module, -module, action, -action, "
                        "created_at, -created_at."
                    ),
                    "data": None,
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        permissions = permissions.order_by(ordering, "id")

        # pagination
        paginator = PermissionListPagination()

        page = paginator.paginate_queryset(
            permissions,
            request,
            view=self,
        )

        serializer = PermissionListSerializer(
            page,
            many=True,
        )

        data = {
            "count": paginator.page.paginator.count,
            "next": paginator.get_next_link(),
            "previous": paginator.get_previous_link(),
            "results": serializer.data,
        }

        return Response(
            {
                "success": True,
                "message": "Permissions retrieved successfully.",
                "data": data,
            },
            status=status.HTTP_200_OK,
        )

    @extend_schema(
        tags=["Permissions"],
        request=CreatePermissionSerializer,
        responses={
            201: PolymorphicProxySerializer(
                component_name="CreatePermissionResult",
                serializers=[
                    PermissionResponseSerializer,
                    PermissionBulkResponseSerializer,
                ],
                resource_type_field_name=None,
            )
        },
        summary="Create permission",
        description=(
            "Create one permission or multiple permissions in the same request. "
            "Send a single object or a list of objects."
        ),
    )
    def post(self, request):
        is_many = isinstance(request.data, list)

        serializer = CreatePermissionSerializer(
            data=request.data,
            many=is_many,
        )

        serializer.is_valid(
            raise_exception=True,
        )

        permissions = PermissionService.create_permission(
            validated_data=serializer.validated_data,
        )

        if is_many:
            data = PermissionListSerializer(
                permissions,
                many=True,
            ).data

            message = "Permissions created successfully."
        else:
            data = PermissionListSerializer(
                permissions
            ).data

            message = "Permission created successfully."

        return Response(
            {
                "success": True,
                "message": message,
                "data": data,
            },
            status=status.HTTP_201_CREATED,
        )

    @extend_schema(
        tags=["Permissions"],
        request=DeletePermissionSerializer,
        responses={200: DeletePermissionResponseSerializer},
        summary="Delete permissions",
        description=(
            "Delete one permission or multiple permissions. "
            "Send one UUID or a list of UUIDs in the ids field."
        ),
    )
    def delete(self, request):
        serializer = DeletePermissionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        try:
            deleted_ids = PermissionService.delete_permission(
                permission_ids=serializer.validated_data["ids"],
            )
        except ValidationError as exc:
            return role_permission_error_response(exc)

        return Response(
            {
                "success": True,
                "message": "Permissions deleted successfully.",
                "data": {
                    "deleted_ids": deleted_ids
                },
            },
            status=status.HTTP_200_OK,
        )


class PermissionDetailView(APIView):
    permission_classes = [
        IsAuthenticated,
        HasPermission,
    ]

    def get_permissions(self):
        if self.request.method in ("OPTIONS", "HEAD"):
            return [IsAuthenticated()]

        if self.request.method == "GET":
            self.required_permission = PERMISSIONS_VIEW
        elif self.request.method == "PATCH":
            self.required_permission = PERMISSIONS_UPDATE

        return super().get_permissions()

    def http_method_not_allowed(self, request, *args, **kwargs):
        if request.method == "DELETE":
            message = (
                "Method 'DELETE' is not allowed on permission detail endpoint. "
                "To delete permissions, please use DELETE /api/v1/permissions/ with the permission IDs in the request body."
            )
        else:
            allowed = [m.upper() for m in self.allowed_methods if m.upper() not in ("OPTIONS", "HEAD")]
            message = f"Method '{request.method}' not allowed on this endpoint. Supported methods: {', '.join(allowed)}."

        return Response(
            {
                "success": False,
                "message": message,
                "data": None,
            },
            status=status.HTTP_405_METHOD_NOT_ALLOWED,
            headers={"Allow": ", ".join(self.allowed_methods)},
        )

    @extend_schema(
        operation_id="permissions_retrieve",
        tags=["Permissions"],
        responses={200: PermissionDetailResponseSerializer},
        summary="Get permission details",
        description="Retrieve details of a specific permission.",
    )
    def get(self, request, id):
        permission = Permission.objects.filter(id=id).first()

        if not permission:
            return Response(
                {
                    "success": False,
                    "message": "Permission not found.",
                    "data": None,
                },
                status=status.HTTP_404_NOT_FOUND,
            )

        return Response(
            {
                "success": True,
                "message": "Permission retrieved successfully.",
                "data": PermissionListSerializer(permission).data,
            },
            status=status.HTTP_200_OK,
        )

    @extend_schema(
        tags=["Permissions"],
        request=UpdatePermissionSerializer,
        responses={200: PermissionDetailResponseSerializer},
        summary="Update permission",
        description="Update a specific permission.",
    )
    def patch(self, request, id):
        permission = Permission.objects.filter(
            id=id
        ).first()

        if not permission:
            return Response(
                {
                    "success": False,
                    "message": "Permission not found.",
                    "data": None,
                },
                status=status.HTTP_404_NOT_FOUND,
            )

        serializer = UpdatePermissionSerializer(
            instance=permission,
            data=request.data,
            partial=True,
        )

        serializer.is_valid(
            raise_exception=True,
        )

        permission = PermissionService.update_permission(
            permission=permission,
            validated_data=serializer.validated_data,
        )

        return Response(
            {
                "success": True,
                "message": "Permission updated successfully.",
                "data": PermissionListSerializer(permission).data,
            },
            status=status.HTTP_200_OK,
        )


class RolePermissionCreateView(APIView):
    permission_classes = [
        IsAuthenticated,
        HasPermission,
    ]

    required_permission = ROLES_UPDATE

    def get_permissions(self):
        if self.request.method in ("OPTIONS", "HEAD"):
            return [IsAuthenticated()]
        return super().get_permissions()

    def http_method_not_allowed(self, request, *args, **kwargs):
        return Response(
            {
                "success": False,
                "message": (
                    f"Method '{request.method}' not allowed on this endpoint. "
                    "Use POST to assign permissions to roles, or DELETE /api/v1/role-permissions/remove/ to remove permissions."
                ),
                "data": None,
            },
            status=status.HTTP_405_METHOD_NOT_ALLOWED,
            headers={"Allow": ", ".join(self.allowed_methods)},
        )

    @extend_schema(
        tags=["Role Permissions"],
        request=AssignPermissionToRoleSerializer,
        responses={201: AssignRolePermissionResponseSerializer},
        summary="Assign permissions to roles",
        description="Assign one or more permissions to one or more roles.",
    )
    def post(self, request):
        serializer = AssignPermissionToRoleSerializer(
            data=request.data
        )
        serializer.is_valid(raise_exception=True)

        try:
            result = RoleService.assign_permissions_to_roles(
                role_ids=serializer.validated_data["role_ids"],
                permission_ids=serializer.validated_data["permission_ids"],
            )
        except ValidationError as exc:
            return role_permission_error_response(exc)

        response_data = AssignRolePermissionResponseSerializer(
            {
                "success": True,
                "message": "Permissions assigned to roles successfully.",
                "data": result,
            }
        ).data

        return Response(
            response_data,
            status=status.HTTP_201_CREATED,
        )


class RolePermissionRemoveView(APIView):
    permission_classes = [
        IsAuthenticated,
        HasPermission,
    ]

    required_permission = ROLES_UPDATE

    def get_permissions(self):
        if self.request.method in ("OPTIONS", "HEAD"):
            return [IsAuthenticated()]
        return super().get_permissions()

    def http_method_not_allowed(self, request, *args, **kwargs):
        return Response(
            {
                "success": False,
                "message": (
                    f"Method '{request.method}' not allowed on this endpoint. "
                    "Use DELETE to remove permissions from roles, or POST /api/v1/role-permissions/assign/ to assign permissions."
                ),
                "data": None,
            },
            status=status.HTTP_405_METHOD_NOT_ALLOWED,
            headers={"Allow": ", ".join(self.allowed_methods)},
        )

    @extend_schema(
        tags=["Role Permissions"],
        request=RemovePermissionFromRoleSerializer,
        responses={200: RemoveRolePermissionResponseSerializer},
        summary="Remove permissions from roles",
        description="Remove one or more permissions from one or more roles.",
    )
    def delete(self, request):
        serializer = RemovePermissionFromRoleSerializer(
            data=request.data
        )
        serializer.is_valid(raise_exception=True)

        try:
            result = RoleService.remove_permissions_from_roles(
                role_ids=serializer.validated_data["role_ids"],
                permission_ids=serializer.validated_data["permission_ids"],
            )
        except ValidationError as exc:
            return role_permission_error_response(exc)

        removed_count = result["summary"]["removed"]

        response_data = RemoveRolePermissionResponseSerializer(
            {
                "success": True,
                "message": (
                    "Permissions removed successfully."
                    if removed_count > 0
                    else "No permissions were removed. The requested permissions were not assigned to the selected roles."
                ),
                "data": result,
            }
        ).data

        return Response(
            response_data,
            status=status.HTTP_200_OK,
        )


class RolePermissionPagination(PageNumberPagination):
    page_size = 10
    page_size_query_param = "page_size"
    max_page_size = 100


def build_role_permission_data(role):
    if role.code == "ADMIN":
        perms = list(Permission.objects.all().order_by("module", "action"))
    else:
        perms = [
            rp.permission
            for rp in role.role_permissions.all()
        ]
        perms.sort(key=lambda p: (p.module, p.action))

    return {
        "role": {
            "id": role.id,
            "name": role.name,
            "code": role.code,
            "description": role.description,
            "is_system_role": role.is_system_role,
            "is_assignable": role.is_assignable,
        },
        "permissions_count": len(perms),
        "permissions": [
            {
                "id": p.id,
                "name": p.name,
                "module": p.module,
                "action": p.action,
                "description": p.description,
            }
            for p in perms
        ],
    }


class RolePermissionListView(APIView):
    permission_classes = [
        IsAuthenticated,
        HasPermission,
    ]
    required_permission = ROLES_VIEW

    def get_permissions(self):
        if self.request.method in ("OPTIONS", "HEAD"):
            return [IsAuthenticated()]
        return super().get_permissions()

    def http_method_not_allowed(self, request, *args, **kwargs):
        return Response(
            {
                "success": False,
                "message": (
                    f"Method '{request.method}' not allowed on this endpoint. "
                    "Use POST /api/v1/role-permissions/assign/ to assign permissions, "
                    "or DELETE /api/v1/role-permissions/remove/ to remove permissions."
                ),
                "data": None,
            },
            status=status.HTTP_405_METHOD_NOT_ALLOWED,
            headers={"Allow": ", ".join(self.allowed_methods)},
        )

    @extend_schema(
        operation_id="role_permissions_list",
        tags=["Role Permissions"],
        parameters=[
            OpenApiParameter(
                name="role_id",
                type=OpenApiTypes.UUID,
                required=False,
                location=OpenApiParameter.QUERY,
                description="Optional. When supplied, returns detailed permissions for this specific role.",
            ),
            OpenApiParameter(
                name="search",
                type=OpenApiTypes.STR,
                required=False,
                location=OpenApiParameter.QUERY,
                description="Search roles by name, code, or description.",
            ),
            OpenApiParameter(
                name="page",
                type=OpenApiTypes.INT,
                required=False,
                location=OpenApiParameter.QUERY,
                description="Page number.",
            ),
            OpenApiParameter(
                name="page_size",
                type=OpenApiTypes.INT,
                required=False,
                location=OpenApiParameter.QUERY,
                description="Number of roles per page.",
            ),
        ],
        responses={
            200: PolymorphicProxySerializer(
                component_name="RolePermissionsResponse",
                serializers=[
                    RolePermissionListResponseSerializer,
                    RolePermissionDetailResponseSerializer,
                ],
                resource_type_field_name=None,
            )
        },
        summary="List or retrieve role permissions",
        description=(
            "Retrieve role permissions with dual behavior:\n"
            "- **Without `role_id` query param**: Returns paginated list of roles with their permission breakdowns.\n"
            "- **With `?role_id=<uuid>`**: Returns permission detail for the specified role."
        ),
    )
    def get(self, request):
        if "role_id" in request.query_params:
            raw_role_id = request.query_params.get("role_id", "").strip()
            try:
                role_id = uuid.UUID(raw_role_id)
            except (ValueError, TypeError, AttributeError):
                return Response(
                    {
                        "success": False,
                        "message": "Invalid role_id.",
                        "data": None,
                    },
                    status=status.HTTP_400_BAD_REQUEST,
                )

            role = Role.objects.filter(id=role_id).prefetch_related(
                "role_permissions__permission"
            ).first()

            if role is None:
                return Response(
                    {
                        "success": False,
                        "message": "Role not found.",
                        "data": None,
                    },
                    status=status.HTTP_404_NOT_FOUND,
                )

            return Response(
                {
                    "success": True,
                    "message": "Role permissions retrieved successfully.",
                    "data": build_role_permission_data(role),
                },
                status=status.HTTP_200_OK,
            )

        roles = Role.objects.all().prefetch_related(
            "role_permissions__permission"
        ).order_by("name", "id")

        search = request.query_params.get("search")
        if search:
            search = search.strip()
            if search:
                roles = roles.filter(
                    Q(name__icontains=search)
                    | Q(code__icontains=search)
                    | Q(description__icontains=search)
                )

        paginator = RolePermissionPagination()
        page = paginator.paginate_queryset(roles, request, view=self)

        results = [build_role_permission_data(r) for r in page]

        return Response(
            {
                "success": True,
                "message": "Role permissions retrieved successfully.",
                "data": {
                    "count": paginator.page.paginator.count,
                    "next": paginator.get_next_link(),
                    "previous": paginator.get_previous_link(),
                    "results": results,
                },
            },
            status=status.HTTP_200_OK,
        )
