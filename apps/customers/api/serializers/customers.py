from collections.abc import Mapping

from rest_framework import serializers
from rest_framework.exceptions import PermissionDenied

from apps.customers.models import Customer, normalize_company_name, CustomerUser
from apps.accounts.models import User, UserRole
from apps.accounts.validators import (
    PhoneNumberField,
    resolve_region_from_country,
    normalize_phone_number,
)

import phonenumbers

class CustomerAccountManagerSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    email = serializers.EmailField()
    first_name = serializers.CharField()
    last_name = serializers.CharField()

class CustomerListSerializer(serializers.ModelSerializer):
    account_manager = CustomerAccountManagerSerializer(read_only=True)

    class Meta:
        model = Customer
        fields = [
            "id",
            "company_name",
            "contact_person",
            "email",
            "phone",
            "address",
            "state",
            "country",
            "is_active",
            "account_status",
            "account_manager",
            "notes",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields

    def to_representation(self, instance):
        data = super().to_representation(instance)
        request = self.context.get("request")

        if request and request.user and request.user.is_authenticated:
            user = request.user
            if not hasattr(self, "_hide_notes"):
                is_admin = (
                    user.is_superuser
                    or UserRole.objects.filter(
                        user=user,
                        role__code="ADMIN",
                    ).exists()
                )
                is_customer_user = CustomerUser.objects.filter(
                    user=user
                ).exists()
                self._hide_notes = (
                    is_customer_user and not is_admin
                )
            if self._hide_notes:
                data.pop("notes", None)

        return data

class CustomerListDataSerializer(serializers.Serializer):
    count = serializers.IntegerField()
    next = serializers.URLField(allow_null=True)
    previous = serializers.URLField(allow_null=True)
    results = CustomerListSerializer(many=True)

class CustomerListResponseSerializer(serializers.Serializer):
    success = serializers.BooleanField()
    message = serializers.CharField()
    data = CustomerListDataSerializer()


class CustomerDetailResponseSerializer(serializers.Serializer):
    success = serializers.BooleanField()
    message = serializers.CharField()
    data = CustomerListSerializer()



#for patch
class CustomerUpdateSerializer(serializers.ModelSerializer):
    phone = PhoneNumberField(
        country_field="country",
        required=False,
        max_length=50,
    )

    primary_contact_user_id = serializers.UUIDField(
        required=False,
        write_only=True,
    )

    class Meta:
        model = Customer
        fields = [
            "company_name",
            "contact_person",
            "email",
            "phone",
            "address",
            "state",
            "country",
            "account_manager",
            "notes",
            "primary_contact_user_id",
        ]

    def validate_account_manager(self, value):
        # Allow removing the assigned account manager.
        if value is None:
            return value

        # Account manager must have an active account.
        if (
            not value.is_active
            or value.account_status != User.AccountStatus.ACTIVE
        ):
            raise serializers.ValidationError(
                "Account manager must be an active internal user."
            )

        if not value.email_verified:
            raise serializers.ValidationError(
                "Account manager must have a verified email."
            )

        if UserRole.objects.filter(
            user=value,
            role__code="CUSTOMER",
        ).exists():
            raise serializers.ValidationError(
                "Customer users cannot be assigned as account managers."
            )

        if CustomerUser.objects.filter(user=value).exists():
            raise serializers.ValidationError(
                "A user belonging to a customer company "
                "cannot be assigned as an account manager."
            )

        return value

    def to_internal_value(self, data):
        if not isinstance(data, Mapping):
            raise serializers.ValidationError({
                "non_field_errors": [
                    "Invalid data. Expected a JSON object."
                ]
            })

        # is_active/account_status are handled by the explicit status
        # restriction below, so they are not reported as unknown here.
        allowed_fields = set(self.fields.keys()) | {
            "is_active",
            "account_status",
        }
        invalid_fields = set(data.keys()) - allowed_fields

        if invalid_fields:
            raise serializers.ValidationError({
                field: ["This field cannot be updated here."]
                for field in sorted(invalid_fields)
            })

        self._raw_data = data
        if "is_active" in data or "account_status" in data:
            raise serializers.ValidationError({
                "account_status": (
                    "Customer status must be changed through "
                    "the dedicated admin status action."
                )
            })

        request = self.context.get('request')
        
        if request and request.user and request.user.is_authenticated:
            from apps.customers.models import CustomerUser
            
            is_admin = (
                request.user.is_superuser
                or UserRole.objects.filter(
                    user=request.user,
                    role__code="ADMIN",
                ).exists()
            )
            customer_user = CustomerUser.objects.filter(user=request.user).first()
            is_internal_admin = is_admin and customer_user is None
            forbidden_fields = {
                field: "Only an internal administrator may modify this field."
                for field in ("account_manager", "notes")
                if field in data
            }
            if forbidden_fields and not is_internal_admin:
                raise PermissionDenied(detail=forbidden_fields)
                    
        return super().to_internal_value(data)

    def validate_company_name(self, value):
        normalized = normalize_company_name(value)

        if not normalized:
            raise serializers.ValidationError(
                "Company name is required."
            )

        return normalized

    def validate_contact_person(self, value):
        value = " ".join(value.split())
        if not value:
            raise serializers.ValidationError(
                "Contact person cannot be blank."
            )
        parts = value.split(maxsplit=1)
        if len(parts[0]) > 150:
            raise serializers.ValidationError(
                "First name cannot exceed 150 characters."
            )
        if len(parts) > 1 and len(parts[1]) > 150:
            raise serializers.ValidationError(
                "Last name cannot exceed 150 characters."
            )
        return value

    def validate_primary_contact_user_id(self, value):
        if self.instance is None:
            raise serializers.ValidationError(
                "Customer company is required."
            )

        request = self.context.get("request")
        if request and request.user and request.user.is_authenticated:
            is_admin = (
                request.user.is_superuser
                or UserRole.objects.filter(
                    user=request.user,
                    role__code="ADMIN",
                ).exists()
            )
            actor_membership = (
                CustomerUser.objects
                .filter(user=request.user)
                .first()
            )
            is_primary = bool(
                actor_membership
                and actor_membership.customer_id == self.instance.pk
                and actor_membership.is_primary_contact
            )
            if not is_admin and not is_primary:
                raise PermissionDenied(
                    "Only the current primary contact or an "
                    "internal administrator can transfer "
                    "the primary contact."
                )

        membership = (
            CustomerUser.objects
            .select_related("user")
            .filter(
                customer=self.instance,
                user_id=value,
            )
            .first()
        )

        if membership is None:
            raise serializers.ValidationError(
                "The selected user must belong to this company."
            )

        if membership.is_primary_contact:
            raise serializers.ValidationError(
                "This user is already the primary contact."
            )

        user = membership.user

        if (
            not user.is_active
            or user.account_status != User.AccountStatus.ACTIVE
            or not user.email_verified
            or not user.has_usable_password()
        ):
            raise serializers.ValidationError(
                "The selected user must be active, verified, "
                "and have a configured password."
            )

        return value

    def validate(self, attrs):
        attrs = super().validate(attrs)

        if "primary_contact_user_id" in attrs:
            conflicting_fields = {
                "contact_person",
                "email",
                "phone",
            }.intersection(attrs)

            if conflicting_fields:
                raise serializers.ValidationError({
                    "primary_contact_user_id": (
                        "Transfer the primary contact separately "
                        "from editing contact details."
                    )
                })

        if "email" in attrs:
            requested_email = attrs["email"].strip().lower()
            current_email = (
                self.instance.email.strip().lower()
                if self.instance is not None
                else ""
            )

            if requested_email == current_email:
                raise serializers.ValidationError({
                    "email": "This is already the current email address."
                })

            if set(attrs.keys()) != {"email"}:
                raise serializers.ValidationError({
                    "email": (
                        "Email changes must be requested separately "
                        "from other customer updates."
                    )
                })

            attrs["email"] = requested_email

        if "country" in attrs:
            if not resolve_region_from_country(attrs["country"]):
                raise serializers.ValidationError({
                    "country": ["Unsupported country."]
                })

        if "primary_contact_user_id" in attrs:
            membership = (
                CustomerUser.objects
                .select_related("user")
                .filter(
                    customer=self.instance,
                    user_id=attrs["primary_contact_user_id"],
                )
                .first()
            )
            if membership is not None:
                if not membership.user.phone:
                    raise serializers.ValidationError({
                        "primary_contact_user_id": [
                            "The selected primary contact must have "
                            "a phone number."
                        ]
                    })
                try:
                    normalize_phone_number(membership.user.phone)
                except serializers.ValidationError as exc:
                    raise serializers.ValidationError({
                        "primary_contact_user_id": exc.detail
                    })

        return attrs


class CustomerStatusUpdateSerializer(serializers.Serializer):
    is_active = serializers.BooleanField(
        required=True,
        help_text="Active status of the customer company (true or false).",
    )


class CustomerStatusDataSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    is_active = serializers.BooleanField()
    account_status = serializers.CharField()
    activation_pending = serializers.BooleanField()


class CustomerStatusResponseSerializer(serializers.Serializer):
    success = serializers.BooleanField()
    message = serializers.CharField()
    data = CustomerStatusDataSerializer()


class CustomerEmailChangePendingDataSerializer(serializers.Serializer):
    email_change_pending = serializers.BooleanField()


class CustomerEmailChangePendingResponseSerializer(serializers.Serializer):
    success = serializers.BooleanField()
    message = serializers.CharField()
    data = CustomerEmailChangePendingDataSerializer()
