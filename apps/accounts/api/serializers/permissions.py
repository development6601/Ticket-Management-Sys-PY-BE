import uuid
from collections.abc import Mapping
from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from apps.accounts.models import Permission
from apps.accounts.validators import (
    validate_permission_name,
    validate_identifier_name,
)

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
        if not attrs:
            raise serializers.ValidationError('At least one permission is required.')

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
        name = validate_permission_name(value)

        if Permission.objects.filter(
            name__iexact=name
        ).exists():
            raise serializers.ValidationError(
                "A permission with this name already exists."
            )

        return name 

    def validate_module(self, value):
        return validate_identifier_name(value, field_name="Module")

    def validate_action(self, value):
        return validate_identifier_name(value, field_name="Action")

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
        name = validate_permission_name(value)

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
        return validate_identifier_name(value, field_name="Module")

    def validate_action(self, value):
        return validate_identifier_name(value, field_name="Action")

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

        if "ids" in data:
            if isinstance(data["ids"], uuid.UUID):
                data = dict(data)
                data["ids"] = str(data["ids"])
            elif isinstance(data["ids"], list):
                data = dict(data)
                data["ids"] = [str(x) if isinstance(x, uuid.UUID) else x for x in data["ids"]]

        return super().to_internal_value(data)

    def validate_ids(self, value):
        if isinstance(value, (str, uuid.UUID)):
            value = [value]

        if not isinstance(value, list) or not value:
            raise serializers.ValidationError(
                "ids must be a UUID or a non-empty list of UUIDs."
            )

        validated_ids = []

        for index, permission_id in enumerate(value):
            try: 
                validated_ids.append(
                    str(uuid.UUID(str(permission_id).strip()))
                )
            except (ValueError, AttributeError, TypeError):
                raise serializers.ValidationError(
                    f"Invalid UUID format at position {index} for permission ID."
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

        data_copy = dict(data)
        changed = False
        for f in ("role_ids", "permission_ids"):
            if f in data_copy:
                if isinstance(data_copy[f], uuid.UUID):
                    data_copy[f] = str(data_copy[f])
                    changed = True
                elif isinstance(data_copy[f], list):
                    data_copy[f] = [str(x) if isinstance(x, uuid.UUID) else x for x in data_copy[f]]
                    changed = True
        if changed:
            data = data_copy

        return super().to_internal_value(data)

    def validate_role_ids(self, value):
        if isinstance(value, (str, uuid.UUID)):
            value = [value]

        if not isinstance(value, list) or not value:
            raise serializers.ValidationError(
                "role_ids must be a UUID or a non-empty list of UUIDs."
            )

        validated = []
        for index, role_id in enumerate(value):
            try:
                validated.append(str(uuid.UUID(str(role_id).strip())))
            except (ValueError, AttributeError, TypeError):
                raise serializers.ValidationError(
                    f"Invalid UUID format at position {index} for role ID."
                )

        return list(dict.fromkeys(validated))

    def validate_permission_ids(self, value):
        if isinstance(value, (str, uuid.UUID)):
            value = [value]

        if not isinstance(value, list) or not value:
            raise serializers.ValidationError(
                "permission_ids must be a UUID or a non-empty list of UUIDs."
            )

        validated = []
        for index, permission_id in enumerate(value):
            try:
                validated.append(str(uuid.UUID(str(permission_id).strip())))
            except (ValueError, AttributeError, TypeError):
                raise serializers.ValidationError(
                    f"Invalid UUID format at position {index} for permission ID."
                )

        return list(dict.fromkeys(validated))

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

        data_copy = dict(data)
        changed = False
        for f in ("role_ids", "permission_ids"):
            if f in data_copy:
                if isinstance(data_copy[f], uuid.UUID):
                    data_copy[f] = str(data_copy[f])
                    changed = True
                elif isinstance(data_copy[f], list):
                    data_copy[f] = [str(x) if isinstance(x, uuid.UUID) else x for x in data_copy[f]]
                    changed = True
        if changed:
            data = data_copy

        return super().to_internal_value(data)

    def validate_role_ids(self, value):
        if isinstance(value, (str, uuid.UUID)):
            value = [value]

        if not isinstance(value, list) or not value:
            raise serializers.ValidationError(
                "role_ids must be a UUID or a non-empty list of UUIDs."
            )

        validated_ids = []
        for index, role_id in enumerate(value):
            try:
                validated_ids.append(str(uuid.UUID(str(role_id).strip())))
            except (ValueError, AttributeError, TypeError):
                raise serializers.ValidationError(
                    f"Invalid UUID format at position {index} for role ID."
                )

        return list(dict.fromkeys(validated_ids))

    def validate_permission_ids(self, value):
        if isinstance(value, (str, uuid.UUID)):
            value = [value]

        if not isinstance(value, list) or not value:
            raise serializers.ValidationError(
                "permission_ids must be a UUID or a non-empty list of UUIDs."
            )

        validated_ids = []
        for index, permission_id in enumerate(value):
            try:
                validated_ids.append(str(uuid.UUID(str(permission_id).strip())))
            except (ValueError, AttributeError, TypeError):
                raise serializers.ValidationError(
                    f"Invalid UUID format at position {index} for permission ID."
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

class UserPermissionBulkSerializer(serializers.Serializer):
    user_ids = extend_schema_field(
        serializers.ListField(
            child=serializers.UUIDField(),
            help_text="One or more target user UUIDs.",
        )
    )(serializers.JSONField())
    permission_ids = extend_schema_field(
        serializers.ListField(
            child=serializers.UUIDField(),
            help_text="One or more permission UUIDs to assign or remove.",
        )
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
                field: "This field is not allowed."
                for field in sorted(unknown_fields)
            })

        data_copy = dict(data)
        for field in ("user_ids", "permission_ids"):
            if field in data_copy:
                val = data_copy[field]
                if isinstance(val, uuid.UUID):
                    data_copy[field] = str(val)
                elif isinstance(val, list):
                    data_copy[field] = [
                        str(item) if isinstance(item, uuid.UUID) else item
                        for item in val
                    ]

        return super().to_internal_value(data_copy)

    @staticmethod
    def validate_ids(value, field_name):
        if isinstance(value, (str, uuid.UUID)):
            value = [value]

        if not isinstance(value, list) or not value:
            raise serializers.ValidationError(
                f"{field_name} must be a UUID or "
                "a non-empty list of UUIDs."
            )

        validated = []

        for index, item in enumerate(value):
            if isinstance(item, uuid.UUID):
                validated.append(item)
                continue

            try:
                validated.append(
                    uuid.UUID(str(item).strip())
                )
            except (ValueError, TypeError, AttributeError):
                raise serializers.ValidationError(
                    f"Invalid UUID format at position {index} for {field_name}."
                )

        return list(dict.fromkeys(validated))

    def validate_user_ids(self, value):
        return self.validate_ids(value, "user_ids")

    def validate_permission_ids(self, value):
        return self.validate_ids(value, "permission_ids")


class UserPermissionResultSerializer(serializers.Serializer):
    user_id = serializers.UUIDField()
    user_email = serializers.EmailField()
    permission_id = serializers.UUIDField()
    permission_name = serializers.CharField()
    permission_code = serializers.CharField()


class UserPermissionSkippedSerializer(
    UserPermissionResultSerializer
):
    reason = serializers.CharField()


class AssignUserPermissionSummarySerializer(serializers.Serializer):
    requested = serializers.IntegerField()
    assigned = serializers.IntegerField()
    already_assigned = serializers.IntegerField()


class RemoveUserPermissionSummarySerializer(serializers.Serializer):
    requested = serializers.IntegerField()
    removed = serializers.IntegerField()
    not_assigned = serializers.IntegerField()


class AssignUserPermissionDataSerializer(serializers.Serializer):
    summary = AssignUserPermissionSummarySerializer()
    assigned = UserPermissionResultSerializer(many=True)
    already_assigned = UserPermissionSkippedSerializer(many=True)


class RemoveUserPermissionDataSerializer(serializers.Serializer):
    summary = RemoveUserPermissionSummarySerializer()
    removed = UserPermissionResultSerializer(many=True)
    not_assigned = UserPermissionSkippedSerializer(many=True)


class AssignUserPermissionResponseSerializer(serializers.Serializer):
    success = serializers.BooleanField()
    message = serializers.CharField()
    data = AssignUserPermissionDataSerializer()


class RemoveUserPermissionResponseSerializer(serializers.Serializer):
    success = serializers.BooleanField()
    message = serializers.CharField()
    data = RemoveUserPermissionDataSerializer()


class UserPermissionUserSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    email = serializers.EmailField()
    account_status = serializers.CharField()
    is_active = serializers.BooleanField()
    is_primary_contact = serializers.BooleanField()


class UserPermissionDetailDataSerializer(serializers.Serializer):
    user = UserPermissionUserSerializer()
    role_permissions = serializers.ListField(
        child=serializers.CharField()
    )
    direct_permissions = serializers.ListField(
        child=serializers.CharField()
    )
    effective_permissions = serializers.ListField(
        child=serializers.CharField()
    )
    access_enabled = serializers.BooleanField()


class UserPermissionPaginatedDataSerializer(serializers.Serializer):
    count = serializers.IntegerField()
    next = serializers.URLField(allow_null=True)
    previous = serializers.URLField(allow_null=True)
    results = UserPermissionDetailDataSerializer(many=True)


class UserPermissionListResponseSerializer(serializers.Serializer):
    success = serializers.BooleanField()
    message = serializers.CharField()
    data = UserPermissionPaginatedDataSerializer()


class UserPermissionDetailResponseSerializer(serializers.Serializer):
    success = serializers.BooleanField()
    message = serializers.CharField()
    data = UserPermissionDetailDataSerializer()


class RolePermissionItemSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    name = serializers.CharField()
    module = serializers.CharField()
    action = serializers.CharField()
    description = serializers.CharField(allow_null=True)


class RolePermissionRoleSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    name = serializers.CharField()
    code = serializers.CharField()
    description = serializers.CharField(allow_null=True)
    is_system_role = serializers.BooleanField()
    is_assignable = serializers.BooleanField()


class RolePermissionDetailDataSerializer(serializers.Serializer):
    role = RolePermissionRoleSerializer()
    permissions_count = serializers.IntegerField()
    permissions = RolePermissionItemSerializer(many=True)


class RolePermissionDetailResponseSerializer(serializers.Serializer):
    success = serializers.BooleanField()
    message = serializers.CharField()
    data = RolePermissionDetailDataSerializer()


class RolePermissionPaginatedDataSerializer(serializers.Serializer):
    count = serializers.IntegerField()
    next = serializers.URLField(allow_null=True)
    previous = serializers.URLField(allow_null=True)
    results = RolePermissionDetailDataSerializer(many=True)


class RolePermissionListResponseSerializer(serializers.Serializer):
    success = serializers.BooleanField()
    message = serializers.CharField()
    data = RolePermissionPaginatedDataSerializer()
