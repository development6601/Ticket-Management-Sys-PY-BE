from rest_framework import status
from rest_framework.exceptions import NotFound
from rest_framework.response import Response
from rest_framework.views import APIView

from drf_spectacular.utils import extend_schema

from apps.tickets.api.serializers.assignments import (
    TicketAssignmentRequestSerializer,
    TicketAssignmentResponseSerializer,
)
from apps.tickets.services.assignment import (
    change_ticket_assignment,
)
from apps.tickets.services.visibility import (
    visible_tickets_queryset,
)


class TicketAssignmentView(APIView):

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

    @extend_schema(
        operation_id="ticket_assignment_update",
        tags=["Ticket Assignments"],
        request=TicketAssignmentRequestSerializer,
        responses={200: TicketAssignmentResponseSerializer},
        summary="Manage ticket assignment",
        description=(
            "Assign, reassign, unassign or transfer a ticket. "
            "The operation is processed through the ticket "
            "assignment service."
        ),
    )
    def post(self, request, ticket_id):
        serializer = TicketAssignmentRequestSerializer(
            data=request.data
        )
        serializer.is_valid(raise_exception=True)

        # Apply visibility scope before accessing the ticket.
        ticket = (
            visible_tickets_queryset(request.user)
            .filter(pk=ticket_id)
            .first()
        )

        if ticket is None:
            raise NotFound("Ticket not found.")

        data = serializer.validated_data

        updated_ticket = change_ticket_assignment(
            ticket_id=ticket.pk,
            user=request.user,
            action=data["action"],
            employee_id=data.get("employee_id"),
            team_id=data.get("team_id"),
            reason=data.get("reason"),
        )

        updated_ticket.refresh_from_db()

        return self.api_response(
            "Ticket assignment updated successfully.",
            data={
                "ticket_id": str(updated_ticket.pk),
                "ticket_number": updated_ticket.ticket_number,
                "assigned_team": (
                    {
                        "id": str(updated_ticket.assigned_team_id),
                        "name": updated_ticket.assigned_team.name,
                    }
                    if updated_ticket.assigned_team_id
                    else None
                ),
                "assigned_employee": (
                    {
                        "id": str(updated_ticket.assigned_employee_id),
                        "email": updated_ticket.assigned_employee.email,
                    }
                    if updated_ticket.assigned_employee_id
                    else None
                ),
            },
        )
