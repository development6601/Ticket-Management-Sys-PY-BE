
from rest_framework import serializers

from apps.tickets.models import Ticket


class TicketCreateSerializer(serializers.Serializer):
    customer_id = serializers.UUIDField(
        required=False,
        help_text=(
            "Required for internal users. Customer users are "
            "automatically restricted to their own company."
        ),
    )
    category_id = serializers.UUIDField()
    subject = serializers.CharField(max_length=255, trim_whitespace=True)
    description = serializers.CharField(
        required=False,
        allow_blank=True,
        default="",
    )
    priority = serializers.ChoiceField(
        choices=Ticket.Priority.choices,
        required=False,
        default=Ticket.Priority.MEDIUM,
    )
    due_at = serializers.DateTimeField(
        required=False,
        allow_null=True,
        default=None,
    )

    def to_internal_value(self, data):
        from collections.abc import Mapping

        if not isinstance(data, Mapping):
            raise serializers.ValidationError(
                "Expected a JSON object."
            )

        allowed = {
            "customer_id",
            "category_id",
            "subject",
            "description",
            "priority",
            "due_at",
        }
        unknown = set(data.keys()) - allowed

        if unknown:
            raise serializers.ValidationError({
                field: "This field is not allowed."
                for field in sorted(unknown)
            })

        return super().to_internal_value(data)

    def validate_subject(self, value):
        value = value.strip()
        if not value:
            raise serializers.ValidationError(
                "Subject cannot be empty."
            )
        return value


class TicketCreateResponseSerializer(serializers.ModelSerializer):
    customer_id = serializers.UUIDField(read_only=True)
    category_id = serializers.UUIDField(read_only=True)
    status_id = serializers.UUIDField(read_only=True)
    assigned_team_id = serializers.UUIDField(read_only=True)
    assigned_employee_id = serializers.UUIDField(read_only=True)

    class Meta:
        model = Ticket
        fields = [
            "id",
            "ticket_number",
            "subject",
            "description",
            "customer_id",
            "category_id",
            "priority",
            "status_id",
            "assigned_team_id",
            "assigned_employee_id",
            "due_at",
            "created_at",
        ]
        read_only_fields = fields
