from rest_framework import serializers
from drf_spectacular.utils import extend_schema_field

from apps.tickets.models import Ticket, TicketCategory, TicketStatus
from apps.customers.models import Customer
from apps.support.models import SupportTeam

class TicketCreateSerializer(serializers.Serializer):
    customer_id = serializers.UUIDField(
        required=False,
        help_text=(
            "Customer company UUID. Required for internal users. "
            "Customer users are automatically associated with "
            "their own company."
        ),
    )

    category_id = serializers.UUIDField(
        help_text=(
            "UUID of an active ticket category belonging "
            "to the selected customer company."
        ),
    )
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


# Small nested serializers used by the ticket detail response.
# These provide readable related-object information without
# exposing unnecessary fields from the related models.

class TicketUserSummarySerializer(serializers.Serializer):
    id = serializers.UUIDField(read_only=True)
    email = serializers.EmailField(read_only=True)
    first_name = serializers.CharField(read_only=True)
    last_name = serializers.CharField(read_only=True)
    display_name = serializers.SerializerMethodField()

    @extend_schema_field(serializers.CharField())
    def get_display_name(self, obj) -> str:
        """
        Return the user's full name.
        Fall back to email when the user has no name.
        """
        full_name = obj.get_full_name().strip()
        return full_name or obj.email


class TicketCustomerSummarySerializer(serializers.ModelSerializer):

    class Meta:
        model = Customer
        fields = [
            "id",
            "company_name",
        ]

class TicketCategorySummarySerializer(serializers.ModelSerializer):

    class Meta:
        model = TicketCategory
        fields = [
            "id",
            "name",
        ]

class TicketStatusSummarySerializer(serializers.ModelSerializer):

    class Meta:
        model = TicketStatus
        fields = [
            "id",
            "name",
            "is_initial",
            "is_resolved",
            "is_closed",
        ]

class TicketTeamSummarySerializer(serializers.ModelSerializer):

    class Meta:
        model = SupportTeam
        fields = [
            "id",
            "name",
        ]


# Ticket detail response.This serializer is read-only.
# All ticket changes must go through TicketUpdateService and the PATCH API.

class TicketDetailSerializer(serializers.ModelSerializer):
    customer = TicketCustomerSummarySerializer(read_only=True)
    created_by = TicketUserSummarySerializer(read_only=True)

    category = TicketCategorySummarySerializer(read_only=True)
    status = TicketStatusSummarySerializer(read_only=True)

    assigned_team = TicketTeamSummarySerializer(
        read_only=True,
        allow_null=True,
    )
    assigned_employee = TicketUserSummarySerializer(
        read_only=True,
        allow_null=True,
    )

    resolved_by = TicketUserSummarySerializer(
        read_only=True,
        allow_null=True,
    )
    closed_by = TicketUserSummarySerializer(
        read_only=True,
        allow_null=True,
    )
    resolution_confirmed_by = TicketUserSummarySerializer(
        read_only=True,
        allow_null=True,
    )

    class Meta:
        model = Ticket
        fields = [
            "id",
            "ticket_number",
            "subject",
            "description",
            "customer",
            "created_by",
            "category",
            "priority",
            "status",
            "assigned_team",
            "assigned_employee",
            "due_at",
            "resolution_type",
            "resolution_notes",
            "resolved_at",
            "resolved_by",
            "closed_at",
            "closed_by",
            "resolution_confirmed_at",
            "resolution_confirmed_by",
            "reopen_count",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields


# Ticket update request serializer.
# Only ticket information fields are accepted.
# Status and assignment changes must use their dedicated APIs.

class TicketUpdateSerializer(serializers.Serializer):
    subject = serializers.CharField(
        required=False,
        max_length=255,
        trim_whitespace=True,
    )
    description = serializers.CharField(
        required=False,
        allow_blank=True,
        trim_whitespace=False,
    )
    category_id = serializers.UUIDField(
        required=False,
    )
    priority = serializers.ChoiceField(
        choices=Ticket.Priority.choices,
        required=False,
    )
    due_at = serializers.DateTimeField(
        required=False,
        allow_null=True,
    )

    def to_internal_value(self, data):
        from collections.abc import Mapping

        if not isinstance(data, Mapping):
            raise serializers.ValidationError(
                "Expected a JSON object."
            )

        allowed_fields = {
            "subject",
            "description",
            "category_id",
            "priority",
            "due_at",
        }

        unknown_fields = set(data.keys()) - allowed_fields

        if unknown_fields:
            raise serializers.ValidationError({
                field: "This field cannot be updated through this API."
                for field in sorted(unknown_fields)
            })

        return super().to_internal_value(data)

    def validate_subject(self, value):
        value = value.strip()

        if not value:
            raise serializers.ValidationError(
                "Subject cannot be empty."
            )

        return value

    def validate(self, attrs):
        if not attrs:
            raise serializers.ValidationError(
                "At least one field must be provided."
            )

        return attrs


# response wrapper for ticket detail and update.

class TicketDetailResponseSerializer(serializers.Serializer):
    success = serializers.BooleanField()
    message = serializers.CharField()
    data = TicketDetailSerializer()


# Ticket list serializer
class TicketListSerializer(serializers.ModelSerializer):
    """
    Lightweight ticket representation for the list API.

    Related object names are included for convenient frontend display.
    Detailed ticket information is handled by TicketDetailSerializer.
    """

    customer_name = serializers.CharField(
        source="customer.company_name",
        read_only=True,
    )
    customer_email = serializers.EmailField(
        source="customer.email",
        read_only=True,
    )
    created_by_name = serializers.SerializerMethodField()

    category_name = serializers.CharField(
        source="category.name",
        read_only=True,
    )
    status_name = serializers.CharField(
        source="status.name",
        read_only=True,
    )
    assigned_team_name = serializers.CharField(
        source="assigned_team.name",
        read_only=True,
        allow_null=True,
    )
    assigned_employee_name = serializers.SerializerMethodField()

    class Meta:
        model = Ticket
        fields = [
            "id",
            "ticket_number",
            "subject",
            "customer_id",
            "customer_name",
            "customer_email",
            "created_by_id",
            "created_by_name",
            "category_id",
            "category_name",
            "priority",
            "status_id",
            "status_name",
            "assigned_team_id",
            "assigned_team_name",
            "assigned_employee_id",
            "assigned_employee_name",
            "due_at",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields

    @extend_schema_field(serializers.CharField)
    def get_created_by_name(self, obj):
        full_name = obj.created_by.get_full_name().strip()
        return full_name or obj.created_by.email

    @extend_schema_field(serializers.CharField(allow_null=True))
    def get_assigned_employee_name(self, obj):
        if not obj.assigned_employee:
            return None

        full_name = obj.assigned_employee.get_full_name().strip()
        return full_name or obj.assigned_employee.email


class TicketListDataSerializer(serializers.Serializer):
    count = serializers.IntegerField()
    next = serializers.URLField(allow_null=True)
    previous = serializers.URLField(allow_null=True)
    results = TicketListSerializer(many=True)


class TicketListResponseSerializer(serializers.Serializer):
    success = serializers.BooleanField()
    message = serializers.CharField()
    data = TicketListDataSerializer()

