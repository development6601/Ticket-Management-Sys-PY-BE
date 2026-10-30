from drf_spectacular.utils import extend_schema, OpenApiParameter, OpenApiTypes, OpenApiRequest, PolymorphicProxySerializer
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.api.serializers.roles import *
from apps.accounts.models import Role
from apps.accounts.permissions.rbac import *
from apps.accounts.services.roles import RoleService

from rest_framework.pagination import PageNumberPagination
from django.db.models import Q


class RoleListPagination(PageNumberPagination):
    page_size = 10
    page_size_query_param = "page_size"
    max_page_size = 100


class RoleListView(APIView):
    permission_classes = [
        IsAuthenticated,
        HasPermission,
    ]

    def get_permissions(self):
        if self.request.method in ("OPTIONS", "HEAD"):
            return [IsAuthenticated()]

        if self.request.method == "GET":
            self.required_permission = ROLES_VIEW
        elif self.request.method == "POST":
            self.required_permission = ROLES_CREATE
        elif self.request.method == "DELETE":
            self.required_permission = ROLES_DELETE

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
        operation_id="roles_list",
        tags=["Roles"],
        parameters=[
            OpenApiParameter(
                name="search",
                type=OpenApiTypes.STR,
                location=OpenApiParameter.QUERY,
                required=False,
                description="Search roles by name, code, or description.",
            ),
            OpenApiParameter(
                name="is_system_role",
                type=OpenApiTypes.BOOL,
                location=OpenApiParameter.QUERY,
                required=False,
                description="Filter by system role status.",
            ),
            OpenApiParameter(
                name="ordering",
                type=OpenApiTypes.STR,
                location=OpenApiParameter.QUERY,
                required=False,
                description="Sort by name,code, created_at, or updated_at. Prefix with '-' for descending order.",
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
                description="Number of roles per page. Maximum 100.",
            ),
        ],
        responses={200: RoleListResponseSerializer},
        summary="List roles",
        description="Retrieve roles with pagination, search, filtering, and ordering.",
    )
    def get(self, request):
        roles = Role.objects.all()

        # search
        search = request.query_params.get("search")

        if search:
            search = search.strip()

            if search:
                roles = roles.filter(
                    Q(name__icontains=search)
                    | Q(code__icontains=search)
                    | Q(description__icontains=search)
                )

        # system role filter
        is_system_role = request.query_params.get("is_system_role")

        if is_system_role is not None:
            if is_system_role.lower() not in ["true", "false"]:
                return Response(
                    {
                        "success": False,
                        "message": "is_system_role must be true or false.",
                        "data": None,
                    },
                    status=status.HTTP_400_BAD_REQUEST,
                )

            roles = roles.filter(
                is_system_role=is_system_role.lower() == "true"
            )

        # Ordering
        ordering = request.query_params.get(
            "ordering",
            "name",
        )

        allowed_ordering = {
            "name",
            "-name",
            "code",
            "-code",
            "created_at",
            "-created_at",
            "updated_at",
            "-updated_at",
        }

        if ordering not in allowed_ordering:
            return Response(
                {
                    "success": False,
                    "message": (
                        "Invalid ordering field. Allowed values are: "
                        "name, -name, code, -code, created_at, -created_at, updated_at, -updated_at."
                    ),
                    "data": None,
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        roles = roles.order_by(ordering)

        # pagination
        paginator = RoleListPagination()

        page = paginator.paginate_queryset(
            roles,
            request,
            view=self,
        )

        serializer = RoleListSerializer(
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
                "message": "Roles retrieved successfully.",
                "data": data,
            },
            status=status.HTTP_200_OK,
        )

    @extend_schema(
        tags=["Roles"],
        request=CreateRoleSerializer,
        responses={
            201: PolymorphicProxySerializer(
                component_name="CreateRoleResult",
                serializers=[
                    CreateRoleResponseSerializer,
                    CreateRoleBulkResponseSerializer,
                ],
                resource_type_field_name=None,
            )
        },
        summary="Create Role",
        description=(
            "Create one role or multiple roles in the same request."
            "Send a single object or a list of objects."
        ),
    )
    def post(self, request):
        is_many = isinstance(request.data, list)

        serializer = CreateRoleSerializer(
            data=request.data,
            many=is_many,
        )

        serializer.is_valid(raise_exception=True)

        roles = RoleService.create_roles(
            validated_data=serializer.validated_data,
        )

        if is_many:
            data = RoleListSerializer(
                roles,
                many=True,
            ).data
            message = "Roles created successfully."
        else:
            data = RoleListSerializer(
                roles,
            ).data
            message = "Role created successfully."

        return Response(
            {
                "success": True,
                "message": message,
                "data": data,
            },
            status=status.HTTP_201_CREATED,
        )

    @extend_schema(
        tags=["Roles"],
        request=DeleteRoleSerializer,
        responses={200: RoleDeleteResponseSerializer},
        summary="Delete roles",
        description=(
            "Delete one role or multiple roles. "
            "Send one UUID or a list of UUIDs in the ids field."
        ),
    )
    def delete(self, request):
        serializer = DeleteRoleSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        if serializer.validated_data.get("all"):
            deleted_ids = RoleService.delete_all_roles()
        else:
            deleted_ids = RoleService.delete_roles(
                role_ids=serializer.validated_data["ids"]
            )

        return Response(
            {
                "success": True,
                "message": "Roles deleted successfully.",
                "data": {
                    "deleted_ids": deleted_ids,
                },
            },
            status=status.HTTP_200_OK,
        )


class RoleDetailView(APIView):
    permission_classes = [
        IsAuthenticated,
        HasPermission,
    ]

    def get_permissions(self):
        if self.request.method in ("OPTIONS", "HEAD"):
            return [IsAuthenticated()]

        if self.request.method == "GET":
            self.required_permission = ROLES_VIEW
        elif self.request.method == "PATCH":
            self.required_permission = ROLES_UPDATE

        return super().get_permissions()

    def http_method_not_allowed(self, request, *args, **kwargs):
        if request.method == "DELETE":
            message = (
                "Method 'DELETE' is not allowed on role detail endpoint. "
                "To delete roles, please use DELETE /api/v1/roles/ with the role IDs in the request body."
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
        operation_id="roles_retrieve",
        tags=["Roles"],
        responses={200: RoleDetailResponseSerializer},
        summary="Get role details",
        description="Retrieve details of a specific role.",
    )
    def get(self, request, id):
        role = Role.objects.filter(id=id).first()

        if not role:
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
                "message": "Role retrieved successfully.",
                "data": RoleListSerializer(role).data,
            },
            status=status.HTTP_200_OK,
        )

    @extend_schema(
        tags=["Roles"],
        request=UpdateRoleSerializer,
        responses={200: UpdateRoleResponseSerializer},
        summary="Update Role",
        description="Update a specific role.",
    )
    def patch(self, request, id):
        role = Role.objects.filter(id=id).first()

        if not role:
            return Response(
                {
                    "success": False,
                    "message": "Role not found.",
                    "data": None,
                },
                status=status.HTTP_404_NOT_FOUND,
            )

        serializer = UpdateRoleSerializer(
            instance=role,
            data=request.data,
            partial=True,
        )

        serializer.is_valid(
            raise_exception=True,
        )

        role = RoleService.update_role(
            role=role,
            validated_data=serializer.validated_data,
        )

        return Response(
            {
                "success": True,
                "message": "Role Updated Successfully.",
                "data": RoleListSerializer(role).data,
            },
            status=status.HTTP_200_OK,
        )