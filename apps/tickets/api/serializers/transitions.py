from collections.abc import Mapping

from rest_framework import serializers
from apps.tickets.models import Ticket


class TicketTransitionRequestSerializer(serializers.Serializer):
    """
    Request serializer for executing a ticket status transition.
    """

    to_status_id = serializers.UUIDField(
        required = True,
        help_text = "UUID of the destination ticket status.",
    )

    resolution_type = serializers.ChoiceField(
        choices=Ticket.ResolutionType.choices,
        required=False,
        allow_null=True,
        default=None,
    )

    resolution_notes = serializers.CharField(
        required=False,
        allow_null=True,
        allow_blank=True,
        default=None,
    )

    reason = serializers.CharField(
        required=False,
        allow_null=True,
        allow_blank=True,
        default=None,
    )

    def to_internal_value(self, data):
        if not isinstance(data, Mapping):
            raise serializers.ValidationError(
                "Expected a JSON object."
            )

        allowed_fields = {
            "to_status_id",
            "resolution_type",
            "resolution_notes",
            "reason",
        }

        unknown_fields = set(data.keys()) - allowed_fields

        if unknown_fields:
            raise serializers.ValidationError({
                field: "This field is not allowed."
                for field in sorted(unknown_fields)
            })

        return super().to_internal_value(data)


class AvailableTransitionItemSerializer(serializers.Serializer):
    to_status_id = serializers.UUIDField()
    to_status_name = serializers.CharField()
    action = serializers.CharField()

    requires_resolution_details = serializers.BooleanField()
    requires_reason = serializers.BooleanField()

class CurrentTicketStatusSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    name = serializers.CharField()

class AvailableTransitionsDataSerializer(serializers.Serializer):
    ticket_number = serializers.CharField()
    current_status = CurrentTicketStatusSerializer()
    available_transitions = AvailableTransitionItemSerializer(
        many=True
    )

class TicketAvailableTransitionsResponseSerializer(serializers.Serializer):
    success = serializers.BooleanField()
    message = serializers.CharField()
    data = AvailableTransitionsDataSerializer()