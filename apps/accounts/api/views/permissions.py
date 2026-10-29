from drf_spectacular.utils import extend_schema, OpenApiParameter, OpenApiTypes, OpenApiRequest, PolymorphicProxySerializer
from rest_framework import status
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
        if self.request.method == "GET":
            self.required_permission = PERMISSIONS_VIEW
        elif self.request.method == "POST":
            self.required_permission = PERMISSIONS_CREATE
        elif self.request.method == "DELETE":
            self.required_permission = PERMISSIONS_DELETE

        return super().get_permissions()

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

        # ordering
        ordering = request.query_params.get(
            "ordering",
            "module",
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

        permissions = permissions.order_by(ordering)

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

        deleted_ids = PermissionService.delete_permission(
            permission_ids=serializer.validated_data["ids"],
        )

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
        if self.request.method == "GET":
            self.required_permission = PERMISSIONS_VIEW
        elif self.request.method == "PATCH":
            self.required_permission = PERMISSIONS_UPDATE

        return super().get_permissions()

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

        result = RoleService.assign_permissions_to_roles(
            role_ids=serializer.validated_data["role_ids"],
            permission_ids=serializer.validated_data["permission_ids"],
        )

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

        result = RoleService.remove_permissions_from_roles(
            role_ids=serializer.validated_data["role_ids"],
            permission_ids=serializer.validated_data["permission_ids"],
        )

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