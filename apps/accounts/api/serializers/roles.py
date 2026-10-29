import uuid
from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from collections.abc import Mapping
import re
from apps.accounts.models import Role
from apps.accounts.validators import validate_role_name

class RoleListSerializer(serializers.ModelSerializer):
    class Meta:
        model = Role
        fields = [
            "id",
            "name",
            "code",
            "description",
            "is_system_role",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields 

#for role list pagination/ search / filter/ sort cleanup
class RoleListDataSerializer(serializers.Serializer):
    count = serializers.IntegerField()
    next = serializers.URLField(allow_null=True)
    previous = serializers.URLField(allow_null=True)
    results = RoleListSerializer(many=True)

class RoleListResponseSerializer(serializers.Serializer):
    success = serializers.BooleanField()
    message = serializers.CharField()
    data = RoleListDataSerializer()


ROLE_CODE_REGEX = re.compile(r"^[A-Z]+$")

class CreateRoleListSerializer(serializers.ListSerializer):
    def validate(self, attrs):
        if not attrs:
            raise serializers.ValidationError(
                "At least one role is required."
            )

        names = [item["name"].strip().lower() for item in attrs]
        if len(names) != len(set(names)):
            raise serializers.ValidationError(
                "Duplicate role names are not allowed in the same request."
            )

        codes = [item["code"].strip().upper() for item in attrs]
        if len(codes) != len(set(codes)):
            raise serializers.ValidationError(
                "Duplicate role codes are not allowed in the same request."
            )

        return attrs

class CreateRoleSerializer(serializers.ModelSerializer):
    code = serializers.CharField(
        max_length=50,
        required=True,
        allow_blank=False,
    )

    class Meta:
        model = Role
        fields = [
            "name",
            "code",
            "description",
        ]
        list_serializer_class = CreateRoleListSerializer

    def to_internal_value(self, data):
        if not isinstance(data, Mapping):
            raise serializers.ValidationError({
                "non_field_errors": ["Invalid data. Expected a JSON object."]
            })
        allowed_fields = set(self.fields.keys())
        unknown_fields = set(data.keys()) - allowed_fields
        if unknown_fields:
            raise serializers.ValidationError({
                field: "This field is not allowed during role creation."
                for field in sorted(unknown_fields)
            })
        return super().to_internal_value(data)

    def validate_name(self, value):
        name = validate_role_name(value)
        if Role.objects.filter(name__iexact=name).exists():
            raise serializers.ValidationError(
                "A role with this name already exists."
            )
        return name
    
    def validate_code(self, value):
        code = value.strip()
        if not ROLE_CODE_REGEX.match(code):
            raise serializers.ValidationError(
                "Role code must be a single word in ALL CAPS (letters A-Z only)."
            )
        if Role.objects.filter(code__iexact=code).exists():
            raise serializers.ValidationError(
                "A role with this code already exists."
            )
        return code
    
    def validate_description(self, value):
        return value.strip() if value else value


class CreateRoleResponseSerializer(serializers.Serializer):
    success = serializers.BooleanField()
    message = serializers.CharField()
    data = RoleListSerializer(many=True)


class UpdateRoleSerializer(serializers.ModelSerializer):
    class Meta:
        model = Role
        fields = [
            "name",
            "description",
        ]

    def to_internal_value(self, data):
        if not isinstance(data, Mapping):
            raise serializers.ValidationError({
                "non_field_errors": ["Invalid data. Expected a JSON object."]
            })

        if "code" in data:
            raise serializers.ValidationError({
                "code": "Role code cannot be modified after creation."
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
        name = validate_role_name(value)

        if Role.objects.filter(
            name__iexact=name
        ).exclude(id=self.instance.id).exists():
            raise serializers.ValidationError(
                "A role with this name already exists."
            )

        return name

    def validate_description(self, value):
        return value.strip() if value else value

    def validate(self, attrs):
        if not attrs:
            raise serializers.ValidationError(
                "At least one field ('name' or 'description') is required to update."
            )
        return attrs
    


class UpdateRoleResponseSerializer(serializers.Serializer):
    success = serializers.BooleanField()
    message = serializers.CharField()
    data = RoleListSerializer()


class RoleDetailResponseSerializer(serializers.Serializer):
    success = serializers.BooleanField()
    message = serializers.CharField()
    data = RoleListSerializer()


class DeleteRoleSerializer(serializers.Serializer):
    ids = extend_schema_field(
        serializers.ListField(child=serializers.UUIDField())
    )(serializers.JSONField(required=False))
    all = serializers.BooleanField(required=False, default=False)

    def to_internal_value(self, data):
        if not isinstance(data, Mapping):
            raise serializers.ValidationError({
                "non_field_errors": ["Invalid data. Expected a JSON object."]
            })

        allowed_fields = set(self.fields.keys())
        unknown_fields = set(data.keys()) - allowed_fields

        if unknown_fields:
            raise serializers.ValidationError({
                field: "This field is not allowed during role deletion."
                for field in sorted(unknown_fields)
            })
        
        return super().to_internal_value(data)

    def validate(self, attrs):
        ids = attrs.get("ids")
        delete_all = attrs.get("all", False)

        if delete_all and ids is not None:
            raise serializers.ValidationError(
                "Use either 'ids' or 'all', not both."
            )

        if not delete_all and ids is None:
            raise serializers.ValidationError(
                "Either 'ids' or 'all' is required."
            )

        if delete_all:
            return attrs

        if isinstance(ids, str):
            ids = [ids]

        if not isinstance(ids, list) or not ids:
            raise serializers.ValidationError(
                "ids must be a UUID or a list of UUIDs."
            )

        validated_ids = []

        for index, role_id in enumerate(ids):
            try:
                validated_ids.append(uuid.UUID(str(role_id)))
            except (ValueError, AttributeError, TypeError):
                raise serializers.ValidationError(
                    f"Invalid role ID at position {index}: {role_id}"
                )

        attrs["ids"] = list(dict.fromkeys(validated_ids))

        return attrs
        

class RoleDeleteDataSerializer(serializers.Serializer):
    deleted_ids = serializers.ListField(
        child=serializers.UUIDField()
    )

class RoleDeleteResponseSerializer(serializers.Serializer):
    success = serializers.BooleanField()
    message = serializers.CharField()
    data = RoleDeleteDataSerializer()