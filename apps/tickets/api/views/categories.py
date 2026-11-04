import ipaddress
import re
from collections.abc import Mapping

from django.db.models import Q
from drf_spectacular.utils import OpenApiParameter, OpenApiTypes, extend_schema

from rest_framework import status
from rest_framework.exceptions import (
    NotFound,
    PermissionDenied,
    ValidationError,
)
from rest_framework.pagination import PageNumberPagination
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.customers.models import CustomerUser
from apps.tickets.api.serializers.categories import (
    TicketCategoryDeleteResponseSerializer,
    TicketCategoryErrorResponseSerializer,
    TicketCategoryListResponseSerializer,
    TicketCategoryResponseSerializer,
    TicketCategorySerializer,
)
from apps.tickets.models import TicketCategory
from apps.tickets.permissions import (
    TicketCategoryPermission,
    resolve_ticket_category_customer,
    user_can_manage_ticket_categories,
)
from apps.tickets.services.categories import TicketCategoryService


class TicketCategoryPagination(PageNumberPagination):
    page_size = 20
    page_size_query_param = "page_size"
    max_page_size = 100


ALLOWED_CATEGORY_ORDERING = {
    "position",
    "-position",
    "name",
    "-name",
    "created_at",
    "-created_at",
    "updated_at",
    "-updated_at",
}


def get_visible_categories(customer, *, can_manage=False):
    categories = TicketCategory.objects.filter(
        customer=customer,
    )

    if not can_manage:
        categories = categories.filter(is_active=True)

    return categories


def get_request_audit_meta(request):
    forwarded_for = request.META.get("HTTP_X_FORWARDED_FOR")
    if forwarded_for:
        raw_ip = forwarded_for.split(",")[0].strip()
    else:
        raw_ip = (request.META.get("REMOTE_ADDR") or "").strip()

    if raw_ip:
        try:
            ipaddress.ip_address(raw_ip)
            ip_address = raw_ip
        except ValueError:
            ip_address = None
    else:
        ip_address = None

    user_agent = request.META.get("HTTP_USER_AGENT", "")
    return ip_address, user_agent


def format_category_exception_response(exc, response):
    if response is None:
        return response

    if isinstance(response.data, dict) and "success" in response.data:
        return response

    if isinstance(exc, ValidationError):
        return Response(
            {
                "success": False,
                "message": "Validation failed.",
                "data": response.data,
            },
            status=response.status_code,
            headers=response.headers,
        )

    if isinstance(response.data, dict) and "detail" in response.data:
        detail = str(response.data["detail"])
    elif isinstance(response.data, str):
        detail = response.data
    else:
        detail = str(exc)

    return Response(
        {
            "success": False,
            "message": detail,
            "data": None,
        },
        status=response.status_code,
        headers=response.headers,
    )


class BaseTicketCategoryView(APIView):
    permission_classes = [TicketCategoryPermission]

    def handle_exception(self, exc):
        response = super().handle_exception(exc)
        return format_category_exception_response(exc, response)

    def get_customer(self, request):
        return resolve_ticket_category_customer(request, self)

    def can_manage(self, request, customer=None):
        cached = getattr(request, "_can_manage_ticket_categories", None)
        if cached is not None:
            return cached
        if customer is None:
            customer = self.get_customer(request)
        return user_can_manage_ticket_categories(request.user, customer)

    def get_visible_categories(self, request, customer=None):
        if customer is None:
            customer = self.get_customer(request)
        return get_visible_categories(
            customer,
            can_manage=self.can_manage(request, customer),
        )

    @staticmethod
    def _extract_payload(request_data):
        if not isinstance(request_data, Mapping):
            return request_data
        payload = dict(request_data)
        payload.pop("customer_id", None)
        return payload

    @staticmethod
    def api_response(
        message,
        data=None,
        *,
        success=True,
        status_code=status.HTTP_200_OK,
    ):
        return Response(
            {
                "success": success,
                "message": message,
                "data": data,
            },
            status=status_code,
        )


class TicketCategoryListView(BaseTicketCategoryView):

    @extend_schema(
        operation_id="ticket_categories_list",
        tags=["Ticket Categories"],
        parameters=[
            OpenApiParameter(
                name="customer_id",
                type=OpenApiTypes.UUID,
                location=OpenApiParameter.QUERY,
                required=False,
                description="Target company UUID (required for internal users).",
            ),
            OpenApiParameter(
                name="search",
                type=OpenApiTypes.STR,
                location=OpenApiParameter.QUERY,
                required=False,
                description="Search category name and description.",
            ),
            OpenApiParameter(
                name="is_active",
                type=OpenApiTypes.BOOL,
                location=OpenApiParameter.QUERY,
                required=False,
                description="Filter by active status (true or false).",
            ),
            OpenApiParameter(
                name="ordering",
                type=OpenApiTypes.STR,
                location=OpenApiParameter.QUERY,
                required=False,
                description=(
                    "Allowed values: position, -position, name, -name, "
                    "created_at, -created_at, updated_at, -updated_at."
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
        responses={200: TicketCategoryListResponseSerializer},
        summary="List ticket categories",
        description=(
            "Retrieve ticket categories with pagination, search, "
            "filtering and ordering. Users without the manage "
            "permission only see active categories."
        ),
    )
    def get(self, request):
        customer = self.get_customer(request)
        can_manage = self.can_manage(request, customer)
        categories = get_visible_categories(
            customer,
            can_manage=can_manage,
        )

        search = request.query_params.get("search")
        if search:
            search = re.sub(r"\s+", " ", search).strip()
            if search:
                categories = categories.filter(
                    Q(name__icontains=search)
                    | Q(description__icontains=search)
                )

        is_active = request.query_params.get("is_active")
        if is_active is not None:
            is_active_clean = is_active.strip().lower()
            if is_active_clean not in ("true", "false"):
                return self.api_response(
                    "is_active must be true or false.",
                    success=False,
                    status_code=status.HTTP_400_BAD_REQUEST,
                )

            if is_active_clean == "false" and not can_manage:
                raise PermissionDenied(
                    "You do not have permission to view "
                    "inactive ticket categories."
                )

            categories = categories.filter(
                is_active=is_active_clean == "true"
            )

        ordering = (
            request.query_params.get("ordering") or ""
        ).strip() or "position"
        if ordering not in ALLOWED_CATEGORY_ORDERING:
            return self.api_response(
                "Invalid ordering field. Allowed values are: "
                "position, -position, name, -name, created_at, "
                "-created_at, updated_at, -updated_at.",
                success=False,
                status_code=status.HTTP_400_BAD_REQUEST,
            )

        page_size = request.query_params.get("page_size")
        if page_size is not None:
            try:
                page_size_int = int(page_size.strip())
            except (ValueError, TypeError):
                return self.api_response(
                    "page_size must be an integer between "
                    f"1 and {TicketCategoryPagination.max_page_size}.",
                    success=False,
                    status_code=status.HTTP_400_BAD_REQUEST,
                )

            if (
                page_size_int < 1
                or page_size_int > TicketCategoryPagination.max_page_size
            ):
                return self.api_response(
                    "page_size must be between "
                    f"1 and {TicketCategoryPagination.max_page_size}.",
                    success=False,
                    status_code=status.HTTP_400_BAD_REQUEST,
                )

        categories = categories.order_by(ordering, "id")

        paginator = TicketCategoryPagination()
        page = paginator.paginate_queryset(
            categories,
            request,
            view=self,
        )

        serializer = TicketCategorySerializer(
            page,
            many=True,
            context={"customer": customer},
        )

        return self.api_response(
            "Ticket categories retrieved successfully.",
            data={
                "count": paginator.page.paginator.count,
                "next": paginator.get_next_link(),
                "previous": paginator.get_previous_link(),
                "results": serializer.data,
            },
        )

    @extend_schema(
        operation_id="ticket_categories_create",
        tags=["Ticket Categories"],
        request=TicketCategorySerializer,
        responses={201: TicketCategoryResponseSerializer},
        summary="Create ticket category",
    )
    def post(self, request):
        customer = self.get_customer(request)
        if isinstance(request.data, Mapping) and "customer_id" in request.data:
            if CustomerUser.objects.filter(user=request.user).exists():
                raise ValidationError({
                    "customer_id": ["This field is not allowed."]
                })
        payload = self._extract_payload(request.data)

        serializer = TicketCategorySerializer(
            data=payload,
            context={"customer": customer},
        )
        serializer.is_valid(raise_exception=True)

        ip_address, user_agent = get_request_audit_meta(request)

        category = TicketCategoryService.create(
            customer=customer,
            validated_data=serializer.validated_data,
            performed_by=request.user,
            ip_address=ip_address,
            user_agent=user_agent,
        )

        return self.api_response(
            "Ticket category created successfully.",
            data=TicketCategorySerializer(
                category,
                context={"customer": customer},
            ).data,
            status_code=status.HTTP_201_CREATED,
        )


class TicketCategoryDetailView(BaseTicketCategoryView):

    def get_category(self, request, id, customer=None):
        if customer is None:
            customer = self.get_customer(request)
        can_manage = self.can_manage(request, customer)
        try:
            return get_visible_categories(
                customer,
                can_manage=can_manage,
            ).get(id=id)
        except TicketCategory.DoesNotExist:
            raise NotFound("Ticket category not found.")

    @extend_schema(
        operation_id="ticket_categories_retrieve",
        tags=["Ticket Categories"],
        responses={200: TicketCategoryResponseSerializer},
        summary="Get ticket category",
    )
    def get(self, request, id):
        customer = self.get_customer(request)
        category = self.get_category(request, id, customer=customer)

        return self.api_response(
            "Ticket category retrieved successfully.",
            data=TicketCategorySerializer(
                category,
                context={"customer": customer},
            ).data,
        )

    @extend_schema(
        operation_id="ticket_categories_update",
        tags=["Ticket Categories"],
        request=TicketCategorySerializer,
        responses={200: TicketCategoryResponseSerializer},
        summary="Update ticket category",
    )
    def patch(self, request, id):
        customer = self.get_customer(request)
        category = self.get_category(request, id, customer=customer)

        serializer = TicketCategorySerializer(
            category,
            data=request.data,
            partial=True,
            context={"customer": customer},
        )
        serializer.is_valid(raise_exception=True)

        ip_address, user_agent = get_request_audit_meta(request)

        updated_category = TicketCategoryService.update(
            customer=customer,
            category=category,
            validated_data=serializer.validated_data,
            performed_by=request.user,
            ip_address=ip_address,
            user_agent=user_agent,
        )

        return self.api_response(
            "Ticket category updated successfully.",
            data=TicketCategorySerializer(
                updated_category,
                context={"customer": customer},
            ).data,
        )

    @extend_schema(
        operation_id="ticket_categories_delete",
        tags=["Ticket Categories"],
        request=None,
        responses={
            200: TicketCategoryDeleteResponseSerializer,
            404: TicketCategoryErrorResponseSerializer,
            409: TicketCategoryErrorResponseSerializer,
        },
        summary="Delete ticket category",
        description=(
            "Delete a category that is not used by any ticket. "
            "Returns 409 if the category is already in use."
        ),
    )
    def delete(self, request, id):
        customer = self.get_customer(request)
        category = self.get_category(request, id, customer=customer)

        ip_address, user_agent = get_request_audit_meta(request)

        deleted = TicketCategoryService.delete(
            customer=customer,
            category=category,
            performed_by=request.user,
            ip_address=ip_address,
            user_agent=user_agent,
        )

        return self.api_response(
            f"Ticket category '{deleted['name']}' deleted successfully.",
            data=deleted,
        )
