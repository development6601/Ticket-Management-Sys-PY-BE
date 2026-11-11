
from rest_framework import status
from rest_framework.exceptions import NotFound, ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView

from drf_spectacular.utils import extend_schema

from apps.accounts.permissions.constants import TICKETS_CREATE
from apps.accounts.permissions.rbac import HasPermission
from apps.customers.models import Customer, CustomerUser
from apps.tickets.api.serializers.tickets import (
    TicketCreateSerializer,
    TicketCreateResponseSerializer,
)
from apps.tickets.models import TicketCategory, TicketStatus
from apps.tickets.services.ticket_creation import TicketCreationService


class CanCreateTicket(HasPermission):
    required_permission = TICKETS_CREATE


class TicketCreateView(APIView):
    permission_classes = [CanCreateTicket]

    @extend_schema(
        operation_id="ticket_create",
        tags=["Tickets"],
        request=TicketCreateSerializer,
        responses={201: TicketCreateResponseSerializer},
        summary="Create a ticket",
        description=(
            "Create a ticket for the authenticated user's company. "
            "Internal users must provide customer_id. The backend "
            "generates the ticket number and selects the initial status."
        ),
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
