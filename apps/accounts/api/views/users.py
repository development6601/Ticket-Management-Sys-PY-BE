from uuid import UUID

from drf_spectacular.utils import extend_schema, OpenApiParameter, OpenApiTypes, OpenApiExample, PolymorphicProxySerializer
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework.pagination import PageNumberPagination
from rest_framework.exceptions import (
    ValidationError,
    PermissionDenied,
    NotFound,
)

from apps.accounts.api.serializers.auth import RegisterDataSerializer, RegisterResponseSerializer
from apps.accounts.api.serializers.users import *
from apps.accounts.services.users import UserService
from apps.accounts.models import User, UserRole
from apps.accounts.permissions.rbac import *
from apps.accounts.permissions.constants import *
from django.conf import settings
from django.db.models import Q

from apps.customers.models import Customer, CustomerUser

class MeView(APIView):
    permission_classes = [IsAuthenticated]

    @staticmethod
    def _validate_customer_company(user):
        customer_user = (
            CustomerUser.objects
            .select_related("customer")
            .filter(user=user)
            .first()
        )
        # Internal users do not belong to customer companies.
        if customer_user is None:
            return
        customer = customer_user.customer
        if (
            not customer.is_active
            or customer.deleted_at is not None
            or customer.account_status != Customer.AccountStatus.ACTIVE
        ):
            raise PermissionDenied(
                "Your customer company is inactive."
            )

    @extend_schema(
        tags=["Users"],
        responses={200: MeResponseSerializer},
        summary="Get current user",
        description="Return the currently authenticated user's profile.",
    )
    def get(self, request):
        self._validate_customer_company(request.user)
        return Response(
            {
                "success": True,
                "message": "Current user retrieved successfully.",
                "data": UserResponseSerializer(request.user).data,
            },
            status=status.HTTP_200_OK,
        )

    @extend_schema(
        tags=["Users"],
        request=UpdateProfileSerializer,
        responses={200: MeResponseSerializer},
        summary="Update current user profile",
        description="Update the currently authenticated user's profile.",
    )
    def patch(self, request):
        self._validate_customer_company(request.user)
        serializer = UpdateProfileSerializer(
            instance=request.user,
            data=request.data,
            partial=True,
        )
        serializer.is_valid(raise_exception=True)

        user = UserService.update_profile(
            user=request.user,
            validated_data=serializer.validated_data,
        )

        return Response(
            {
                "success": True,
                "message": "Profile updated successfully.",
                "data": UserResponseSerializer(user).data,
            },
            status=status.HTTP_200_OK,
        )


class UserListPagination(PageNumberPagination):
    page_size = 10
    page_size_query_param = "page_size"
    max_page_size = 100


class UserListView(APIView):
    permission_classes = [
        IsAuthenticated,
        HasPermission,
    ]

    def get_permissions(self):
        if self.request.method == "GET":
            self.permission_classes = [
                IsAuthenticated,
                CanViewUsers,
            ]
            self.required_permission = USERS_VIEW

        elif self.request.method == "POST":
            self.permission_classes = [
                IsAuthenticated,
                CanCreateUser,
            ]

        return super().get_permissions()
    

    @extend_schema(
        operation_id="users_list",
        tags=["Users"],
        parameters=[
            OpenApiParameter(
                name="search",
                type=OpenApiTypes.STR,
                location=OpenApiParameter.QUERY,
                required=False,
                description="Search users by email, first name, or last name.",
            ),
            OpenApiParameter(
                name="is_active",
                type=OpenApiTypes.BOOL,
                location=OpenApiParameter.QUERY,
                required=False,
                description="Filter users by active status.",
            ),
            OpenApiParameter(
                name="customer_id",
                type=OpenApiTypes.UUID,
                location=OpenApiParameter.QUERY,
                required=False,
                description=(
                    "Filter users belonging to a specific customer company. "
                    "Internal users with users.view can filter by an accessible "
                    "company. Customer users can only specify their own company. "
                    "The existing users.view or primary-contact permission "
                    "is still required."
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
        responses={200: UserListResponseSerializer},
        summary="List users",
        description=(
            "Retrieve users with pagination, search, active status, "
            "and optional customer_id filtering. Customer users are "
            "restricted to their own company."
        ),
    )
    def get(self, request):
        # 1. Read and validate optional customer_id.
        raw_customer_id = request.query_params.get("customer_id")
        customer_id = None

        if raw_customer_id is not None:
            try:
                customer_id = UUID(raw_customer_id)
            except (ValueError, AttributeError, TypeError):
                raise ValidationError({
                    "customer_id": [
                        "Enter a valid customer UUID."
                    ]
                })

        # 2. Identify the requesting user's company membership.
        customer_user = (
            CustomerUser.objects
            .select_related("customer")
            .filter(user=request.user)
            .first()
        )

        is_admin = (
            request.user.is_superuser
            or UserRole.objects.filter(
                user=request.user,
                role__code="ADMIN",
            ).exists()
        )

        # 3. Build the existing user queryset.
        queryset = (
            User.objects.all()
            .select_related("user_role__role")
            .prefetch_related("customer_members__customer")
        )

        # 4. Enforce company isolation and optional customer_id filter.
        if customer_user:
            if (
                customer_id is not None
                and customer_id != customer_user.customer_id
            ):
                raise PermissionDenied(
                    "You can only view users from your own company."
                )

            queryset = queryset.filter(
                customer_members__customer_id=customer_user.customer_id
            )

        elif customer_id is not None:
            customer = (
                Customer.objects
                .filter(id=customer_id)
                .first()
            )

            if customer is None:
                raise NotFound("Customer company not found.")

            queryset = queryset.filter(
                customer_members__customer_id=customer_id
            )

        # 5. Apply existing search and active status filters.
        search = request.query_params.get("search")
        is_active = request.query_params.get("is_active")

        if search:
            queryset = queryset.filter(
                Q(email__icontains=search)
                | Q(first_name__icontains=search)
                | Q(last_name__icontains=search)
            )

        if is_active is not None:
            queryset = queryset.filter(
                is_active=is_active.lower() == "true"
            )

        queryset = queryset.distinct().order_by("-created_at", "-id")

        paginator = UserListPagination()

        page = paginator.paginate_queryset(
            queryset,
            request,
            view=self,
        )

        serializer = UserListSerializer(
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
                "message": "Users retrieved successfully.",
                "data": data,
            },
            status=status.HTTP_200_OK,
        )

        
    @extend_schema(
        tags=["Users"],
        request=CreateUserRequestSchemaSerializer,
        responses={
            201: PolymorphicProxySerializer(
                component_name="CreateUserResult",
                serializers=[
                    CreateInternalUserResponseSerializer,
                    CreateExistingCompanyCustomerUserResponseSerializer,
                    CreateCustomerCompanyResponseSerializer,
                ],
                resource_type_field_name=None,
            )
        },
        summary="Create user",
        description=(
            "Create an internal user, invite a customer user to an existing "
            "company, or create a new customer company with its primary user. "
            "The allowed workflow depends on the authenticated user's permissions. "
            "Internal users require a password. Customer users must not be "
            "given a password by the creator; they complete password setup "
            "through their invitation email. "
            "Provide exactly one of customer_id or customer when an "
            "authorized internal user creates a customer user."
        ),
        examples=[
            OpenApiExample(
                name="1. Create Internal User",
                summary="Create an internal employee",
                description=(
                    "For authorized administrators or internal users. "
                    "A password is required. Do not provide customer_id "
                    "or customer."
                ),
                value={
                    "email": "internaluser@example.com",
                    "first_name": "string",
                    "last_name": "string",
                    "role_code": "SUPPORT_ROLE",
                    "password": "string",
                    "password_confirm": "string",
                },
                request_only=True,
            ),

            OpenApiExample(
                name="2. Add User to Existing Company",
                summary="Invite a customer user to an existing company",
                description=(
                    "Provide customer_id for an existing company. "
                    "Do not provide a customer object or password. "
                    "Customer creators are restricted to their own company."
                ),
                value={
                    "email": "customeruser@example.com",
                    "first_name": "string",
                    "last_name": "string",
                    "role_code": "CUSTOMER",
                    "customer_id": "UUID",
                },
                request_only=True,
            ),

            OpenApiExample(
                name="3. Create Customer Company",
                summary="Create a company with its primary contact",
                description=(
                    "Creates a new customer company and its primary user. "
                    "The customer field represents the company name. "
                    "The contact_person field represents the primary user's full name. "
                    "The CUSTOMER role is assigned automatically. "
                    "Do not provide password fields."
                ),
                value={
                    "email": "user@example.com",
                    "customer": "string",
                    "contact_person": "string",
                    "phone": "string",
                    "address": "string",
                    "state": "string",
                    "country": "string",
                },
                request_only=True,
            ),
        ],
    )

    def post(self, request):
        serializer = CreateUserSerializer(
            data=request.data,
            context={"request": request},
        )

        serializer.is_valid(raise_exception=True)

        is_new_company = (
            serializer.validated_data.get("customer") is not None
        )
        
        user = UserService.create_user(
            performed_by=request.user,
            validated_data=serializer.validated_data,
        )

        setup_token = getattr(user, "_password_setup_token", None)

        user = (
            User.objects
            .select_related("user_role__role").prefetch_related("customer_members__customer")
            .get(id=user.id)
        )

        if is_new_company:
            membership = (
                CustomerUser.objects
                .select_related("customer")
                .get(user=user, is_primary_contact=True)
            )

            response_data = dict(RegisterDataSerializer({
                "customer": membership.customer,
                "primary_user": user,
            }).data)

            if getattr(settings, "EXPOSE_TEST_TOKENS", False) and setup_token:
                response_data["_test_token"] = setup_token

            return Response(
                {
                    "success": True,
                    "message": (
                        "Customer created successfully. "
                        "Password setup link sent to your email."
                    ),
                    "data": response_data,
                },
                status=status.HTTP_201_CREATED,
            )

        resp_data = dict(UserListSerializer(user).data)
        if getattr(settings, "EXPOSE_TEST_TOKENS", False) and setup_token:
            resp_data["_test_token"] = setup_token

        return Response(
            {
                "success": True,
                "message": "User created successfully.",
                "data": resp_data,
            },
            status=status.HTTP_201_CREATED,
        )


# user detail view
class UserDetailView(APIView):
    permission_classes = [
        IsAuthenticated,
        HasPermission,
    ]

    def get_permissions(self):
        if self.request.method == "GET":
            self.permission_classes = [
                IsAuthenticated,
                CanViewUsers,
            ]
            self.required_permission = USERS_VIEW
        elif self.request.method == "PATCH":
            self.permission_classes = [
                IsAuthenticated,
                HasPermission,
            ]
            self.required_permission = USERS_UPDATE

        return super().get_permissions()

    @extend_schema(
        tags=["Users"],
        responses={200: UserDetailResponseSerializer},
        summary="Get user details",
        description="Retrieve details of a specific user including their assigned roles and last login.",
    )
    def get(self, request, id):
        from rest_framework.exceptions import PermissionDenied
        
        user = (
            User.objects
            .select_related("user_role__role").prefetch_related("customer_members__customer")
            .filter(id=id)
            .first()
        )

        if not user:
            return Response(
                {
                    "success": False,
                    "message": "User not found.",
                    "data": None,
                },
                status=status.HTTP_404_NOT_FOUND,
            )

        is_admin = (
            request.user.is_superuser
            or UserRole.objects.filter(
                user=request.user,
                role__code="ADMIN",
            ).exists()
        )
        customer_user = CustomerUser.objects.filter(user=request.user).first()

        if customer_user and not is_admin:
            if not CustomerUser.objects.filter(user=user, customer=customer_user.customer).exists():
                raise PermissionDenied("You do not have permission to access this user.")

        return Response(
            {
                "success": True,
                "message": "User retrieved successfully.",
                "data": UserDetailSerializer(user).data,
            },
            status=status.HTTP_200_OK,
        )

    @extend_schema(
        tags=["Users"],
        request=UpdateUserSerializer,
        responses={200: UserDetailResponseSerializer},
        summary="Update user",
        description="Update a specific user's information.",
    )
    def patch(self, request, id):
        from rest_framework.exceptions import PermissionDenied
        
        user = (
            User.objects
            .select_related("user_role__role").prefetch_related("customer_members__customer")
            .filter(id=id)
            .first()
        )

        if not user:
            return Response(
                {
                    "success": False,
                    "message": "User not found.",
                    "data": None,
                },
                status=status.HTTP_404_NOT_FOUND,
            )

        is_admin = (
            request.user.is_superuser
            or UserRole.objects.filter(
                user=request.user,
                role__code="ADMIN",
            ).exists()
        )
        customer_user = CustomerUser.objects.filter(user=request.user).first()

        if customer_user and not is_admin:
            if not CustomerUser.objects.filter(user=user, customer=customer_user.customer).exists():
                raise PermissionDenied("You do not have permission to modify this user.")

        serializer = UpdateUserSerializer(
            instance=user,
            data=request.data,
            partial=True,
        )
        serializer.is_valid(raise_exception=True)

        user = UserService.update_user(
            user=user,
            validated_data=serializer.validated_data,
        )

        # Re-fetch with prefetch so roles are populated on the response object.
        user = (
            User.objects
            .select_related("user_role__role").prefetch_related("customer_members__customer")
            .get(id=user.id)
        )

        return Response(
            {
                "success": True,
                "message": "User updated successfully.",
                "data": UserDetailSerializer(user).data,
            },
            status=status.HTTP_200_OK,
        )


class UserStatusView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(
        tags=["Users"],
        request=UpdateUserStatusSerializer,
        responses={
            200: UserStatusResponseSerializer,
            202: UserStatusResponseSerializer,
        },
        summary="Activate or deactivate user",
        description="Activate or deactivate a user account.",
    )
    def patch(self, request, id):
        user = User.objects.filter(id=id).first()

        if not user:
            return Response(
                {
                    "success": False,
                    "message": "User not found.",
                    "data": None,
                },
                status=status.HTTP_404_NOT_FOUND,
            )

        
        serializer = UpdateUserStatusSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        result = UserService.set_user_active_status(
            user=user,
            is_active=serializer.validated_data["is_active"],
            performed_by=request.user,
        )

        updated_user = result["user"]
        activation_pending = result["activation_pending"]

        if activation_pending:
            message = (
                "An activation email has been sent to the customer user. "
                "The account will remain deactivated until email confirmation."
            )
        elif updated_user.is_active:
            message = "User activated successfully."
        else:
            message = "User deactivated successfully."

        resp_data = {
            **UserListSerializer(updated_user).data,
            "activation_pending": activation_pending,
        }
        if getattr(settings, "EXPOSE_TEST_TOKENS", False) and result.get("raw_token"):
            resp_data["_test_token"] = result["raw_token"]
            resp_data["_test_verify_url"] = f"/api/v1/auth/verify-email/?token={result['raw_token']}"

        return Response(
            {
                "success": True,
                "message": message,
                "data": resp_data,
            },
            status=(
                status.HTTP_202_ACCEPTED
                if activation_pending
                else status.HTTP_200_OK
            ),
        )

class UserRoleView(APIView):
    permission_classes = [
        IsAuthenticated,
        HasPermission,
    ]

    def get_permissions(self):
        if self.request.method in ("OPTIONS", "HEAD"):
            return [IsAuthenticated()]

        if self.request.method == "PATCH":
            self.required_permission = ROLES_ASSIGN

        return super().get_permissions()

    def http_method_not_allowed(self, request, *args, **kwargs):
        if request.method == "GET":
            message = (
                "Method 'GET' is not allowed on this endpoint. "
                "Use PATCH to update a user's role, or GET /api/v1/users/<id>/ to view user details."
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
        tags=["Users"],
        request=UpdateUserRoleSerializer,
        responses={200: UserDetailResponseSerializer},
        summary="Update user role",
        description="Change the role assigned to a user. A user can have only one role.",
    )

    def patch(self, request, id):
        from rest_framework.exceptions import PermissionDenied
        
        user = (
            User.objects.filter(id=id).first()
        )

        if not user:
            return Response(
                {
                    "success": False,
                    "message": "User not found.",
                    "data": None,
                },
                status=status.HTTP_404_NOT_FOUND,
            )

        is_admin = (
            request.user.is_superuser
            or UserRole.objects.filter(
                user=request.user,
                role__code="ADMIN",
            ).exists()
        )

        customer_user = CustomerUser.objects.filter(
            user=request.user
        ).first()

        if customer_user and not is_admin:

            # Customer-side users cannot change roles.
            raise PermissionDenied(
                "Customer users cannot change user roles."
            )
        
        serializer = UpdateUserRoleSerializer(
            data=request.data,
            context={"request": request},
        )
        serializer.is_valid(raise_exception=True)

        UserService.update_user_role(
            user=user,
            role_id=serializer.validated_data["role_id"],
            performed_by=request.user,
        )

        user = (
            User.objects
            .select_related("user_role__role").prefetch_related("customer_members__customer")
            .get(id=user.id)
        )

        return Response(
            {
                "success": True,
                "message": "User role updated successfully.",
                "data": UserDetailSerializer(user).data,
            },
            status=status.HTTP_200_OK,
        )