from django.contrib.auth import password_validation
from rest_framework import serializers

from apps.accounts.models import User, UserRole
from apps.customers.models import Customer, normalize_company_name, CustomerUser
from apps.accounts.api.serializers.users import (
    UserResponseSerializer,
    RegisterUserResponseSerializer,
    RegisterCustomerResponseSerializer,
)
from drf_spectacular.utils import extend_schema_field

from apps.accounts.validators import PhoneNumberField, normalize_phone_number
from django.core.exceptions import ValidationError as DjangoValidationError

from collections.abc import Mapping

from rest_framework.exceptions import AuthenticationFailed
from rest_framework_simplejwt.serializers import TokenRefreshSerializer
from rest_framework_simplejwt.tokens import RefreshToken
from rest_framework_simplejwt.exceptions import TokenError

class RegisterSerializer(serializers.ModelSerializer):
    password = serializers.CharField(
        write_only = True,
        min_length = 8,
        trim_whitespace=False,
        style={"input_type":"password"},
    )
    password_confirm = serializers.CharField(
        write_only = True,
        min_length = 8,
        trim_whitespace=False,
        style={"input_type": "password"},
    )

    customer = serializers.CharField(
        write_only=True,
        max_length=255,
        trim_whitespace=True,
        allow_blank=False,
    )
    contact_person = serializers.CharField(
        write_only=True,
        max_length=255,
        trim_whitespace=True,
        allow_blank=False,
    )

    phone = PhoneNumberField(
        write_only=True,
        required=True,
        max_length=50,
        allow_blank=False,
        country_field="country",
    )
    address = serializers.CharField(
        write_only=True,
        max_length=512,
    )
    state = serializers.CharField(
        write_only=True,
        max_length=100,
    )
    country = serializers.CharField(
        write_only=True,
        max_length=100,
    )

    class Meta:
        model = User
        fields = [
            "id",
            "email",
            "customer",
            "contact_person",
            "password",
            "password_confirm",
            "phone",
            "address",
            "state",
            "country",
        ]
        read_only_fields = ["id"]

    def validate_email(self, value):
        email = value.strip().lower()

        if User.objects.filter(email__iexact=email).exists():
            raise serializers.ValidationError(
                "An account with this email already exists."
            )

        if Customer.all_objects.filter(
            email__iexact=email
        ).exists():
            raise serializers.ValidationError(
                "An account with this email already exists."
            )

        return email

    def validate_customer(self, value):
        normalized = normalize_company_name(value)

        if not normalized:
            raise serializers.ValidationError(
                "Customer or Company name is required."
            )

        return normalized

    def to_internal_value(self, data):
        if not isinstance(data, Mapping):
            raise serializers.ValidationError({
                "non_field_errors": [
                    "Invalid data. Expected a JSON object."
                ]
            })

        self._raw_data = data

        allowed_fields = set(self.fields.keys()) - {"id"}
        unknown_fields = set(data.keys()) - allowed_fields

        if unknown_fields:
            raise serializers.ValidationError({
                field: "This field is not allowed during registration."
                for field in sorted(unknown_fields)
            })
        
        return super().to_internal_value(data)

    def validate(self, attrs):

        # Public registration always creates a CUSTOMER.
        # Do not allow the client to provide a role.
        protected_fields = {
            "role",
            "role_id",
            "is_staff",
            "is_superuser",
            "is_active",
            "email_verified",
            "is_primary_contact",
            "customer_id",

            #old field
            "company_name",
            "first_name",
            "last_name",
        }

        submitted_fields = protected_fields.intersection(
            self.initial_data.keys()
        )

        if submitted_fields:
            raise serializers.ValidationError({
                field: "This field is not allowed during registration."
                for field in sorted(submitted_fields)
            })
        

        if attrs["password"] != attrs["password_confirm"]:
            raise serializers.ValidationError({
                "password_confirm": "Passwords do not match."
            })

        attrs["customer"] = normalize_company_name(attrs["customer"])
        attrs["contact_person"] = attrs["contact_person"].strip()
        attrs["phone"] = normalize_phone_number(
            attrs["phone"],
            country=attrs.get("country"),
        )

        if not attrs["customer"]:
            raise serializers.ValidationError({
                "customer": "Customer or Company name is required."
            })

        if not attrs["contact_person"]:
            raise serializers.ValidationError({
                "contact_person": "Contact person is required."
            })

        name_parts = attrs["contact_person"].strip().split(maxsplit=1)

        temporary_user = User(
            email=attrs["email"],
            first_name=name_parts[0],
            last_name=name_parts[1] if len(name_parts) > 1 else "",
        )

        try:
            password_validation.validate_password(
                attrs["password"],
                user=temporary_user,
            )
        except Exception as exc:

            if not isinstance(exc, DjangoValidationError):
                raise

            raise serializers.ValidationError({
                "password": exc.messages
            })

        return attrs


class RegisterDataSerializer(serializers.Serializer):
    customer = RegisterCustomerResponseSerializer()
    primary_user = RegisterUserResponseSerializer()
    next_step = serializers.CharField(read_only=True)


class RegisterResponseSerializer(serializers.Serializer):
    success = serializers.BooleanField()
    message = serializers.CharField()
    data = RegisterDataSerializer()


#email verification
class VerifyEmailSerializer(serializers.Serializer):
    token = serializers.CharField(
        required=True,
        write_only=True,
    )

    def to_internal_value(self, data):
        if not isinstance(data, Mapping):
            raise serializers.ValidationError({
                "non_field_errors": [
                    "Invalid data. Expected a JSON object."
                ]
            })

        allowed_fields = set(self.fields.keys())
        unknown_fields = set(data.keys()) - allowed_fields

        if unknown_fields:
            raise serializers.ValidationError({
                field: ["This field is not allowed."]
                for field in sorted(unknown_fields)
            })

        return super().to_internal_value(data)

class VerifyEmailResponseDataSerializer(serializers.Serializer):
    access = serializers.CharField()
    refresh = serializers.CharField()
    user = UserResponseSerializer()


class VerifyEmailPasswordSetupDataSerializer(serializers.Serializer):
    password_setup_required = serializers.BooleanField()
    user = UserResponseSerializer()


class VerifyEmailResponseSerializer(serializers.Serializer):
    success = serializers.BooleanField()
    message = serializers.CharField()
    data = VerifyEmailResponseDataSerializer()


class VerifyEmailPasswordSetupResponseSerializer(serializers.Serializer):
    success = serializers.BooleanField()
    message = serializers.CharField()
    data = VerifyEmailPasswordSetupDataSerializer()


# resend verification

class ResendVerificationSerializer(serializers.Serializer):
    email = serializers.EmailField()

    def to_internal_value(self, data):
        if not isinstance(data, Mapping):
            raise serializers.ValidationError({
                "non_field_errors": [
                    "Invalid data. Expected a JSON object."
                ]
            })

        allowed_fields = set(self.fields.keys())
        unknown_fields = set(data.keys()) - allowed_fields

        if unknown_fields:
            raise serializers.ValidationError({
                field: ["This field is not allowed."]
                for field in sorted(unknown_fields)
            })

        return super().to_internal_value(data)

    def validate_email(self, value):
        return value.strip().lower()

class ResendVerificationResponseSerializer(serializers.Serializer):
    success = serializers.BooleanField()
    message = serializers.CharField()


#login api serializer

class LoginSerializer(serializers.Serializer):
    email = serializers.EmailField()
    password = serializers.CharField(
        write_only = True,
        trim_whitespace=False,
        style={"input_type": "password"},
    )

    def to_internal_value(self, data):
        if not isinstance(data, Mapping):
            raise serializers.ValidationError({
                "non_field_errors": [
                    "Invalid data. Expected a JSON object."
                ]
            })

        allowed_fields = set(self.fields.keys())
        unknown_fields = set(data.keys()) - allowed_fields

        if unknown_fields:
            raise serializers.ValidationError({
                field: ["This field is not allowed."]
                for field in sorted(unknown_fields)
            })

        return super().to_internal_value(data)

    #validation email to lower  
    def validate_email(self, value):
        return value.strip().lower()

    def validate(self, attrs):
        if not attrs.get("password"):
            raise serializers.ValidationError({
                "password": "Password is required."
            })

        return attrs

#response 
class LoginResponseDataSerializer(serializers.Serializer):
    access = serializers.CharField()
    refresh = serializers.CharField()
    user = UserResponseSerializer()

class LoginResponseSerializer(serializers.Serializer):
    success = serializers.BooleanField()
    message = serializers.CharField()
    data = LoginResponseDataSerializer()


#session management - refresh token renew
class SessionTokenRefreshSerializer(TokenRefreshSerializer):

    def validate(self, attrs):
        try : 
            refresh = RefreshToken(attrs["refresh"])
            user_id = refresh.get("user_id")

        except TokenError:
            raise AuthenticationFailed(
                "Invalid, expired, or revoked refresh token."
            )

        if not user_id:
            raise AuthenticationFailed("Invalid refresh token.")

        user = User.objects.filter(pk=user_id).first()

        if (
            user is None 
            or not user.is_active
            or user.account_status != User.AccountStatus.ACTIVE
        ):
            raise AuthenticationFailed(
                "This account is not active."
            )

        memberships = CustomerUser.objects.filter(
            user=user
        ).select_related("customer")

        for membership in memberships:
            customer = membership.customer

            if (
                customer.deleted_at is not None 
                or not customer.is_active
                or customer.account_status
                != Customer.AccountStatus.ACTIVE
            ):
                raise AuthenticationFailed(
                    "Your customer company is inactive."
                )

            if not user.email_verified:
                raise AuthenticationFailed(
                    "Please verify your email before logging in."
                )

        # Simple JWT handles access-token generation,
        # refresh-token rotation, and old-token blacklisting.
        return super().validate(attrs)

class SessionTokenRefreshDataSerializer(serializers.Serializer):
    access = serializers.CharField()
    refresh = serializers.CharField()


class SessionTokenRefreshResponseSerializer(serializers.Serializer):
    success = serializers.BooleanField()
    message = serializers.CharField()
    data = SessionTokenRefreshDataSerializer()

#logout 
class LogoutSerializer(serializers.Serializer):
    refresh = serializers.CharField(
        write_only=True,
        trim_whitespace=True,
    )

    def to_internal_value(self, data):
        if not isinstance(data, Mapping):
            raise serializers.ValidationError({
                "non_field_errors": [
                    "Invalid data. Expected a JSON object."
                ]
            })

        allowed_fields = set(self.fields.keys())
        unknown_fields = set(data.keys()) - allowed_fields

        if unknown_fields:
            raise serializers.ValidationError({
                field: ["This field is not allowed."]
                for field in sorted(unknown_fields)
            })

        return super().to_internal_value(data)


class LogoutResponseDataSerializer(serializers.Serializer):
    email = serializers.EmailField()


class LogoutResponseSerializer(serializers.Serializer):
    success = serializers.BooleanField()
    message = serializers.CharField()
    data = LogoutResponseDataSerializer()


class ChangePasswordSerializer(serializers.Serializer):
    current_password = serializers.CharField(
        write_only=True,
        trim_whitespace=False,
        style={"input_type": "password"},
    )

    new_password = serializers.CharField(
        write_only=True,
        min_length=8,
        trim_whitespace=False,
        style={"input_type": "password"},
    )

    new_password_confirm = serializers.CharField(
        write_only=True,
        trim_whitespace=False,
        style={"input_type": "password"},
    )

    def to_internal_value(self, data):
        if not isinstance(data, Mapping):
            raise serializers.ValidationError({
                "non_field_errors": [
                    "Invalid data. Expected a JSON object."
                ]
            })

        allowed_fields = set(self.fields.keys())
        unknown_fields = set(data.keys()) - allowed_fields

        if unknown_fields:
            raise serializers.ValidationError({
                field: ["This field is not allowed."]
                for field in sorted(unknown_fields)
            })

        return super().to_internal_value(data)

    def validate_new_password(self, value):
        user = self.context.get("request").user if self.context.get("request") else None
        password_validation.validate_password(value, user=user)
        return value

    def validate(self, attrs):
        if attrs["new_password"] != attrs["new_password_confirm"]:
            raise serializers.ValidationError({
                "new_password_confirm": "Passwords do not match."
            })

        if attrs["current_password"] == attrs["new_password"]:
            raise serializers.ValidationError({
                "new_password": (
                    "New password must be different from the current password."
                )
            })

        return attrs


class ChangePasswordResponseSerializer(serializers.Serializer):
    success = serializers.BooleanField()
    message = serializers.CharField()


class ForgotPasswordSerializer(serializers.Serializer):
    email = serializers.EmailField()

    def to_internal_value(self, data):
        if not isinstance(data, Mapping):
            raise serializers.ValidationError({
                "non_field_errors": [
                    "Invalid data. Expected a JSON object."
                ]
            })

        allowed_fields = set(self.fields.keys())
        unknown_fields = set(data.keys()) - allowed_fields

        if unknown_fields:
            raise serializers.ValidationError({
                field: ["This field is not allowed."]
                for field in sorted(unknown_fields)
            })

        return super().to_internal_value(data)

    def validate_email(self, value):
        return value.strip().lower()

class ForgotPasswordResponseSerializer(serializers.Serializer):
    success = serializers.BooleanField()
    message = serializers.CharField()

class ResetPasswordSerializer(serializers.Serializer):
    token = serializers.CharField(
        required=True,
        write_only=True,
    )

    new_password = serializers.CharField(
        write_only=True,
        min_length=8,
        trim_whitespace=False,
        style={"input_type": "password"},
    )

    new_password_confirm = serializers.CharField(
        write_only=True,
        trim_whitespace=False,
        style={"input_type": "password"},
    )

    def to_internal_value(self, data):
        if not isinstance(data, Mapping):
            raise serializers.ValidationError({
                "non_field_errors": [
                    "Invalid data. Expected a JSON object."
                ]
            })

        allowed_fields = set(self.fields.keys())
        unknown_fields = set(data.keys()) - allowed_fields

        if unknown_fields:
            raise serializers.ValidationError({
                field: ["This field is not allowed."]
                for field in sorted(unknown_fields)
            })

        return super().to_internal_value(data)


    def validate_new_password(self, value):
        password_validation.validate_password(value)
        return value

    def validate(self, attrs):
        if attrs["new_password"] != attrs["new_password_confirm"]:
            raise serializers.ValidationError({
                "new_password_confirm": "Passwords do not match."
            })

        return attrs

class ResetPasswordResponseSerializer(serializers.Serializer):
    success = serializers.BooleanField()
    message = serializers.CharField()


class PasswordSetupSerializer(serializers.Serializer):
    action = serializers.ChoiceField(
        choices=["setup", "renew"],
        default="setup",
        required=False,
        write_only=True,
        help_text=(
            "Optional action. Defaults to 'setup' when omitted. "
            "Use 'renew' with an expired token to request a new setup link."
        ),
    )

    token = serializers.CharField(
        required=True,
        write_only=True,
        allow_blank=False,
        trim_whitespace=True,
    )

    new_password = serializers.CharField(
        required=False,
        write_only=True,
        allow_blank=False,
        trim_whitespace=False,
        style={"input_type": "password"},
    )

    new_password_confirm = serializers.CharField(
        required=False,
        write_only=True,
        allow_blank=False,
        trim_whitespace=False,
        style={"input_type": "password"},
    )

    def to_internal_value(self, data):
        if not isinstance(data, Mapping):
            raise serializers.ValidationError({
                "non_field_errors": [
                    "Invalid data. Expected a JSON object."
                ]
            })

        action = data.get("action", "setup")

        if action == "renew":
            allowed_fields = {"action", "token"}
        else:
            allowed_fields = {
                "action",
                "token",
                "new_password",
                "new_password_confirm",
            }

        unknown_fields = set(data.keys()) - allowed_fields

        if unknown_fields:
            raise serializers.ValidationError({
                field: ["This field is not allowed."]
                for field in sorted(unknown_fields)
            })

        return super().to_internal_value(data)

    def validate(self, attrs):
        action = attrs.get("action")

        if action == "renew":
            return attrs

        password = attrs.get("new_password")
        confirmation = attrs.get("new_password_confirm")

        errors = {}

        if password is None:
            errors["new_password"] = ["This field is required."]

        if confirmation is None:
            errors["new_password_confirm"] = [
                "This field is required."
            ]

        if errors:
            raise serializers.ValidationError(errors)

        if password != confirmation:
            raise serializers.ValidationError({
                "new_password_confirm": ["Passwords do not match."]
            })

        return attrs

class PasswordSetupResponseSerializer(serializers.Serializer):
    success = serializers.BooleanField()
    message = serializers.CharField()

