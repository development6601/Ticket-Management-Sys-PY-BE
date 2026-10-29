import uuid
from collections.abc import Mapping
from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from apps.accounts.models import Permission

class PermissionListSerializer(serializers.ModelSerializer):
    class Meta:
        model = Permission
        fields = [
            "id",
            "name",
            "module",
            "action",
            "description",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields

class CreatePermissionListSerializer(serializers.ListSerializer):
    def validate(self, attrs):
        names = [
            item["name"].lower()
            for item in attrs
        ]

        pairs = [
            (
                item["module"].lower(),
                item["action"].lower(),
            )
            for item in attrs
        ]

        if len(names) != len(set(names)):
            raise serializers.ValidationError(
                "Duplicate permission names are not allowed in the same request."
            )

        if len(pairs) != len(set(pairs)):
            raise serializers.ValidationError(
                "Duplicate module and action combinations are not allowed in the same request."
            )

        return attrs

class CreatePermissionSerializer(serializers.ModelSerializer):
    class Meta:
        model = Permission
        fields = [
            "name",
            "module",
            "action",
            "description",
        ]
        list_serializer_class = CreatePermissionListSerializer

    def to_internal_value(self, data):
        if not isinstance(data, Mapping):
            raise serializers.ValidationError({
                "non_field_errors": ["Invalid data. Expected a JSON object."]
            })

        allowed_fields = set(self.fields.keys())
        unknown_fields = set(data.keys()) - allowed_fields

        if unknown_fields:
            raise serializers.ValidationError({
                field: "This field is not allowed during permission creation."
                for field in sorted(unknown_fields)
            })

        return super().to_internal_value(data)

    def validate_name(self, value):
        name = value.strip()

        if Permission.objects.filter(
            name__iexact=name
        ).exists():
            raise serializers.ValidationError(
                "A permission with this name already exists."
            )

        return name 

    def validate_module(self, value):
        value = value.strip()
        if not value:
            raise serializers.ValidationError("Module cannot be blank.")
        return value

    def validate_action(self, value):
        value = value.strip()
        if not value:
            raise serializers.ValidationError("Action cannot be blank.")
        return value

    def validate_description(self, value):
        return value.strip() if value else value

    def validate(self, attrs):
        module = attrs.get("module", "").strip()
        action = attrs.get("action", "").strip()

        if Permission.objects.filter(
            module__iexact=module,
            action__iexact=action,
        ).exists():
            raise serializers.ValidationError({
                "action": (
                    f"A permission for module '{module}' "
                    f"and action '{action}' already exists."
                )
            })

        return attrs

class UpdatePermissionSerializer(serializers.ModelSerializer):
    class Meta:
        model = Permission
        fields = [
            "name",
            "module",
            "action",
            "description",
        ]

    def to_internal_value(self, data):
        if not isinstance(data, Mapping):
            raise serializers.ValidationError({
                "non_field_errors": ["Invalid data. Expected a JSON object."]
            })

        allowed_fields = set(self.fields.keys())
        unknown_fields = set(data.keys()) - allowed_fields

        if unknown_fields:
            raise serializers.ValidationError({
                field: "This field cannot be updated here."
                for field in sorted(unknown_fields)
            })

        return super().to_internal_value(data)

    def validate_name(self, value):
        name = value.strip()

        if Permission.objects.filter(
            name__iexact=name
        ).exclude(
            id = self.instance.id
        ).exists():
            raise serializers.ValidationError(
                "A permission with this name already exists."
            )

        return name

    def validate_module(self, value):
        value = value.strip()
        if not value:
            raise serializers.ValidationError("Module cannot be blank.")
        return value

    def validate_action(self, value):
        value = value.strip()
        if not value:
            raise serializers.ValidationError("Action cannot be blank.")
        return value

    def validate_description(self, value):
        return value.strip() if value else value

    def validate(self, attrs):
        module = attrs.get(
            "module",
            self.instance.module,
        ).strip()

        action = attrs.get(
            "action",
            self.instance.action,
        ).strip()

        if Permission.objects.filter(
            module__iexact=module,
            action__iexact=action,
        ).exclude(
            id=self.instance.id
        ).exists():
            raise serializers.ValidationError({
                "action": (
                    f"A permission for module '{module}' "
                    f"and action '{action}' already exists."
                )
            })

        return attrs

class DeletePermissionSerializer(serializers.Serializer):
    ids = extend_schema_field(
        serializers.ListField(child=serializers.UUIDField())
    )(serializers.JSONField())

    def to_internal_value(self, data):
        if not isinstance(data, Mapping):
            raise serializers.ValidationError({
                "non_field_errors": ["Invalid data. Expected a JSON object."]
            })

        allowed_fields = set(self.fields.keys())
        unknown_fields = set(data.keys()) - allowed_fields

        if unknown_fields:
            raise serializers.ValidationError({
                field: "This field is not allowed during permission deletion."
                for field in sorted(unknown_fields)
            })

        return super().to_internal_value(data)

    def validate_ids(self, value):
        if isinstance(value, str):
            value = [value]

        if not isinstance(value, list):
            raise serializers.ValidationError(
                "ids must be a UUID or a list of UUIDs."
            )

        if not value:
            raise serializers.ValidationError(
                "At least one permission ID is required."
            )

        validated_ids = []

        for index, permission_id in enumerate(value):
            try: 
                validated_ids.append(
                    str(uuid.UUID(str(permission_id)))
                )
            except (ValueError, AttributeError, TypeError):
                raise serializers.ValidationError(
                    f"Invalid permission ID at position {index}: {permission_id}"
                )

        #duplicate
        return list(dict.fromkeys(validated_ids))


class DeletePermissionDataSerializer(serializers.Serializer):
    deleted_ids = serializers.ListField(
        child=serializers.UUIDField()
    )

class DeletePermissionResponseSerializer(serializers.Serializer):
    success = serializers.BooleanField()
    message = serializers.CharField()
    data = DeletePermissionDataSerializer()

class PermissionListDataSerializer(serializers.Serializer):
    count = serializers.IntegerField()
    next = serializers.URLField(allow_null=True)
    previous = serializers.URLField(allow_null=True)
    results = PermissionListSerializer(many=True)

#for get method
class PermissionListResponseSerializer(serializers.Serializer):
    success = serializers.BooleanField()
    message = serializers.CharField()
    data = PermissionListDataSerializer()

#for post/patch
class PermissionResponseSerializer(serializers.Serializer):
    success = serializers.BooleanField()
    message = serializers.CharField()
    data = PermissionListSerializer()


class PermissionBulkResponseSerializer(serializers.Serializer):
    success = serializers.BooleanField()
    message = serializers.CharField()
    data = PermissionListSerializer(many=True)


class PermissionDetailResponseSerializer(serializers.Serializer):
    success = serializers.BooleanField()
    message = serializers.CharField()
    data = PermissionListSerializer()


    

class AssignPermissionToRoleSerializer(serializers.Serializer):
    role_ids = extend_schema_field(
        serializers.ListField(child=serializers.UUIDField())
    )(serializers.JSONField())
    permission_ids = extend_schema_field(
        serializers.ListField(child=serializers.UUIDField())
    )(serializers.JSONField())

    def to_internal_value(self, data):
        if not isinstance(data, Mapping):
            raise serializers.ValidationError({
                "non_field_errors": ["Invalid data. Expected a JSON object."]
            })

        allowed_fields = set(self.fields.keys())
        unknown_fields = set(data.keys()) - allowed_fields

        if unknown_fields:
            raise serializers.ValidationError({
                field: "This field is not allowed during permission assignment."
                for field in sorted(unknown_fields)
            })

        return super().to_internal_value(data)

    def validate_role_ids(self, value):
        if isinstance(value, str):
            value = [value]

        if not isinstance(value, list):
            raise serializers.ValidationError(
                "role_ids must be a UUID or a list of UUIDs."
            )

        if not value:
            raise serializers.ValidationError(
                "At least one role is required."
            )

        try : 
            value = [
                str(uuid.UUID(str(role_id)))
                for role_id in value
            ]
        except (ValueError, AttributeError, TypeError):
            raise serializers.ValidationError(
                "There are one or more invalid role IDs."
            )

        #remove duplicate role ids
        return list(dict.fromkeys(value))


    def validate_permission_ids(self, value):
        # Support single UUID
        if isinstance(value, str):
            value = [value]

        # Must be a list after conversion
        if not isinstance(value, list):
            raise serializers.ValidationError(
                "permission_ids must be a UUID or a list of UUIDs."
            )

        if not value:
            raise serializers.ValidationError(
                "At least one permission is required."
            )

        try:
            value = [
                str(uuid.UUID(str(permission_id)))
                for permission_id in value
            ]
        except (ValueError, AttributeError, TypeError):
            raise serializers.ValidationError(
                "One or more permission IDs are invalid."
            )

        # Remove duplicate IDs from request itself
        return list(dict.fromkeys(value))

class RolePermissionResultSerializer(serializers.Serializer):
    role_id = serializers.UUIDField()
    role_name = serializers.CharField()
    permission_id = serializers.UUIDField()
    permission_name = serializers.CharField()

class RolePermissionSkippedSerializer(RolePermissionResultSerializer):
    reason = serializers.CharField()

class RolePermissionSummarySerializer(serializers.Serializer):
    requested = serializers.IntegerField()
    assigned = serializers.IntegerField()
    already_assigned = serializers.IntegerField()

class AssignRolePermissionDataSerializer(serializers.Serializer):
    summary = RolePermissionSummarySerializer()
    assigned = RolePermissionResultSerializer(many=True)
    already_assigned = RolePermissionSkippedSerializer(many=True)

class AssignRolePermissionResponseSerializer(serializers.Serializer):
    success = serializers.BooleanField()
    message = serializers.CharField()
    data = AssignRolePermissionDataSerializer()



class RemovePermissionFromRoleSerializer(serializers.Serializer):
    role_ids = extend_schema_field(
        serializers.ListField(child=serializers.UUIDField())
    )(serializers.JSONField())
    permission_ids = extend_schema_field(
        serializers.ListField(child=serializers.UUIDField())
    )(serializers.JSONField())

    def to_internal_value(self, data):
        if not isinstance(data, Mapping):
            raise serializers.ValidationError({
                "non_field_errors": ["Invalid data. Expected a JSON object."]
            })

        allowed_fields = set(self.fields.keys())
        unknown_fields = set(data.keys()) - allowed_fields

        if unknown_fields:
            raise serializers.ValidationError({
                field: "This field is not allowed during permission removal."
                for field in sorted(unknown_fields)
            })

        return super().to_internal_value(data)

    def validate_role_ids(self, value):
        if isinstance(value, str):
            value = [value]

        if not isinstance(value, list):
            raise serializers.ValidationError(
                "role_ids must be a UUID or a list of UUIDs."
            )

        if not value:
            raise serializers.ValidationError(
                "At least one role is required."
            )

        validated_ids = []

        for index, role_id in enumerate(value):
            try:
                validated_ids.append(str(uuid.UUID(str(role_id))))
            except (ValueError, AttributeError, TypeError):
                raise serializers.ValidationError(
                    f"Invalid role ID at position {index}: {role_id}"
                )

        return list(dict.fromkeys(validated_ids))

    def validate_permission_ids(self, value):
        if isinstance(value, str):
            value = [value]

        if not isinstance(value, list):
            raise serializers.ValidationError(
                "permission_ids must be a UUID or a list of UUIDs."
            )

        if not value:
            raise serializers.ValidationError(
                "At least one permission is required."
            )

        validated_ids = []

        for index, permission_id in enumerate(value):
            try:
                validated_ids.append(str(uuid.UUID(str(permission_id))))
            except (ValueError, AttributeError, TypeError):
                raise serializers.ValidationError(
                    f"Invalid permission ID at position {index}: {permission_id}"
                )

        return list(dict.fromkeys(validated_ids))


class RemoveRolePermissionSummarySerializer(serializers.Serializer):
    requested = serializers.IntegerField()
    removed = serializers.IntegerField()
    not_assigned = serializers.IntegerField()

class RemoveRolePermissionDataSerializer(serializers.Serializer):
    summary = RemoveRolePermissionSummarySerializer()
    removed = RolePermissionResultSerializer(many=True)
    not_assigned = RolePermissionSkippedSerializer(many=True)


class RemoveRolePermissionResponseSerializer(serializers.Serializer):
    success = serializers.BooleanField()
    message = serializers.CharField()
    data = RemoveRolePermissionDataSerializer()