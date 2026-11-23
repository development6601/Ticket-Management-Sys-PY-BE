from rest_framework import status
from rest_framework.exceptions import NotFound, ValidationError, PermissionDenied
from rest_framework.response import Response
from rest_framework.views import APIView

from drf_spectacular.utils import (
    extend_schema,
    OpenApiExample,
    OpenApiParameter,
    OpenApiTypes,
)

from apps.accounts.permissions.constants import (
    TICKETS_CREATE,
    TICKETS_VIEW,
)
from apps.accounts.permissions.rbac import HasPermission
from apps.customers.models import Customer, CustomerUser
from apps.tickets.api.filters import (
    TicketListFilterSerializer,
    TicketPagination,
    filter_tickets,
)
from apps.tickets.api.serializers.tickets import (
    TicketCreateSerializer,
    TicketCreateResponseSerializer,
    TicketDetailSerializer,
    TicketDetailResponseSerializer,
    TicketListSerializer,
    TicketListResponseSerializer,
    TicketUpdateSerializer,
)
from apps.tickets.models import TicketCategory, TicketStatus
from apps.tickets.services.ticket_creation import TicketCreationService
from apps.tickets.services.ticket_update import TicketUpdateService
from apps.tickets.services.visibility import (
    visible_tickets_queryset,
)


class CanCreateTicket(HasPermission):
    required_permission = TICKETS_CREATE


class CanViewTickets(HasPermission):
    required_permission = TICKETS_VIEW


class TicketCreateView(APIView):

    def get_permissions(self):
        """
        Use separate permissions for listing and creating tickets.
        GET  -> tickets.view
        POST -> tickets.create
        """
        if self.request.method == "GET":
            return [CanViewTickets()]
        if self.request.method == "POST":
            return [CanCreateTicket()]
        return super().get_permissions()

    @extend_schema(
        operation_id="ticket_list",
        tags=["Tickets"],
        parameters=[
            OpenApiParameter(
                name="search",
                type=OpenApiTypes.STR,
                location=OpenApiParameter.QUERY,
                required=False,
                description="Search ticket number, subject, customer name/email.",
            ),
            OpenApiParameter(
                name="status",
                type=OpenApiTypes.UUID,
                location=OpenApiParameter.QUERY,
                required=False,
                description="Filter by status ID.",
            ),
            OpenApiParameter(
                name="priority",
                type=OpenApiTypes.STR,
                location=OpenApiParameter.QUERY,
                required=False,
                description="Filter by priority (LOW, MEDIUM, HIGH, CRITICAL).",
            ),
            OpenApiParameter(
                name="category",
                type=OpenApiTypes.UUID,
                location=OpenApiParameter.QUERY,
                required=False,
                description="Filter by category ID.",
            ),
            OpenApiParameter(
                name="assigned_employee",
                type=OpenApiTypes.UUID,
                location=OpenApiParameter.QUERY,
                required=False,
                description="Filter by employee ID.",
            ),
            OpenApiParameter(
                name="team",
                type=OpenApiTypes.UUID,
                location=OpenApiParameter.QUERY,
                required=False,
                description="Filter by support team ID.",
            ),
            OpenApiParameter(
                name="customer",
                type=OpenApiTypes.UUID,
                location=OpenApiParameter.QUERY,
                required=False,
                description="Filter by customer ID.",
            ),
            OpenApiParameter(
                name="created_from",
                type=OpenApiTypes.DATETIME,
                location=OpenApiParameter.QUERY,
                required=False,
                description="Creation date range start.",
            ),
            OpenApiParameter(
                name="created_to",
                type=OpenApiTypes.DATETIME,
                location=OpenApiParameter.QUERY,
                required=False,
                description="Creation date range end.",
            ),
            OpenApiParameter(
                name="updated_from",
                type=OpenApiTypes.DATETIME,
                location=OpenApiParameter.QUERY,
                required=False,
                description="Update date range start.",
            ),
            OpenApiParameter(
                name="updated_to",
                type=OpenApiTypes.DATETIME,
                location=OpenApiParameter.QUERY,
                required=False,
                description="Update date range end.",
            ),
            OpenApiParameter(
                name="due_from",
                type=OpenApiTypes.DATETIME,
                location=OpenApiParameter.QUERY,
                required=False,
                description="Due date range start.",
            ),
            OpenApiParameter(
                name="due_to",
                type=OpenApiTypes.DATETIME,
                location=OpenApiParameter.QUERY,
                required=False,
                description="Due date range end.",
            ),
            OpenApiParameter(
                name="ordering",
                type=OpenApiTypes.STR,
                location=OpenApiParameter.QUERY,
                required=False,
                description="Sorting field(s), comma-separated. Default is -created_at.",
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
                description="Results per page, maximum 100.",
            ),
        ],
        responses={200: TicketListResponseSerializer},
        summary="List tickets",
        description=(
            "Retrieve tickets visible to the authenticated user with "
            "filtering, search, ordering, and pagination."
        ),
    )
    def get(self, request):
        # STEP 1: Validate query parameters.
        filter_serializer = TicketListFilterSerializer(
            data=request.query_params
        )
        filter_serializer.is_valid(raise_exception=True)
        filters = filter_serializer.validated_data

        # STEP 2: Retrieve only the tickets visible to the authenticated user.
        tickets = visible_tickets_queryset(request.user)

        # STEP 3: Optimize queries with select_related.
        tickets = tickets.select_related(
            "customer",
            "created_by",
            "category",
            "status",
            "assigned_team",
            "assigned_employee",
        )
        # STEP 4: Apply all requested filters and search.
        tickets = filter_tickets(
            tickets,
            filters,
        )
        # STEP 5: Apply validated ordering.
        ordering = filters.get(
            "ordering",
            ["-created_at"],
        )
        tickets = tickets.order_by(
            *ordering,
            "id",
        ).distinct()
        # STEP 6: Apply pagination.
        paginator = TicketPagination()
        page = paginator.paginate_queryset(
            tickets,
            request,
            view=self,
        )
        # STEP 7: Serialize the current page.
        serializer = TicketListSerializer(
            page,
            many=True,
        )
        # STEP 8: Return the standard project response structure.
        return Response(
            {
                "success": True,
                "message": "Tickets retrieved successfully.",
                "data": {
                    "count": paginator.page.paginator.count,
                    "next": paginator.get_next_link(),
                    "previous": paginator.get_previous_link(),
                    "results": serializer.data,
                },
            },
            status=status.HTTP_200_OK,
        )


    @extend_schema(
        operation_id="ticket_create",
        tags=["Tickets"],
        request=TicketCreateSerializer,
        responses={201: TicketCreateResponseSerializer},
        summary="Create a support ticket",
        description=(
            "Create a support ticket for a customer company.\n\n"
            "**Customer ID:** Required for internal users. "
            "Customer users are automatically associated with "
            "their own company.\n\n"
            "**Category:** Must belong to the selected customer "
            "company and be active.\n\n"
            "**Priority:** Optional. Defaults to MEDIUM.\n\n"
            "**Description:** Optional. Defaults to an empty string.\n\n"
            "**Due date:** Optional. Can be omitted or set to null.\n"
            "Provide an ISO 8601 datetime when a due date is required.\n\n"
            "**Ticket number and initial status:** Generated by the backend.\n\n"
            "**Assignment:** The backend automatically selects a support "
            "team when routing is configured."
        ),
        examples=[
            OpenApiExample(
                name="Create ticket without due date",
                summary="Internal user - no due date",
                description=(
                    "Recommended example when a ticket does not "
                    "have a due date."
                ),
                value={
                    "customer_id": (
                        "7ed4e6a5-91b9-4a50-afaf-3c6d69970e82"
                    ),
                    "category_id": (
                        "3b493e87-e2ed-450d-8824-cabb1f4ecbe6"
                    ),
                    "subject": "Unable to log in",
                    "description": (
                        "The customer cannot log in to the application. "
                        "The login page displays an authentication error."
                    ),
                    "priority": "HIGH",
                    "due_at": None,
                },
                request_only=True,
            ),
            OpenApiExample(
                name="Create ticket with due date",
                summary="Internal user - with due date",
                description="Example with an assigned due date.",
                value={
                    "customer_id": (
                        "7ed4e6a5-91b9-4a50-afaf-3c6d69970e82"
                    ),
                    "category_id": (
                        "3b493e87-e2ed-450d-8824-cabb1f4ecbe6"
                    ),
                    "subject": "Application performance issue",
                    "description": (
                        "The application takes too long to load "
                        "the customer dashboard."
                    ),
                    "priority": "MEDIUM",
                    "due_at": "2026-12-01T18:00:00Z",
                },
                request_only=True,
            ),
            OpenApiExample(
                name="Create ticket with default values",
                summary="Minimal internal user request",
                description=(
                    "Priority defaults to MEDIUM, description defaults "
                    "to an empty string and due_at defaults to null."
                ),
                value={
                    "customer_id": (
                        "7ed4e6a5-91b9-4a50-afaf-3c6d69970e82"
                    ),
                    "category_id": (
                        "3b493e87-e2ed-450d-8824-cabb1f4ecbe6"
                    ),
                    "subject": "General support request",
                },
                request_only=True,
            ),
            OpenApiExample(
                name="Create ticket as customer user",
                summary="Customer user - without customer_id",
                description=(
                    "Customer users do not need to send customer_id. "
                    "The backend identifies their company from "
                    "their authenticated account."
                ),
                value={
                    "category_id": (
                        "3b493e87-e2ed-450d-8824-cabb1f4ecbe6"
                    ),
                    "subject": "Unable to access reports",
                    "description": (
                        "The reports page displays an unexpected error."
                    ),
                    "priority": "MEDIUM",
                    "due_at": None,
                },
                request_only=True,
            ),
        ],
    )
    def post(self, request):
        serializer = TicketCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        membership = (
            CustomerUser.objects
            .select_related("customer")
            .filter(user=request.user)
            .first()
        )

        if membership:
            customer = membership.customer

            if data.get("customer_id") and (
                data["customer_id"] != customer.pk
            ):
                from rest_framework.exceptions import PermissionDenied
                raise PermissionDenied(
                    "You cannot create tickets for another company."
                )
        else:
            customer_id = data.get("customer_id")
            if not customer_id:
                raise ValidationError({
                    "customer_id": (
                        "This field is required for internal users."
                    )
                })

            customer = Customer.objects.filter(
                pk=customer_id,
                deleted_at__isnull=True,
            ).first()

            if customer is None:
                raise NotFound("Customer company not found.")

        if (
            customer.deleted_at is not None
            or not customer.is_active
            or customer.account_status != Customer.AccountStatus.ACTIVE
        ):
            raise ValidationError({
                "customer_id": "This customer company is inactive."
            })

        category = TicketCategory.objects.filter(
            pk=data["category_id"],
            customer=customer,
            is_active=True,
        ).first()

        if category is None:
            raise ValidationError({
                "category_id": (
                    "Select an active category belonging to this company."
                )
            })

        initial_status = TicketStatus.objects.filter(
            customer=customer,
            is_initial=True,
            is_active=True,
        ).first()

        if initial_status is None:
            raise ValidationError({
                "status": (
                    "No active initial ticket status is configured "
                    "for this company."
                )
            })

        # Automatic routing:
        # 1. Category's configured support team.
        # 2. Company's default support team.
        assigned_team = getattr(
            category,
            "default_support_team",
            None,
        )

        if assigned_team is None:
            assigned_team = getattr(
                customer,
                "default_support_team",
                None,
            )

        if assigned_team and not assigned_team.is_active:
            assigned_team = None

        ticket = TicketCreationService.create_ticket(
            customer_id=customer.pk,
            performed_by=request.user,
            subject=data["subject"],
            description=data.get("description", ""),
            category=category,
            initial_status=initial_status,
            priority=data.get("priority"),
            assigned_team=assigned_team,
            assigned_employee=None,
            due_at=data.get("due_at"),
        )

        return Response(
            {
                "success": True,
                "message": "Ticket created successfully.",
                "data": TicketCreateResponseSerializer(ticket).data,
            },
            status=status.HTTP_201_CREATED,
        )


class TicketDetailUpdateView(APIView):

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

    def get_ticket(self, user, ticket_number):
        """
        Retrieve a ticket only when it is visible to the user.

        Returning 404 for inaccessible tickets avoids exposing
        whether another company's ticket number exists.
        """

        ticket = (
            visible_tickets_queryset(user)
            .select_related(
                "customer",
                "created_by",
                "category",
                "status",
                "assigned_team",
                "assigned_employee",
                "resolved_by",
                "closed_by",
                "resolution_confirmed_by",
            )
            .filter(ticket_number=ticket_number)
            .first()
        )

        if ticket is None:
            raise NotFound("Ticket not found.")

        return ticket


    # GET /api/v1/tickets/{ticket_number}/
    # Retrieve complete ticket details.

    @extend_schema(
        operation_id="ticket_detail",
        tags=["Tickets"],
        responses={
            200: TicketDetailResponseSerializer,
        },
        summary="Retrieve ticket details",
        description=(
            "Retrieve the details of a ticket that is visible "
            "to the authenticated user. Ticket visibility is "
            "controlled by the ticket visibility service."
        ),
    )
    def get(self, request, ticket_number):

        ticket = self.get_ticket(
            request.user,
            ticket_number,
        )

        serializer = TicketDetailSerializer(ticket)

        return self.api_response(
            "Ticket details retrieved successfully.",
            data=serializer.data,
        )

    # PATCH /api/v1/tickets/{ticket_number}/
    # Update permitted ticket information.

    @extend_schema(
        operation_id="ticket_update",
        tags=["Tickets"],
        request=TicketUpdateSerializer,
        responses={
            200: TicketDetailResponseSerializer,
        },
        summary="Update ticket information",
        description=(
            "Update permitted ticket information such as subject, "
            "description, category, priority and due date. "
            "Status and assignment changes must use their "
            "dedicated APIs. Requires tickets.update permission."
        ),
    )

    def patch(self, request, ticket_number):

        # STEP 1: Validate the request payload.
        serializer = TicketUpdateSerializer(
            data=request.data,
        )
        serializer.is_valid(raise_exception=True)

        # STEP 2: Check ticket visibility before performing
        # the update. The service checks visibility again while
        # locking the ticket to protect the actual write operation.
        ticket = self.get_ticket(
            request.user,
            ticket_number,
        )

        # STEP 3: Delegate the update to the service layer.
        updated_ticket = TicketUpdateService.update_ticket(
            ticket_id=ticket.pk,
            user=request.user,
            validated_data=serializer.validated_data,
        )

        # STEP 4: Retrieve the updated ticket with its related
        # objects loaded for the detail response.
        updated_ticket = self.get_ticket(
            request.user,
            ticket_number,
        )

        return self.api_response(
            "Ticket updated successfully.",
            data=TicketDetailSerializer(updated_ticket).data,
        )
