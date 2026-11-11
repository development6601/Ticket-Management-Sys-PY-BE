from collections.abc import Mapping

from rest_framework import serializers


class TicketAssignmentRequestSerializer(serializers.Serializer):
    action = serializers.ChoiceField(
        choices=("assign", "reassign", "unassign", "transfer")
    )
    employee_id = serializers.UUIDField(
        required=False,
        allow_null=True,
    )
    team_id = serializers.UUIDField(
        required=False,
        allow_null=True,
    )
    reason = serializers.CharField(
        required=False,
        allow_blank=True,
        allow_null=True,
        max_length=2000,
    )

    def to_internal_value(self, data):
        if not isinstance(data, Mapping):
            raise serializers.ValidationError({
                "non_field_errors": [
                    "Invalid data. Expected a JSON object."
                ]
            })

        allowed_fields = {
            "action",
            "employee_id",
            "team_id",
            "reason",
        }
        unknown_fields = set(data.keys()) - allowed_fields

        if unknown_fields:
            raise serializers.ValidationError({
                field: ["This field is not allowed."]
                for field in sorted(unknown_fields)
            })

        return super().to_internal_value(data)

    def validate(self, attrs):
        action = attrs["action"]
        employee_id = attrs.get("employee_id")
        team_id = attrs.get("team_id")

        if action in ("assign", "reassign") and not employee_id:
            raise serializers.ValidationError({
                "employee_id": "An employee is required."
            })

        if action in ("reassign", "transfer") and not attrs.get("reason", "").strip():
            raise serializers.ValidationError({
                "reason": "A reason is required."
            })

        if action == "unassign" and employee_id:
            raise serializers.ValidationError({
                "employee_id": "Unassign does not accept an employee."
            })

        if action == "transfer" and not team_id:
            raise serializers.ValidationError({
                "team_id": "A destination team is required."
            })

        if action != "transfer" and team_id:
            raise serializers.ValidationError({
                "team_id": "Only transfer accepts a team ID."
            })

        return attrs


# ---------------------------------------------------------------------------
# Response serializers used for Swagger documentation only.
# ---------------------------------------------------------------------------

class _AssignedTeamSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    name = serializers.CharField()


class _AssignedEmployeeSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    email = serializers.EmailField()


class TicketAssignmentResponseDataSerializer(serializers.Serializer):
    ticket_id = serializers.UUIDField()
    ticket_number = serializers.CharField()
    assigned_team = _AssignedTeamSerializer(allow_null=True)
    assigned_employee = _AssignedEmployeeSerializer(allow_null=True)


class TicketAssignmentResponseSerializer(serializers.Serializer):
    success = serializers.BooleanField()
    message = serializers.CharField()
    data = TicketAssignmentResponseDataSerializer()
