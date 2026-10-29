import uuid

from collections.abc import Mapping

from django.db import transaction
from django.utils import timezone
from django.db.models import Q
from drf_spectacular.utils import OpenApiParameter, OpenApiTypes, extend_schema

from rest_framework import status
from rest_framework.pagination import PageNumberPagination
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework.exceptions import (
    PermissionDenied,
    ValidationError,
    NotFound,
)

from apps.accounts.models import EmailVerificationRequest, User, UserRole, RolePermission, PasswordResetRequest
from apps.accounts.permissions.rbac import HasPermission
from apps.customers.api.serializers.customers import *
from apps.customers.models import Customer, CustomerUser
from apps.customers.permissions import *
from apps.customers.services.customers import *
from apps.customers.services.customer_updates import (
    CustomerUpdateService,
)

class CustomerListPagination(PageNumberPagination):
    page_size = 10
    page_size_query_param = "page_size"
    max_page_size = 100


class CustomerListView(APIView):
    permission_classes = [
        IsAuthenticated,
        HasPermission,
    ]

    def get_permissions(self):
        self.required_permission = CUSTOMERS_VIEW
        return super().get_permissions()

    @extend_schema(
        operation_id="customers_list",
        tags=["Customers"],
        parameters=[
            OpenApiParameter(
                name="search",
                type=OpenApiTypes.STR,
                location=OpenApiParameter.QUERY,
                required=False,
                description=(
                    "Search customers by company name, contact person, "
                    "email, phone, address, state, or country."
                ),
            ),
            OpenApiParameter(
                name="is_active",
                type=OpenApiTypes.BOOL,
                location=OpenApiParameter.QUERY,
                required=False,
                description="Filter customers by active status.",
            ),
            OpenApiParameter(
                name="account_status",
                type=OpenApiTypes.STR,
                location=OpenApiParameter.QUERY,
                required=False,
                description=(
                    "Filter by customer account status. "
                    "Accepted values are defined by Customer.AccountStatus."
                ),
            ),
            OpenApiParameter(
                name="state",
                type=OpenApiTypes.STR,
                location=OpenApiParameter.QUERY,
                required=False,
                description="Filter customers by state.",
            ),
            OpenApiParameter(
                name="country",
                type=OpenApiTypes.STR,
                location=OpenApiParameter.QUERY,
                required=False,
                description="Filter customers by country.",
            ),
            OpenApiParameter(
                name="account_manager",
                type=OpenApiTypes.UUID,
                location=OpenApiParameter.QUERY,
                required=False,
                description="Filter customers by account manager ID.",
            ),
            OpenApiParameter(
                name="ordering",
                type=OpenApiTypes.STR,
                location=OpenApiParameter.QUERY,
                required=False,
                description=(
                    "Allowed values: company_name, -company_name, "
                    "contact_person, -contact_person, created_at, "
                    "-created_at, updated_at, -updated_at."
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
        responses={200: CustomerListResponseSerializer},
        summary="List customers",
        description=(
            "Retrieve customers with pagination, search, filtering, "
            "and ordering."
        ),
    )
    def get(self, request):
        from rest_framework.exceptions import PermissionDenied
        from apps.customers.models import CustomerUser

        is_admin = (
            request.user.is_superuser
            or UserRole.objects.filter(
                user=request.user,
                role__code="ADMIN",
            ).exists()
        )
        customer_user = CustomerUser.objects.filter(user=request.user).first()
        
        if customer_user and not is_admin:
            raise PermissionDenied("Customer users are not allowed to view the customer list.")

        customers = Customer.objects.select_related(
            "account_manager"
        ).all()

        search = request.query_params.get("search")

        if search:
            search = search.strip()

            if search:
                customers = customers.filter(
                    Q(company_name__icontains=search)
                    | Q(contact_person__icontains=search)
                    | Q(email__icontains=search)
                    | Q(phone__icontains=search)
                    | Q(address__icontains=search)
                    | Q(state__icontains=search)
                    | Q(country__icontains=search)
                )

        is_active = request.query_params.get("is_active")

        if is_active is not None:
            if is_active.lower() not in ["true", "false"]:
                return Response(
                    {
                        "success": False,
                        "message": "is_active must be true or false.",
                        "data": None,
                    },
                    status=status.HTTP_400_BAD_REQUEST,
                )

            customers = customers.filter(
                is_active=is_active.lower() == "true"
            )

        account_status = request.query_params.get("account_status")
        if account_status is not None:
            account_status = account_status.strip().upper()
            valid_statuses = {
                choice.value
                for choice in Customer.AccountStatus
            }
            if account_status not in valid_statuses:
                return Response(
                    {
                        "success": False,
                        "message": (
                            "Invalid account_status. Allowed values: "
                            + ", ".join(sorted(valid_statuses))
                        ),
                        "data": None,
                    },
                    status=status.HTTP_400_BAD_REQUEST,
                )
            customers = customers.filter(
                account_status=account_status
            )

        state = request.query_params.get("state")

        if state:
            customers = customers.filter(
                state__iexact=state.strip()
            )

        # Country filter
        country = request.query_params.get("country")

        if country:
            customers = customers.filter(
                country__iexact=country.strip()
            )

        # Account manager filter
        account_manager = request.query_params.get("account_manager")

        if account_manager:
            try:
                account_manager = uuid.UUID(account_manager)
            except (ValueError, AttributeError, TypeError):
                return Response(
                    {
                        "success": False,
                        "message": "Invalid account manager ID.",
                        "data": None,
                    },
                    status=status.HTTP_400_BAD_REQUEST,
                )

            customers = customers.filter(
                account_manager_id=account_manager
            )

        # Ordering
        ordering = request.query_params.get(
            "ordering",
            "company_name",
        )

        allowed_ordering = {
            "company_name",
            "-company_name",
            "contact_person",
            "-contact_person",
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
                        "company_name, -company_name, "
                        "contact_person, -contact_person, "
                        "created_at, -created_at, "
                        "updated_at, -updated_at."
                    ),
                    "data": None,
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        customers = customers.order_by(ordering, "id")

        # pagination
        paginator = CustomerListPagination()

        try:
            page = paginator.paginate_queryset(
                customers,
                request,
                view=self,
            )
        except NotFound as exc:
            return Response(
                {
                    "success": False,
                    "message": str(exc.detail),
                    "data": None,
                },
                status=status.HTTP_404_NOT_FOUND,
            )

        serializer = CustomerListSerializer(
            page,
            many=True,
            context={"request": request}
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
                "message": "Customers retrieved successfully.",
                "data": data,
            },
            status=status.HTTP_200_OK,
        )


class CustomerDetailView(APIView):
    """Handles GET (retrieve) and PATCH (update) for a single customer."""

    permission_classes = [
        IsAuthenticated,
        HasPermission,
    ]

    def get_permissions(self):
        if self.request.method == "GET":
            self.required_permission = CUSTOMERS_VIEW
        elif self.request.method == "PATCH":
            self.required_permission = CUSTOMERS_UPDATE

        return super().get_permissions()
    

    @extend_schema(
        operation_id="customers_retrieve",
        tags=["Customers"],
        responses={200: CustomerDetailResponseSerializer},
        summary="Get customer details",
        description="Retrieve details of a specific customer.",
    )
    def get(self, request, id):
        from rest_framework.exceptions import PermissionDenied
        from apps.customers.models import CustomerUser

        is_admin = (
            request.user.is_superuser
            or UserRole.objects.filter(
                user=request.user,
                role__code="ADMIN",
            ).exists()
        )
        customer_user = CustomerUser.objects.filter(user=request.user).first()
        
        if customer_user and not is_admin:
            if customer_user.customer_id != id:
                raise PermissionDenied("You do not have permission to access this company's details.")

        try:
            customer = Customer.objects.select_related(
                "account_manager"
            ).get(id=id)
        except Customer.DoesNotExist:
            return Response(
                {
                    "success": False,
                    "message": "Customer not found.",
                    "data": None,
                },
                status=status.HTTP_404_NOT_FOUND,
            )

        return Response(
            {
                "success": True,
                "message": "Customer retrieved successfully.",
                "data": CustomerListSerializer(customer, context={"request": request}).data,
            },
            status=status.HTTP_200_OK,
        )

    @extend_schema(
        operation_id="customers_update",
        tags=["Customers"],
        request=CustomerUpdateSerializer,
        responses={
            200: CustomerDetailResponseSerializer,
            202: CustomerEmailChangePendingResponseSerializer,
        },
        summary="Update customer company",
        description=(
            "Update customer company details. Internal administrators "
            "and authorized internal users can update permitted fields. "
            "The company's current primary contact can update their own "
            "company when the CUSTOMER role has customers.update permission. "
            "Other customer users cannot update company details. "
            "Only internal administrators can modify account_manager "
            "and notes. Primary-contact transfer is permitted for "
            "internal administrators and the company's current primary "
            "contact, subject to validation and authorization. "
            "Email changes require separate verification."
        ),
    )
    def patch(self, request, id):
        from rest_framework.exceptions import PermissionDenied
        from apps.customers.models import CustomerUser

        is_admin = (
            request.user.is_superuser
            or UserRole.objects.filter(
                user=request.user,
                role__code="ADMIN",
            ).exists()
        )
        customer_user = CustomerUser.objects.filter(user=request.user).first()
        
        if customer_user and not is_admin:
            if customer_user.customer_id != id:
                raise PermissionDenied("You do not have permission to modify this company's details.")

        try:
            customer = Customer.objects.select_related(
                "account_manager"
            ).get(id=id)
        except Customer.DoesNotExist:
            return Response(
                {
                    "success": False,
                    "message": "Customer not found.",
                    "data": None,
                },
                status=status.HTTP_404_NOT_FOUND,
            )

        serializer = CustomerUpdateSerializer(
            customer,
            data=request.data,
            partial=True,
            context={"request": request}
        )

        if not serializer.is_valid():
            return Response(
                {
                    "success": False,
                    "message": "Customer update failed.",
                    "data": serializer.errors,
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            if "email" in serializer.validated_data:
                CustomerUpdateService.request_email_change(
                    customer_id=customer.pk,
                    new_email=serializer.validated_data["email"],
                    performed_by=request.user,
                )

                return Response(
                    {
                        "success": True,
                        "message": (
                            "A verification link has been sent "
                            "to the new email address."
                        ),
                        "data": {
                            "email_change_pending": True,
                        },
                    },
                    status=status.HTTP_202_ACCEPTED,
                )

            customer = CustomerUpdateService.update_customer(
                customer_id=customer.pk,
                validated_data=serializer.validated_data,
                performed_by=request.user,
            )
        except ValidationError as exc:
            return Response(
                {
                    "success": False,
                    "message": "Customer update failed.",
                    "data": exc.detail,
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        return Response(
            {
                "success": True,
                "message": "Customer updated successfully.",
                "data": CustomerListSerializer(customer, context={"request": request}).data,
            },
            status=status.HTTP_200_OK,
        )


class CustomerStatusView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(
        tags=["Customers"],
        request=CustomerStatusUpdateSerializer,
        responses={
            200: CustomerStatusResponseSerializer,
            202: CustomerStatusResponseSerializer,
        },
        summary="Update customer status",
        description="Activate or deactivate a customer company.",
    )
    
    def patch(self, request, id):
        if not isinstance(request.data, Mapping):
            raise ValidationError({
                "non_field_errors": [
                    "Invalid data. Expected a JSON object."
                ]
            })

        if set(request.data.keys()) != {"is_active"}:
            raise ValidationError({
                "is_active": [
                    "Only the is_active field is allowed and required."
                ]
            })

        if type(request.data["is_active"]) is not bool:
            raise ValidationError({
                "is_active": [
                    "This field must be a JSON boolean (true or false)."
                ]
            })

        is_active = request.data["is_active"]


        try:
            result = CustomerStatusService.set_status(
                customer_id=id,
                is_active=is_active,
                performed_by=request.user,
            )
        except ValidationError as exc:
            return Response(
                {
                    "success": False,
                    "message": "Customer status update failed.",
                    "data": exc.detail,
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        customer = result["customer"]
        activation_pending = result["activation_pending"]

        if activation_pending:
            message = (
                "A company activation email has been sent to "
                "the primary contact. The company will remain "
                "deactivated until confirmation."
            )
        elif customer.is_active:
            message = "Company activated successfully."
        else:
            message = "Company deactivated successfully."

        return Response(
            {
                "success": True,
                "message": message,
                "data": {
                    "id": str(customer.id),
                    "is_active": customer.is_active,
                    "account_status": customer.account_status,
                    "activation_pending": activation_pending,
                },
            },
            status=(
                status.HTTP_202_ACCEPTED
                if activation_pending
                else status.HTTP_200_OK
            ),
        )
