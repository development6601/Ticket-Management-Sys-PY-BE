from rest_framework import serializers, status
from rest_framework.exceptions import NotFound, PermissionDenied
from rest_framework.response import Response
from rest_framework.views import APIView

from drf_spectacular.utils import extend_schema, inline_serializer

from apps.tickets.api.serializers.tickets import TicketDetailSerializer
from apps.tickets.api.serializers.transitions import (
    TicketTransitionRequestSerializer,
    TicketAvailableTransitionsResponseSerializer,
)
from apps.tickets.models import Ticket, TicketStatusTransition
from apps.tickets.services.authorization import (
    authorize_ticket_transition,
)
from apps.tickets.services.visibility import visible_tickets_queryset
from apps.tickets.services.workflow import execute_ticket_transition


class BaseTicketTransitionView(APIView):
    def get_ticket(self, user, ticket_number):
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

    def get_ticket_detail(self, ticket_id):
        return (
            Ticket.objects
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
            .get(pk=ticket_id)
        )


class TicketAvailableTransitionsView(BaseTicketTransitionView):

    @extend_schema(
        operation_id="ticket_available_transitions",
        tags=["Tickets"],
        summary="List available ticket transitions",
        responses={
            200: TicketAvailableTransitionsResponseSerializer,
        },
    )
    def get(self, request, ticket_number):
        ticket = self.get_ticket(request.user, ticket_number)

        candidate_transitions = (
            TicketStatusTransition.objects
            .select_related(
                "from_status",
                "to_status",
                "required_permission",
            )
            .filter(
                from_status_id=ticket.status_id,
                is_active=True,
                to_status__customer_id = ticket.customer_id,
                to_status__is_active=True,
            )
        )

        available_transitions = []

        for transition in candidate_transitions:
            try: 
                action = authorize_ticket_transition(
                    user=request.user,
                    ticket=ticket,
                    transition=transition,
                )

                available_transitions.append({
                    "to_status_id": transition.to_status_id,
                    "to_status_name": transition.to_status.name,
                    "action": action,
                    "requires_resolution_details": action == "resolve",
                    "requires_reason": action == "reopen",
                })

            except PermissionDenied:
                continue

        return Response(
            {
                "success": True,
                "message": "Available transitions retrieved successfully.",
                "data": {
                    "ticket_number": ticket.ticket_number,
                    "current_status": {
                        "id": ticket.status_id,
                        "name": ticket.status.name,
                    },
                    "available_transitions": available_transitions,
                },
            },
            status=status.HTTP_200_OK,
        )


class TicketTransitionView(BaseTicketTransitionView):

    @extend_schema(
        operation_id="ticket_transition_execute",
        tags=["Tickets"],
        summary="Transition ticket status",
        request=TicketTransitionRequestSerializer,
        responses={
            200: inline_serializer(
                name="TicketTransitionResponse",
                fields={
                    "success": serializers.BooleanField(),
                    "message": serializers.CharField(),
                    "data": TicketDetailSerializer(),
                },
            ),
        },
    )
    def post(self, request, ticket_number):
        serializer = TicketTransitionRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        data = serializer.validated_data

        ticket = self.get_ticket(request.user, ticket_number)

        resolution_notes = (
            (data.get("resolution_notes") or "").strip() or None
        )
        reason = (data.get("reason") or "").strip() or None

        updated_ticket = execute_ticket_transition(
            ticket_id=ticket.pk,
            to_status_id=data["to_status_id"],
            user=request.user,
            resolution_type=data.get("resolution_type"),
            resolution_notes=resolution_notes,
            reason=reason,
        )

        refreshed_ticket = self.get_ticket_detail(updated_ticket.pk)

        return Response(
            {
                "success": True,
                "message": (
                    f"Ticket transitioned to "
                    f"'{refreshed_ticket.status.name}' successfully."
                ),
                "data": TicketDetailSerializer(refreshed_ticket).data,
            },
            status=status.HTTP_200_OK,
        )