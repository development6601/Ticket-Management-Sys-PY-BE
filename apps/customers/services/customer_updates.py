import hashlib
import secrets
from datetime import timedelta

from django.db import IntegrityError, connection, transaction
from django.db.models.functions import Lower
from django.utils import timezone
from rest_framework import serializers
from rest_framework.exceptions import (
    ValidationError,
    NotFound,
    PermissionDenied,
)

from apps.accounts.models import EmailChangeRequest, User, UserRole
from apps.accounts.permissions.rbac import (
    primary_has_customer_permission,
    role_has_permission,
)
from apps.accounts.validators import validate_phone_for_country
from apps.customers.models import Customer, CustomerUser
from apps.customers.permissions import CUSTOMERS_UPDATE
from apps.notifications.models import EmailJob

# Fields that only an internal administrator may change.
ADMIN_ONLY_FIELDS = {
    "account_manager",
    "notes",
}


class CustomerUpdateService:
    @staticmethod
    def _check_email_available(*, email, customer_id, user_id):
        if (
            User.objects
            .annotate(normalized_email=Lower("email"))
            .filter(normalized_email=email.lower())
            .exclude(pk=user_id)
            .exists()
        ):
            raise ValidationError({
                "email": ["This email address is already in use."]
            })

        if (
            Customer.all_objects
            .annotate(normalized_email=Lower("email"))
            .filter(normalized_email=email.lower())
            .exclude(pk=customer_id)
            .exists()
        ):
            raise ValidationError({
                "email": ["This email address is already in use."]
            })

    @staticmethod
    @transaction.atomic
    def request_email_change(*, customer_id, new_email, performed_by):
        customer = (
            Customer.objects
            .select_for_update()
            .filter(
                pk=customer_id,
                deleted_at__isnull=True,
            )
            .first()
        )

        if customer is None:
            raise NotFound("Customer company not found.")

        CustomerUpdateService._authorize(
            customer=customer,
            validated_data={"email": new_email},
            performed_by=performed_by,
        )

        if (
            not customer.is_active
            or customer.account_status != Customer.AccountStatus.ACTIVE
        ):
            raise ValidationError({
                "email": ["The company must be active."]
            })

        memberships = list(
            CustomerUser.objects
            .select_for_update()
            .filter(customer=customer)
            .order_by("pk")
        )

        primary = next(
            (m for m in memberships if m.is_primary_contact),
            None,
        )

        if primary is None:
            raise ValidationError({
                "email": ["The company has no primary contact."]
            })

        actor_is_admin = (
            performed_by.is_superuser
            or UserRole.objects.filter(
                user_id=performed_by.pk,
                role__code="ADMIN",
            ).exists()
        )

        if not actor_is_admin:
            if primary.user_id != performed_by.pk:
                raise PermissionDenied(
                    "Only the primary contact may request an email change."
                )

        user = User.objects.select_for_update().get(
            pk=primary.user_id
        )

        if (
            not user.is_active
            or user.account_status != User.AccountStatus.ACTIVE
            or not user.email_verified
            or not user.has_usable_password()
        ):
            raise ValidationError({
                "email": ["The primary contact is not eligible."]
            })

        old_email = user.email.strip().lower()
        new_email = new_email.strip().lower()

        if customer.email.strip().lower() != old_email:
            raise ValidationError({
                "email": [
                    "Company and primary contact emails are inconsistent."
                ]
            })

        if new_email == old_email:
            raise ValidationError({
                "email": ["This is already the current email address."]
            })

        CustomerUpdateService._check_email_available(
            email=new_email,
            customer_id=customer.pk,
            user_id=user.pk,
        )

        now = timezone.now()

        EmailChangeRequest.objects.filter(
            customer=customer,
            used_at__isnull=True,
        ).update(
            used_at=now,
            updated_at=now,
        )

        raw_token = secrets.token_urlsafe(32)
        token_hash = hashlib.sha256(
            raw_token.encode("utf-8")
        ).hexdigest()

        email_request = EmailChangeRequest.objects.create(
            user=user,
            customer=customer,
            requested_by=performed_by,
            old_email=old_email,
            new_email=new_email,
            token_hash=token_hash,
            expires_at=now + timedelta(hours=24),
        )

        EmailJob.objects.create(
            user=user,
            recipient_email=new_email,
            template="customer_email_change",
            subject="Verify your new email address",
            payload={
                "token": raw_token,
                "customer_id": str(customer.pk),
            },
        )

        return email_request, raw_token

    @staticmethod
    @transaction.atomic
    def confirm_email_change(*, raw_token):
        invalid = (
            "Email change link is invalid, expired, or already used."
        )

        if not isinstance(raw_token, str) or not raw_token.strip():
            raise ValidationError({"token": [invalid]})

        token_hash = hashlib.sha256(
            raw_token.encode("utf-8")
        ).hexdigest()

        reference = (
            EmailChangeRequest.objects
            .filter(token_hash=token_hash)
            .values("id", "customer_id", "user_id", "new_email")
            .first()
        )

        if reference is None:
            raise ValidationError({"token": [invalid]})

        if connection.vendor == "postgresql":
            lock_key = int.from_bytes(
                hashlib.sha256(
                    reference["new_email"].strip().lower().encode("utf-8")
                ).digest()[:8],
                byteorder="big",
                signed=True,
            )
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT pg_advisory_xact_lock(%s)",
                    [lock_key],
                )

        customer = (
            Customer.all_objects
            .select_for_update()
            .filter(pk=reference["customer_id"])
            .first()
        )

        if customer is None:
            raise ValidationError({"token": [invalid]})

        memberships = list(
            CustomerUser.objects
            .select_for_update()
            .filter(customer_id=customer.pk)
            .order_by("pk")
        )

        user = (
            User.objects
            .select_for_update()
            .filter(pk=reference["user_id"])
            .first()
        )

        if user is None:
            raise ValidationError({"token": [invalid]})

        email_request = (
            EmailChangeRequest.objects
            .select_for_update()
            .filter(pk=reference["id"])
            .first()
        )

        now = timezone.now()

        if (
            email_request is None
            or email_request.token_hash != token_hash
            or email_request.customer_id != customer.pk
            or email_request.user_id != user.pk
            or email_request.used_at is not None
            or email_request.expires_at <= now
        ):
            raise ValidationError({"token": [invalid]})

        if (
            customer.deleted_at is not None
            or not customer.is_active
            or customer.account_status != Customer.AccountStatus.ACTIVE
        ):
            raise ValidationError({"token": [invalid]})

        primary = next(
            (m for m in memberships if m.is_primary_contact),
            None,
        )

        if primary is None or primary.user_id != user.pk:
            raise ValidationError({"token": [invalid]})

        if (
            not user.is_active
            or user.account_status != User.AccountStatus.ACTIVE
            or not user.email_verified
            or not user.has_usable_password()
        ):
            raise ValidationError({"token": [invalid]})

        old_email = email_request.old_email.strip().lower()
        new_email = email_request.new_email.strip().lower()

        if (
            user.email.strip().lower() != old_email
            or customer.email.strip().lower() != old_email
        ):
            raise ValidationError({"token": [invalid]})

        CustomerUpdateService._check_email_available(
            email=new_email,
            customer_id=customer.pk,
            user_id=user.pk,
        )

        try:
            with transaction.atomic():
                user.email = new_email
                user.save(update_fields=["email", "updated_at"])

                customer.email = new_email
                customer.save(update_fields=["email", "updated_at"])
        except IntegrityError:
            raise ValidationError({
                "email": ["This email address is already in use."]
            })

        email_request.used_at = now
        email_request.save(
            update_fields=["used_at", "updated_at"]
        )

        EmailChangeRequest.objects.filter(
            customer=customer,
            used_at__isnull=True,
        ).update(
            used_at=now,
            updated_at=now,
        )

        return customer, user

    @staticmethod
    @transaction.atomic
    def update_customer(*, customer_id, validated_data, performed_by):
        customer = (
            Customer.objects
            .select_for_update()
            .filter(
                pk=customer_id,
                deleted_at__isnull=True,
            )
            .first()
        )

        if customer is None:
            raise NotFound("Customer company not found.")

        CustomerUpdateService._authorize(
            customer=customer,
            validated_data=validated_data,
            performed_by=performed_by,
        )

        if "account_manager" in validated_data:
            CustomerUpdateService._validate_account_manager(
                validated_data["account_manager"]
            )

        data = validated_data.copy()
        new_primary_user_id = data.pop(
            "primary_contact_user_id",
            None,
        )

        memberships = list(
            CustomerUser.objects
            .select_for_update()
            .filter(customer=customer)
            .order_by("pk")
        )

        current_primary = next(
            (m for m in memberships if m.is_primary_contact),
            None,
        )

        update_fields = set()

        if new_primary_user_id is not None:
            primary_count = sum(
                1 for membership in memberships
                if membership.is_primary_contact
            )
            if primary_count != 1:
                raise ValidationError({
                    "primary_contact_user_id": [
                        "The company must have exactly one "
                        "current primary contact before transfer."
                    ]
                })

            if (
                not customer.is_active
                or customer.account_status != Customer.AccountStatus.ACTIVE
            ):
                raise ValidationError({
                    "detail": (
                        "Primary contact can only be transferred "
                        "for an active company."
                    )
                })

            if current_primary is None:
                raise ValidationError({
                    "detail": "The company does not have a primary contact."
                })

            new_membership = next(
                (m for m in memberships if m.user_id == new_primary_user_id),
                None,
            )

            if new_membership is None:
                raise ValidationError({
                    "primary_contact_user_id": (
                        "The selected user must belong to this company."
                    )
                })

            if current_primary.pk == new_membership.pk:
                raise ValidationError({
                    "primary_contact_user_id": (
                        "This user is already the primary contact."
                    )
                })

            new_user = User.objects.select_for_update().get(
                pk=new_primary_user_id
            )

            if (
                not new_user.is_active
                or new_user.account_status != User.AccountStatus.ACTIVE
                or not new_user.email_verified
                or not new_user.has_usable_password()
            ):
                raise ValidationError({
                    "primary_contact_user_id": (
                        "The selected user must be active, verified, "
                        "and have a configured password."
                    )
                })

            new_contact_name = f"{new_user.first_name} {new_user.last_name}".strip()
            if not new_contact_name:
                raise ValidationError({
                    "primary_contact_user_id": (
                        "The selected user must have a valid first name or last name."
                    )
                })

            try:
                validated_phone = validate_phone_for_country(
                    value=new_user.phone,
                    country=data.get("country", customer.country),
                )
            except serializers.ValidationError as exc:
                raise ValidationError({
                    "primary_contact_user_id": exc.detail
                })

            EmailChangeRequest.objects.filter(
                customer=customer,
                used_at__isnull=True,
            ).update(used_at=timezone.now())

            # Remove old primary first to respect the unique constraint.
            current_primary.is_primary_contact = False
            current_primary.save(
                update_fields=["is_primary_contact", "updated_at"]
            )

            new_membership.is_primary_contact = True
            new_membership.save(
                update_fields=["is_primary_contact", "updated_at"]
            )

            customer.contact_person = (
                f"{new_user.first_name} {new_user.last_name}".strip()
            )
            customer.email = new_user.email
            update_fields.update({"contact_person", "email"})

            customer.phone = validated_phone
            update_fields.add("phone")

        elif "contact_person" in data or "phone" in data:
            if current_primary is None:
                raise ValidationError({
                    "detail": "The company does not have a primary contact."
                })

            primary_user = User.objects.select_for_update().get(
                pk=current_primary.user_id
            )
            user_fields = []

            if "contact_person" in data:
                contact_person_val = " ".join(data["contact_person"].split())
                if not contact_person_val:
                    raise ValidationError({
                        "contact_person": ["Contact person cannot be blank."]
                    })
                parts = contact_person_val.split(maxsplit=1)
                if len(parts[0]) > 150:
                    raise ValidationError({
                        "contact_person": [
                            "First name cannot exceed 150 characters."
                        ]
                    })
                if len(parts) > 1 and len(parts[1]) > 150:
                    raise ValidationError({
                        "contact_person": [
                            "Last name cannot exceed 150 characters."
                        ]
                    })
                primary_user.first_name = parts[0] if parts else ""
                primary_user.last_name = parts[1] if len(parts) > 1 else ""
                data["contact_person"] = contact_person_val
                user_fields += ["first_name", "last_name"]

            if "phone" in data:
                primary_user.phone = data["phone"]
                user_fields.append("phone")

            primary_user.save(update_fields=user_fields + ["updated_at"])

        for field, value in data.items():
            setattr(customer, field, value)
            update_fields.add(field)

        if update_fields:
            customer.save(update_fields=list(update_fields) + ["updated_at"])

        return customer

    @staticmethod
    def _validate_account_manager(account_manager):
        if account_manager is None:
            return
        if not isinstance(account_manager, User):
            raise ValidationError({
                "account_manager": [
                    "Select a valid account manager."
                ]
            })
        manager = User.objects.filter(
            pk=account_manager.pk
        ).first()
        if manager is None:
            raise ValidationError({
                "account_manager": [
                    "Account manager does not exist."
                ]
            })
        if (
            not manager.is_active
            or manager.account_status != User.AccountStatus.ACTIVE
        ):
            raise ValidationError({
                "account_manager": [
                    "Account manager must be an active internal user."
                ]
            })
        if not manager.email_verified:
            raise ValidationError({
                "account_manager": [
                    "Account manager must have a verified email."
                ]
            })
        if UserRole.objects.filter(
            user=manager,
            role__code="CUSTOMER",
        ).exists():
            raise ValidationError({
                "account_manager": [
                    "Customer users cannot be assigned as account managers."
                ]
            })
        if CustomerUser.objects.filter(user=manager).exists():
            raise ValidationError({
                "account_manager": [
                    "A user belonging to a customer company cannot be assigned as an account manager."
                ]
            })

    @staticmethod
    def _authorize(*, customer, validated_data, performed_by):
        """
        Caller authorization and company scoping, enforced here so the
        service is not a permission bypass when used outside the views.
        """
        if performed_by is None or not getattr(performed_by, "pk", None):
            raise PermissionDenied("Authentication is required.")

        actor = (
            User.objects
            .select_for_update()
            .filter(pk=performed_by.pk)
            .first()
        )

        if (
            actor is None
            or not actor.is_active
            or actor.account_status != User.AccountStatus.ACTIVE
        ):
            raise PermissionDenied(
                "Your account must be active to update a company."
            )

        is_admin = (
            actor.is_superuser
            or UserRole.objects.filter(
                user=actor,
                role__code="ADMIN",
            ).exists()
        )

        membership = CustomerUser.objects.filter(
            user=actor,
        ).first()

        is_internal_admin = is_admin and membership is None
        forbidden = ADMIN_ONLY_FIELDS.intersection(validated_data)
        if forbidden and not is_internal_admin:
            raise PermissionDenied(
                "Only an internal administrator may modify: "
                + ", ".join(sorted(forbidden))
            )

        if is_internal_admin:
            return

        is_primary = bool(
            membership
            and membership.customer_id == customer.pk
            and membership.is_primary_contact
        )
        is_transfer = "primary_contact_user_id" in validated_data

        user_role = (
            UserRole.objects
            .select_related("role")
            .filter(user=actor)
            .first()
        )
        has_customers_update = role_has_permission(
            user_role.role if user_role else None,
            CUSTOMERS_UPDATE,
        )

        if membership is not None:
            if membership.customer_id != customer.pk:
                raise PermissionDenied(
                    "You cannot modify another company."
                )

            if (
                not customer.is_active
                or customer.account_status != Customer.AccountStatus.ACTIVE
            ):
                raise PermissionDenied(
                    "Your customer company is inactive."
                )

            if is_transfer and not is_primary:
                raise PermissionDenied(
                    "Only the primary contact can transfer ownership."
                )

            if not is_primary:
                raise PermissionDenied(
                    "You do not have permission to update this company."
                )

            if not primary_has_customer_permission(
                CUSTOMERS_UPDATE
            ):
                raise PermissionDenied(
                    "Company update permission is not enabled."
                )

            return

        # Internal non-admin user with explicit update permission.
        if not has_customers_update:
            raise PermissionDenied(
                "You do not have permission to update customer details."
            )

        if is_transfer:
            raise PermissionDenied(
                "Only the current primary contact or "
                "an internal administrator can transfer "
                "the primary contact."
            )

