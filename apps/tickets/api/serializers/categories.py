import re
from collections.abc import Mapping

from django.db.models.functions import Lower
from rest_framework import serializers

from apps.tickets.models import TicketCategory

class TicketCategorySerializer(serializers.ModelSerializer):
    class Meta:
        model = TicketCategory
        fields = [
            "id",
            "name",
            "description",
            "is_active",
            "position",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "id",
            "position",
            "created_at",
            "updated_at",
        ]
        extra_kwargs = {
            "name": {
                "required": True,
                "allow_blank": False,
                "max_length": 100,
            },
            "description": {
                "required": False,
                "allow_blank": True,
                "max_length": 2000,
            },
            "is_active": {
                "required": False,
                "default": True,
            },
        }

    def to_internal_value(self, data):
        if not isinstance(data, Mapping):
            raise serializers.ValidationError({
                "non_field_errors": [
                    "Invalid data. Expected a JSON object."
                ]
            })

        read_only = set(self.Meta.read_only_fields)
        supplied_read_only = set(data.keys()) & read_only
        if supplied_read_only:
            raise serializers.ValidationError({
                field: ["This field is read-only and cannot be modified."]
                for field in sorted(supplied_read_only)
            })

        allowed_fields = set(self.fields.keys()) - read_only
        unknown_fields = set(data.keys()) - allowed_fields - read_only
        if unknown_fields:
            raise serializers.ValidationError({
                field: ["This field is not allowed."]
                for field in sorted(unknown_fields)
            })

        if (
            "name" in data
            and data["name"] is not None
            and not isinstance(data["name"], str)
        ):
            raise serializers.ValidationError({
                "name": ["Category name must be a string."]
            })

        if "description" in data and data["description"] is None:
            raise serializers.ValidationError({
                "description": ["This field may not be null."]
            })

        if (
            "description" in data
            and not isinstance(data["description"], str)
        ):
            raise serializers.ValidationError({
                "description": ["Description must be a string."]
            })

        if "is_active" in data and type(data["is_active"]) is not bool:
            raise serializers.ValidationError({
                "is_active": [
                    "This field must be a JSON boolean (true or false)."
                ]
            })

        return super().to_internal_value(data)

    def validate_name(self, value):
        name = re.sub(r"\s+", " ", value).strip()

        if not name:
            raise serializers.ValidationError(
                "Category name cannot be empty."
            )

        if not any(char.isalnum() for char in name):
            raise serializers.ValidationError(
                "Category name must contain a letter or number."
            )

        if name.isdigit() or not any(char.isalpha() for char in name):
            raise serializers.ValidationError(
                "Category name must contain at least one letter and cannot be only numbers."
            )

        customer = self.context["customer"]

        queryset = TicketCategory.objects.filter(
            customer=customer,
            name__iexact=name,
        )

        if self.instance:
            queryset = queryset.exclude(pk=self.instance.pk)

        if queryset.exists():
            raise serializers.ValidationError(
                "A category with this name already exists for this company."
            )

        return name

    def validate_description(self, value):
        description = re.sub(r"\s+", " ", value).strip()
        if description and (
            description.isdigit()
            or not any(char.isalpha() for char in description)
        ):
            raise serializers.ValidationError(
                "Description must contain at least one letter and cannot be only numbers."
            )
        return description

    def validate(self, attrs):
        if self.instance is not None and not attrs:
            raise serializers.ValidationError(
                "At least one field ('name', 'description', or 'is_active') is required to update."
            )
        return attrs


class TicketCategoryResponseSerializer(serializers.Serializer):
    success = serializers.BooleanField()
    message = serializers.CharField()
    data = TicketCategorySerializer()


class TicketCategoryListDataSerializer(serializers.Serializer):
    count = serializers.IntegerField()
    next = serializers.URLField(allow_null=True)
    previous = serializers.URLField(allow_null=True)
    results = TicketCategorySerializer(many=True)


class TicketCategoryListResponseSerializer(serializers.Serializer):
    success = serializers.BooleanField()
    message = serializers.CharField()
    data = TicketCategoryListDataSerializer()


class TicketCategoryDeleteDataSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    name = serializers.CharField()


class TicketCategoryDeleteResponseSerializer(serializers.Serializer):
    success = serializers.BooleanField()
    message = serializers.CharField()
    data = TicketCategoryDeleteDataSerializer()


class TicketCategoryErrorResponseSerializer(serializers.Serializer):
    success = serializers.BooleanField(default=False)
    message = serializers.CharField()
    data = serializers.JSONField(allow_null=True, default=None)
