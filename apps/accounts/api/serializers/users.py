from collections.abc import Mapping

from rest_framework import serializers
from rest_framework.exceptions import PermissionDenied

from apps.accounts.models import User, Role, UserRole
from apps.customers.models import Customer, CustomerUser, normalize_company_name

from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError as DjangoValidationError
from apps.accounts.validators import PhoneNumberField, normalize_phone_number

from apps.accounts.permissions.constants import *
from apps.accounts.permissions.rbac import (
    primary_has_customer_permission,
    role_has_permission,
)
from drf_spectacular.utils import extend_schema_field

class UserResponseSerializer(serializers.ModelSerializer):
    role = serializers.SerializerMethodField()
    customer_id = serializers.SerializerMethodField()
    company_name = serializers.SerializerMethodField()
    is_primary_contact = serializers.SerializerMethodField()

    class Meta :
        model = User
        fields = [
            "id",
            "email",
            "first_name",
            "last_name",
            "phone",
            "address",
            "email_verified",
            "is_active",
            "role",
            "customer_id",
            "company_name",
            "is_primary_contact",
            "created_at",
            "updated_at",
        ]

        read_only_fields = fields

    @extend_schema_field(serializers.CharField(allow_null=True))
    def get_role(self, obj):
        user_role = getattr(obj, "user_role", None)

        if not user_role:
            return None

        return user_role.role.code

    
    def _get_membership(self, obj):
        if not hasattr(obj, "_cached_customer_membership"):
            obj._cached_customer_membership = (
                CustomerUser.objects
                .select_related("customer")
                .filter(user=obj)
                .first()
            )
        return obj._cached_customer_membership

    @extend_schema_field(serializers.UUIDField(allow_null=True))
    def get_customer_id(self, obj):
        membership = self._get_membership(obj)
        return membership.customer_id if membership else None

    @extend_schema_field(serializers.CharField(allow_null=True))
    def get_company_name(self, obj):
        membership = self._get_membership(obj)
        return (
            membership.customer.company_name
            if membership and membership.customer
            else None
        )

    @extend_schema_field(serializers.BooleanField(allow_null=True))
    def get_is_primary_contact(self, obj):
        membership = self._get_membership(obj)
        return membership.is_primary_contact if membership else None

    def to_representation(self, instance):
        data = super().to_representation(instance)

        if data.get("role") != "CUSTOMER":
            data.pop("customer_id", None)
            data.pop("company_name", None)
            data.pop("is_primary_contact", None)

        return data


class MeResponseSerializer(serializers.Serializer):
    success = serializers.BooleanField()
    message = serializers.CharField()
    data = UserResponseSerializer()



#patch/auth/me
class UpdateProfileSerializer(serializers.ModelSerializer):
    phone = PhoneNumberField(
        required=False,
        allow_blank=True,
        max_length=50,
        country_field="country",
    )

    address = serializers.CharField(
        required=False,
        allow_blank=True,
        max_length=2000,
        trim_whitespace=True,
    )

    country = serializers.CharField(
        required=False,
        write_only=True,
        allow_blank=False,
        max_length=100,
        trim_whitespace=True,
    )
    
    class Meta:
        model = User
        fields = [
            "first_name",
            "last_name",
            "phone",
            "address",
            "country",
        ]

    def validate_first_name(self, value):
        return value.strip()

    def validate_last_name(self, value):
        return value.strip()

    def validate(self, attrs):
        country = attrs.pop("country", None)

        if country is not None:
            from apps.accounts.validators import resolve_region_from_country

            if not resolve_region_from_country(country):
                raise serializers.ValidationError({
                    "country": ["Unsupported country."]
                })

            if "phone" not in attrs:
                raise serializers.ValidationError({
                    "country": [
                        "Provide a phone number when specifying a country."
                    ]
                })

        return attrs

class UserListSerializer(serializers.ModelSerializer):
    role = serializers.SerializerMethodField()
    customer_id = serializers.SerializerMethodField()
    company_name = serializers.SerializerMethodField()
    is_primary_contact = serializers.SerializerMethodField()
    
    class Meta:
        model = User
        fields = [
            "id",
            "email",
            "first_name",
            "last_name",
            "phone",
            "address",
            "email_verified",
            "is_active",
            "role",
            "customer_id",
            "company_name",
            "is_primary_contact",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields

    @extend_schema_field(serializers.CharField(allow_null=True))
    def get_role(self, obj):
        user_role = getattr(obj, "user_role", None)
        if not user_role:
            return None
        return user_role.role.code

    def _get_membership(self, obj):
        if not hasattr(obj, "_cached_customer_user"):
            customer_members = getattr(obj, "customer_members", None)
            if customer_members is not None and hasattr(customer_members, "all"):
                members = list(customer_members.all())
                obj._cached_customer_user = members[0] if members else None
            else:
                obj._cached_customer_user = (
                    CustomerUser.objects
                    .select_related("customer")
                    .filter(user=obj)
                    .first()
                )
        return obj._cached_customer_user

    @extend_schema_field(serializers.UUIDField(allow_null=True))
    def get_customer_id(self, obj):
        membership = self._get_membership(obj)
        return membership.customer_id if membership else None

    @extend_schema_field(serializers.CharField(allow_null=True))
    def get_company_name(self, obj):
        membership = self._get_membership(obj)
        return (
            membership.customer.company_name
            if membership and membership.customer
            else None
        )

    @extend_schema_field(serializers.BooleanField(allow_null=True))
    def get_is_primary_contact(self, obj):
        membership = self._get_membership(obj)
        return membership.is_primary_contact if membership else None

    def to_representation(self, instance):
        data = super().to_representation(instance)

        if not data.get("customer_id"):
            data.pop("customer_id", None)
            data.pop("company_name", None)
            data.pop("is_primary_contact", None)

        return data

class UserListDataSerializer(serializers.Serializer):
    count = serializers.IntegerField()
    next = serializers.URLField(allow_null=True)
    previous = serializers.URLField(allow_null=True)
    results = UserListSerializer(many=True)


class UserListResponseSerializer(serializers.Serializer):
    success = serializers.BooleanField()
    message = serializers.CharField()
    data = UserListDataSerializer()


class InternalUserCreatedDataSerializer(serializers.ModelSerializer):
    role = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = [
            "id",
            "email",
            "first_name",
            "last_name",
            "phone",
            "address",
            "email_verified",
            "is_active",
            "role",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields

    @extend_schema_field(serializers.CharField(allow_null=True))
    def get_role(self, obj):
        user_role = getattr(obj, "user_role", None)
        return user_role.role.code if user_role else None


class CreateInternalUserResponseSerializer(serializers.Serializer):
    success = serializers.BooleanField()
    message = serializers.CharField()
    data = InternalUserCreatedDataSerializer()


class CreateExistingCompanyCustomerUserResponseSerializer(serializers.Serializer):
    success = serializers.BooleanField()
    message = serializers.CharField()
    data = UserListSerializer()


class RegisterUserResponseSerializer(serializers.ModelSerializer):
    role = serializers.SerializerMethodField()
    customer_id = serializers.SerializerMethodField()
    is_primary_contact = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = [
            "id",
            "email",
            "first_name",
            "last_name",
            "role",
            "customer_id",
            "is_primary_contact",
            "account_status",
            "email_verified",
            "is_active",
        ]

    @extend_schema_field(serializers.CharField(allow_null=True))
    def get_role(self, obj):
        user_role = UserRole.objects.select_related("role").filter(
            user=obj
        ).first()
        return user_role.role.code if user_role else None

    @extend_schema_field(serializers.CharField(allow_null=True))
    def get_customer_id(self, obj):
        membership = obj.customer_members.first()
        return str(membership.customer_id) if membership else None

    @extend_schema_field(serializers.BooleanField())
    def get_is_primary_contact(self, obj):
        membership = obj.customer_members.first()
        return membership.is_primary_contact if membership else False


class RegisterCustomerResponseSerializer(serializers.ModelSerializer):
    customer = serializers.CharField(
        source="company_name",
        read_only=True,
    )

    class Meta:
        model = Customer
        fields = [
            "id",
            "customer",
            "contact_person",
            "email",
            "phone",
            "address",
            "state",
            "country",
            "is_active",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields


class CreateCustomerCompanyDataSerializer(serializers.Serializer):
    customer = RegisterCustomerResponseSerializer()
    primary_user = RegisterUserResponseSerializer()


class CreateCustomerCompanyResponseSerializer(serializers.Serializer):
    success = serializers.BooleanField()
    message = serializers.CharField()
    data = CreateCustomerCompanyDataSerializer()


# Nested role shape for the detail view
class UserRoleSerializer(serializers.Serializer):
    id = serializers.UUIDField(source="role.id")
    name = serializers.CharField(source="role.name")


class UserDetailSerializer(serializers.ModelSerializer):
    role = serializers.SerializerMethodField()
    roles = serializers.SerializerMethodField()
    customer_id = serializers.SerializerMethodField()
    company_name = serializers.SerializerMethodField()
    is_primary_contact = serializers.SerializerMethodField()
    last_login = serializers.DateTimeField(read_only=True)

    class Meta:
        model = User
        fields = [
            "id",
            "email",
            "first_name",
            "last_name",
            "phone",
            "address",
            "email_verified",
            "is_active",
            "last_login",
            "role",
            "roles",
            "customer_id",
            "company_name",
            "is_primary_contact",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields

    @extend_schema_field(serializers.CharField(allow_null=True))
    def get_role(self, obj):
        user_role = getattr(obj, "user_role", None)
        if not user_role:
            return None
        return user_role.role.code

    @extend_schema_field(UserRoleSerializer(many=True))
    def get_roles(self, obj):
        user_role = getattr(obj, "user_role", None)

        if not user_role:
            return []

        return [{
            "id": user_role.role.id,
            "name": user_role.role.name,
        }]

    def _get_membership(self, obj):
        if not hasattr(obj, "_cached_customer_detail_membership"):
            customer_members = getattr(obj, "customer_members", None)
            if customer_members is not None and hasattr(customer_members, "all"):
                members = list(customer_members.all())
                obj._cached_customer_detail_membership = members[0] if members else None
            else:
                obj._cached_customer_detail_membership = (
                    CustomerUser.objects
                    .select_related("customer")
                    .filter(user=obj)
                    .first()
                )
        return obj._cached_customer_detail_membership

    @extend_schema_field(serializers.UUIDField(allow_null=True))
    def get_customer_id(self, obj):
        membership = self._get_membership(obj)
        return membership.customer_id if membership else None

    @extend_schema_field(serializers.CharField(allow_null=True))
    def get_company_name(self, obj):
        membership = self._get_membership(obj)
        return (
            membership.customer.company_name
            if membership and membership.customer
            else None
        )

    @extend_schema_field(serializers.BooleanField(allow_null=True))
    def get_is_primary_contact(self, obj):
        membership = self._get_membership(obj)
        return membership.is_primary_contact if membership else None

    def to_representation(self, instance):
        data = super().to_representation(instance)

        if not data.get("customer_id"):
            data.pop("customer_id", None)
            data.pop("company_name", None)
            data.pop("is_primary_contact", None)

        return data


class UserDetailResponseSerializer(serializers.Serializer):
    success = serializers.BooleanField()
    message = serializers.CharField()
    data = UserDetailSerializer()


class UpdateUserSerializer(serializers.ModelSerializer):
    country = serializers.CharField(
        required=False,
        write_only=True,
        allow_blank=False,
        max_length=100,
        trim_whitespace=True,
    )

    phone = PhoneNumberField(
        required=False,
        allow_blank=True,
        max_length=50,
        country_field="country",
    )

    address = serializers.CharField(
        required=False,
        allow_blank=True,
        max_length=2000,
        trim_whitespace=True,
    )

    class Meta:
        model = User
        fields = [
            "first_name",
            "last_name",
            "phone",
            "address",
            "country",
        ]

    def to_internal_value(self, data):
        if not isinstance(data, Mapping):
            raise serializers.ValidationError({
                "non_field_errors": [
                    "Invalid data. Expected a JSON object."
                ]
            })

        if "email" in data:
            raise serializers.ValidationError({
                "email": "Email cannot be changed through this endpoint."
            })

        allowed_fields = set(self.fields.keys())
        invalid_fields = set(data.keys()) - allowed_fields

        if invalid_fields:
            raise serializers.ValidationError({
                field: "This field cannot be updated here."
                for field in sorted(invalid_fields)
            })

        return super().to_internal_value(data)

    def validate_first_name(self, value):
        return value.strip()

    def validate_last_name(self, value):
        return value.strip()

    def validate(self, attrs):
        country = attrs.pop("country", None)

        if country is not None:
            from apps.accounts.validators import resolve_region_from_country

            if not resolve_region_from_country(country):
                raise serializers.ValidationError({
                    "country": ["Unsupported country."]
                })

            if "phone" not in attrs:
                raise serializers.ValidationError({
                    "country": [
                        "Provide a phone number when specifying a country."
                    ]
                })

        return attrs


class CustomerCreateSerializer(serializers.Serializer):
    company_name = serializers.CharField(
        max_length=255,
        trim_whitespace=True,
    )
    contact_person = serializers.CharField(
        max_length=255,
        trim_whitespace=True,
    )
    phone = PhoneNumberField(
        required=True,
        allow_blank=False,
        max_length=50,
        country_field="country",
    )
    address = serializers.CharField(
        max_length=512,
    )
    state = serializers.CharField(
        max_length=100,
    )
    country = serializers.CharField(
        max_length=100,
    )

    def validate_company_name(self, value):
        normalized = normalize_company_name(value)

        if not normalized:
            raise serializers.ValidationError(
                "Customer company name is required."
            )

        return normalized

    def to_internal_value(self, data):
        if not isinstance(data, Mapping):
            raise serializers.ValidationError(
                "Invalid customer data."
            )

        self._raw_data = data

        unknown_fields = set(data.keys()) - set(self.fields.keys())

        if unknown_fields:
            raise serializers.ValidationError({
                field: "This field is not allowed during customer creation."
                for field in sorted(unknown_fields)
            })

        return super().to_internal_value(data)

    def validate(self, attrs):
        attrs["company_name"] = normalize_company_name(attrs["company_name"])
        attrs["phone"] = normalize_phone_number(
            attrs["phone"],
            country=attrs.get("country"),
        )
        return attrs

class CreateUserRequestSchemaSerializer(serializers.Serializer):
    email = serializers.EmailField(
        required=True,
        allow_blank=False,
    )
    first_name = serializers.CharField(
        required=False,
        allow_blank=False,
        trim_whitespace=True,
    )
    last_name = serializers.CharField(
        required=False,
        allow_blank=True,
        trim_whitespace=True,
    )
    phone = PhoneNumberField(
        required=False,
        allow_blank=True,
        max_length=50,
    )
    address = serializers.CharField(
        required=False,
        allow_blank=True,
        max_length=2000,
        trim_whitespace=True,
    )
    contact_person = serializers.CharField(
        required=False,
        allow_blank=False,
        max_length=255,
        trim_whitespace=True,
        write_only=True,
    )
    state = serializers.CharField(
        required=False,
        allow_blank=False,
        max_length=100,
        trim_whitespace=True,
        write_only=True,
    )
    country = serializers.CharField(
        required=False,
        allow_blank=False,
        max_length=100,
        trim_whitespace=True,
        write_only=True,
    )
    role_code = serializers.CharField(
        write_only=True,
        required=False,
    )
    password = serializers.CharField(
        write_only=True,
        required=False,
        min_length=8,
        style={"input_type": "password"},
    )
    password_confirm = serializers.CharField(
        write_only=True,
        required=False,
        style={"input_type": "password"},
    )
    customer_id = serializers.UUIDField(
        write_only=True,
        required=False,
        allow_null=True,
    )
    customer = serializers.CharField(
        write_only=True,
        required=False,
        allow_blank=False,
        max_length=255,
        trim_whitespace=True,
        help_text="New customer company name (string).",
    )


class CreateUserSerializer(serializers.ModelSerializer):
    email = serializers.EmailField(
        required=True,
        allow_blank=False,
    )

    role_code = serializers.CharField(
        write_only=True,
        required=False,
    )
    customer_id = serializers.UUIDField(
        write_only=True,
        required=False,
        allow_null=True,
    )
    customer = CustomerCreateSerializer(
        write_only=True,
        required=False,
        allow_null=True,
    ) 
    first_name = serializers.CharField(
        required=False,
        allow_blank=False,
        trim_whitespace=True,
    )

    last_name = serializers.CharField(
        required=False,
        allow_blank=True,
        trim_whitespace=True,
    )
    phone = PhoneNumberField(
        required=False,
        allow_blank=True,
        max_length=50,
    )

    address = serializers.CharField(
        required=False,
        allow_blank=True,
        max_length=2000,
        trim_whitespace=True,
    )

    contact_person = serializers.CharField(
        required=False,
        allow_blank=False,
        max_length=255,
        trim_whitespace=True,
        write_only=True,
    )

    state = serializers.CharField(
        required=False,
        allow_blank=False,
        max_length=100,
        trim_whitespace=True,
        write_only=True,
    )

    country = serializers.CharField(
        required=False,
        allow_blank=False,
        max_length=100,
        trim_whitespace=True,
        write_only=True,
    )

    password = serializers.CharField(
        write_only=True,
        required=False,
        min_length=8,
        style={"input_type": "password"},
    )

    password_confirm = serializers.CharField(
        write_only=True,
        required=False,
        style={"input_type": "password"},
    )

    class Meta:
        model = User
        fields = [
            "email",
            "first_name",
            "last_name",
            "phone",
            "address",
            "contact_person",
            "state",
            "country",
            "role_code",
            "password",
            "password_confirm",
            "customer_id",
            "customer"
        ]

        
    def to_internal_value(self, data):
        if not isinstance(data, Mapping):
            raise serializers.ValidationError({
                "non_field_errors": [
                    "Invalid data. Expected a JSON object."
                ]
            })

        data = data.copy()

        # Reject nested company creation requests.
        if isinstance(data.get("customer"), Mapping):
            raise serializers.ValidationError({
                "customer": (
                    "Nested customer objects are not supported. "
                    "Provide a company name as a string."
                )
            })

        # New-company creation uses flat JSON only.
        if "customer" in data:
            if not isinstance(data["customer"], str):
                raise serializers.ValidationError({
                    "customer": "Customer must be a company name string."
                })

            company_name = normalize_company_name(data["customer"])
            contact_person = data.get("contact_person")

            if not company_name:
                raise serializers.ValidationError({
                    "customer": "Customer company name is required."
                })

            if (
                not isinstance(contact_person, str)
                or not contact_person.strip()
            ):
                raise serializers.ValidationError({
                    "contact_person": "Contact person is required."
                })

            contact_person = contact_person.strip()

            required_company_fields = (
                "phone", "address", "state", "country"
            )

            missing = [
                field
                for field in required_company_fields
                if not isinstance(data.get(field), str)
                or not data[field].strip()
            ]

            if missing:
                raise serializers.ValidationError({
                    field: "This field is required."
                    for field in missing
                })

            forbidden_fields = (
                "customer_id",
                "first_name",
                "last_name",
                "role_code",
                "password",
                "password_confirm",
            )

            provided_forbidden = [
                field
                for field in forbidden_fields
                if field in data
            ]

            if provided_forbidden:
                raise serializers.ValidationError({
                    field: "This field is not allowed for new company creation."
                    for field in provided_forbidden
                })

            # Keep the existing User name fields.
            first_name, _, last_name = contact_person.partition(" ")

            name_fields = {
                "first_name": first_name,
                "last_name": last_name,
            }

            for field_name, value in name_fields.items():
                max_length = User._meta.get_field(field_name).max_length

                if max_length and len(value) > max_length:
                    raise serializers.ValidationError({
                        "contact_person": (
                            f"Contact person cannot be converted into a "
                            f"valid user name. {field_name} exceeds "
                            f"{max_length} characters."
                        )
                    })

            data["first_name"] = first_name
            data["last_name"] = last_name
            data["role_code"] = "CUSTOMER"

            # Internal representation only, not the public request format.
            data["customer"] = {
                "company_name": company_name,
                "contact_person": contact_person,
                "phone": data.pop("phone"),
                "address": data.pop("address"),
                "state": data.pop("state"),
                "country": data.pop("country"),
            }

            data.pop("contact_person", None)

        allowed_fields = set(self.fields.keys())
        unknown_fields = set(data.keys()) - allowed_fields

        if unknown_fields:
            raise serializers.ValidationError({
                field: "This field is not allowed during user creation."
                for field in sorted(unknown_fields)
            })

        request = self.context.get("request")
        user = getattr(request, "user", None)

        if user and user.is_authenticated:
            customer_user = self._get_authenticated_customer_user()

            if customer_user and not self._is_admin(user):
                supplied_role = data.get("role_code")

                if supplied_role is not None and supplied_role != "CUSTOMER":
                    raise serializers.ValidationError({
                        "role_code": (
                            "Customer users cannot assign other roles."
                        )
                    })

                data.pop("role_code", None)

        try:
            return super().to_internal_value(data)
        except serializers.ValidationError as exc:
            if (
                isinstance(exc.detail, Mapping)
                and isinstance(exc.detail.get("customer"), Mapping)
            ):
                detail = dict(exc.detail)
                customer_errors = dict(detail.pop("customer"))
                if "company_name" in customer_errors:
                    detail["customer"] = customer_errors.pop("company_name")
                for field_key, field_err in customer_errors.items():
                    detail[field_key] = field_err
                raise serializers.ValidationError(detail)
            raise


    def validate_email(self, value):
        email = value.strip().lower()

        if User.objects.filter(email__iexact=email).exists():
            raise serializers.ValidationError(
                "A user with this email already exists."
            )

        if Customer.all_objects.filter(
            email__iexact=email
        ).exists():
            raise serializers.ValidationError(
                "This email is already assigned to a company."
            )

        return email

    def validate_first_name(self, value):
        return value.strip()

    def validate_last_name(self, value):
        return value.strip()


    def _get_authenticated_customer_user(self):
        request = self.context.get("request")
        user = getattr(request, "user", None)

        if not user or not user.is_authenticated:
            return None

        return (
            CustomerUser.objects
            .select_related("customer")
            .filter(user=user)
            .first()
        )


    def _has_permission(self, user, permission_name):
        user_role = (
            UserRole.objects
            .select_related("role")
            .filter(user=user)
            .first()
        )
        return role_has_permission(
            user_role.role if user_role else None,
            permission_name,
        )

    def _is_admin(self, user):
        return (
            user.is_superuser
            or UserRole.objects.filter(
                user=user,
                role__code="ADMIN",
            ).exists()
        )

    def validate(self, attrs):
        # Reject flat company-only fields that were not
        # consumed by the flat company creation conversion.
        company_only_fields = (
            "contact_person",
            "state",
            "country",
        )
        unexpected_fields = [
            field
            for field in company_only_fields
            if field in attrs
        ]
        if unexpected_fields:
            raise serializers.ValidationError({
                field: "This field is only allowed when creating a new company."
                for field in unexpected_fields
            })

        # AUTHENTICATED ACTOR
        request = self.context.get("request")
        user = getattr(request, "user", None)

        if not user or not user.is_authenticated:
            raise serializers.ValidationError({
                "permission": "Authentication is required."
            })

        is_admin = self._is_admin(user)
        customer_user = self._get_authenticated_customer_user()

        if not attrs.get("first_name", "").strip():
            raise serializers.ValidationError({
                "first_name": "First name is required."
            })

        if attrs.get("customer") is None and not attrs.get("last_name", "").strip():
            raise serializers.ValidationError({
                "last_name": "Last name is required."
            })

        # ---------------------------------------------------------
        # CUSTOMER USER CREATOR
        # ---------------------------------------------------------

        if customer_user and not is_admin:

            customer_role = Role.objects.filter(
                code="CUSTOMER",
                is_assignable=True,
            ).first()

            if not customer_role:
                raise serializers.ValidationError({
                    "role_code": "CUSTOMER role is not configured."
                })

            
            # Customer-created users can ONLY have the CUSTOMER role.
            # Never trust role_code supplied by the client.
            attrs["role_code"] = customer_role.code

            customer = customer_user.customer

            if not customer.is_active or customer.deleted_at is not None:
                raise serializers.ValidationError({
                    "customer": "Your company is inactive or unavailable."
                })

            if not (
                customer_user.is_primary_contact
                and primary_has_customer_permission(USERS_CREATE_CUSTOMER)
            ):
                raise PermissionDenied(
                    "You do not have permission to create customer users."
                )

            # Customer creator cannot select another company.
            if attrs.get("customer_id"):
                raise serializers.ValidationError({
                    "customer_id": (
                        "Customer users cannot specify a customer company."
                    )
                })

            if attrs.get("customer"):
                raise serializers.ValidationError({
                    "customer": (
                        "Customer users cannot create or specify a company."
                    )
                })

            # Always use creator's company.
            attrs["customer_id"] = customer.id

            # Customer users use password setup/email verification flow.
            if "password" in attrs or "password_confirm" in attrs:
                raise serializers.ValidationError({
                    "password": (
                        "Password fields are not allowed for customer users."
                    )
                })

            return attrs

        role = Role.objects.filter(
            code=attrs.get("role_code"),
            is_assignable=True,
        ).first()

        if not role:
            raise serializers.ValidationError({
                "role_code": "The selected role is not available for assignment."
            })

        
        # INTERNAL CREATOR / ADMIN

        if role.code == "CUSTOMER":

            if not is_admin:
                has_customer_create_permission = self._has_permission(
                    user,
                    USERS_CREATE_CUSTOMER,
                )

                if not has_customer_create_permission:
                    raise PermissionDenied(
                        "You do not have permission to create customer users."
                    )

            customer_id = attrs.get("customer_id")
            customer_data = attrs.get("customer")

            if bool(customer_id) == bool(customer_data):
                raise serializers.ValidationError({
                    "customer": (
                        "Provide exactly one of customer_id or customer."
                    )
                })

            if attrs.get("password") or attrs.get("password_confirm"):
                raise serializers.ValidationError({
                    "password": (
                        "Password cannot be provided for customer users."
                    )
                })

            if customer_id:
                customer = Customer.objects.filter(
                    id=customer_id,
                    is_active=True,
                    deleted_at__isnull=True,
                ).first()

                if not customer:
                    raise serializers.ValidationError({
                        "customer_id": (
                            "Customer does not exist or is inactive."
                        )
                    })

            return attrs

        # ---------------------------------------------------------
        # INTERNAL USER
        # ---------------------------------------------------------

        if not is_admin:
            has_internal_create_permission = self._has_permission(
                user,
                USERS_CREATE_INTERNAL,
            )

            if not has_internal_create_permission:
                raise PermissionDenied(
                    "You do not have permission to create internal users."
                )

        if attrs.get("customer_id"):
            raise serializers.ValidationError({
                "customer_id": (
                    "Customer information is not allowed for internal users."
                )
            })

        if attrs.get("customer"):
            raise serializers.ValidationError({
                "customer": (
                    "Customer information is not allowed for internal users."
                )
            })

        # for field in ("first_name", "last_name"):
        #     if not attrs.get(field):
        #         raise serializers.ValidationError({
        #             field: "This field is required for internal users."
        #         })

        password = attrs.get("password")
        password_confirm = attrs.get("password_confirm")

        if not password:
            raise serializers.ValidationError({
                "password": "Password is required for internal users."
            })

        if not password_confirm:
            raise serializers.ValidationError({
                "password_confirm": (
                    "Password confirmation is required for internal users."
                )
            })

        if password != password_confirm:
            raise serializers.ValidationError({
                "password_confirm": "Passwords do not match."
            })

        temp_user = User(
            email=attrs.get("email", ""),
            first_name=attrs.get("first_name", ""),
            last_name=attrs.get("last_name", ""),
        )
        try:
            validate_password(password, user=temp_user)
        except DjangoValidationError as exc:
            raise serializers.ValidationError({
                "password": list(exc.messages)
            })

        return attrs
        

class UpdateUserStatusSerializer(serializers.Serializer):
    is_active = serializers.BooleanField()


class UserStatusDataSerializer(UserListSerializer):
    activation_pending = serializers.BooleanField(default=False)

    class Meta(UserListSerializer.Meta):
        fields = UserListSerializer.Meta.fields + ["activation_pending"]


class UserStatusResponseSerializer(serializers.Serializer):
    success = serializers.BooleanField()
    message = serializers.CharField()
    data = UserStatusDataSerializer()


class UpdateUserRoleSerializer(serializers.Serializer):
    role_id = serializers.UUIDField()

    def validate(self, attrs):
        request = self.context.get("request")
        user = getattr(request, "user", None)

        if not user or not user.is_authenticated:
            raise serializers.ValidationError({
                "permission": "Authentication is required."
            })

        try:
            role = Role.objects.get(
                id=attrs["role_id"],
                is_assignable=True,
            )
        except Role.DoesNotExist:
            raise serializers.ValidationError({
                "role_id": (
                    "The selected role is not available for assignment."
                )
            })

        if role.code == "ADMIN":
            raise serializers.ValidationError({
                "role_id": (
                    "The Admin role cannot be assigned through this API."
                )
            })

        return attrs