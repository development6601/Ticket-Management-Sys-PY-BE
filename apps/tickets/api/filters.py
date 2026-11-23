
from django.db.models import Q

from rest_framework import serializers
from rest_framework.pagination import PageNumberPagination

from apps.tickets.models import Ticket


# ---------------------------------------------------------------------------
# Ticket list pagination.
# Follows the same pagination convention as TicketCategoryListView.
# ---------------------------------------------------------------------------

class TicketPagination(PageNumberPagination):
    page_size = 20
    page_size_query_param = "page_size"
    max_page_size = 100


# ---------------------------------------------------------------------------
# Allowed ordering fields.
# Never pass an unrestricted client-provided ordering field to order_by().
# ---------------------------------------------------------------------------

TICKET_ORDERING_FIELDS = {
    "created_at",
    "-created_at",
    "updated_at",
    "-updated_at",
    "due_at",
    "-due_at",
    "priority",
    "-priority",
    "ticket_number",
    "-ticket_number",
    "status__name",
    "-status__name",
}


# ---------------------------------------------------------------------------
# Validate ticket list query parameters.
# UUID filters are used for related objects.
# Date filters accept ISO 8601 datetime values.
# ---------------------------------------------------------------------------

class TicketListFilterSerializer(serializers.Serializer):

    search = serializers.CharField(
        required=False,
        allow_blank=True,
        trim_whitespace=True,
    )

    status = serializers.UUIDField(required=False)
    priority = serializers.ChoiceField(
        choices=Ticket.Priority.choices,
        required=False,
    )
    category = serializers.UUIDField(required=False)
    assigned_employee = serializers.UUIDField(required=False)
    team = serializers.UUIDField(required=False)
    customer = serializers.UUIDField(required=False)

    created_from = serializers.DateTimeField(required=False)
    created_to = serializers.DateTimeField(required=False)

    updated_from = serializers.DateTimeField(required=False)
    updated_to = serializers.DateTimeField(required=False)

    due_from = serializers.DateTimeField(required=False)
    due_to = serializers.DateTimeField(required=False)

    ordering = serializers.CharField(
        required=False,
        default="-created_at",
    )

    page = serializers.IntegerField(
        required=False,
        min_value=1,
        default=1,
    )

    page_size = serializers.IntegerField(
        required=False,
        min_value=1,
        max_value=TicketPagination.max_page_size,
        default=TicketPagination.page_size,
    )

    def to_internal_value(self, data):
        """
        Reject unknown query parameters rather than silently ignoring
        misspelled filter names.
        """

        allowed_fields = set(self.fields.keys())
        unknown_fields = set(data.keys()) - allowed_fields

        if unknown_fields:
            raise serializers.ValidationError({
                field: "Unsupported ticket filter."
                for field in sorted(unknown_fields)
            })

        return super().to_internal_value(data)

    def validate_ordering(self, value):
        """
        Support comma-separated ordering fields, for example:
        -created_at,priority
        """

        ordering_fields = [
            field.strip()
            for field in value.split(",")
            if field.strip()
        ]

        if not ordering_fields:
            raise serializers.ValidationError(
                "At least one ordering field is required."
            )

        invalid_fields = [
            field
            for field in ordering_fields
            if field not in TICKET_ORDERING_FIELDS
        ]

        if invalid_fields:
            raise serializers.ValidationError(
                "Invalid ordering field(s): "
                + ", ".join(invalid_fields)
            )

        return ordering_fields

    def validate(self, attrs):

        date_ranges = (
            ("created_from", "created_to"),
            ("updated_from", "updated_to"),
            ("due_from", "due_to"),
        )

        for start_field, end_field in date_ranges:
            start_date = attrs.get(start_field)
            end_date = attrs.get(end_field)

            if (
                start_date is not None
                and end_date is not None
                and start_date > end_date
            ):
                raise serializers.ValidationError({
                    end_field: (
                        f"{end_field} must be greater than "
                        f"or equal to {start_field}."
                    )
                })

        return attrs


# ---------------------------------------------------------------------------
# Apply validated filters to an already visibility-scoped queryset.
#
# IMPORTANT:
# This function never starts from Ticket.objects.all().
# The caller must pass visible_tickets_queryset(user).
# ---------------------------------------------------------------------------

def filter_tickets(queryset, filters):

    # Search ticket number, subject, customer company name and email.
    search = filters.get("search", "").strip()

    if search:
        queryset = queryset.filter(
            Q(ticket_number__icontains=search)
            | Q(subject__icontains=search)
            | Q(customer__company_name__icontains=search)
            | Q(customer__email__icontains=search)
        )

    # Related object filters.
    if "status" in filters:
        queryset = queryset.filter(
            status_id=filters["status"]
        )

    if "priority" in filters:
        queryset = queryset.filter(
            priority=filters["priority"]
        )

    if "category" in filters:
        queryset = queryset.filter(
            category_id=filters["category"]
        )

    if "assigned_employee" in filters:
        queryset = queryset.filter(
            assigned_employee_id=filters["assigned_employee"]
        )

    if "team" in filters:
        queryset = queryset.filter(
            assigned_team_id=filters["team"]
        )

    if "customer" in filters:
        queryset = queryset.filter(
            customer_id=filters["customer"]
        )

    # Created date range.
    if "created_from" in filters:
        queryset = queryset.filter(
            created_at__gte=filters["created_from"]
        )

    if "created_to" in filters:
        queryset = queryset.filter(
            created_at__lte=filters["created_to"]
        )

    # Updated date range.
    if "updated_from" in filters:
        queryset = queryset.filter(
            updated_at__gte=filters["updated_from"]
        )

    if "updated_to" in filters:
        queryset = queryset.filter(
            updated_at__lte=filters["updated_to"]
        )

    # Due date range.
    # Tickets without a due date are naturally excluded when a due
    # date filter is applied.
    if "due_from" in filters:
        queryset = queryset.filter(
            due_at__gte=filters["due_from"]
        )

    if "due_to" in filters:
        queryset = queryset.filter(
            due_at__lte=filters["due_to"]
        )

    return queryset
