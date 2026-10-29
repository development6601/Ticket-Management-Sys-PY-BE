from django.db import transaction, connection
from django.utils import timezone

from apps.accounts.models import User, Role, UserRole, EmailVerificationRequest, PasswordResetRequest
from apps.customers.models import Customer, CustomerUser, normalize_company_name
from rest_framework.exceptions import ValidationError, PermissionDenied

from apps.accounts.services.authentication import AuthenticationService
from apps.notifications.models import EmailJob

from apps.accounts.permissions.constants import (
    USERS_ACTIVATE, 
    USERS_DEACTIVATE,
    USERS_CREATE_CUSTOMER,
    USERS_CREATE_INTERNAL,
    ROLES_ASSIGN,
)
from apps.accounts.permissions.rbac import (
    primary_has_customer_permission,
    role_has_permission,
)
from django.db.models import Q

class UserService:

    @staticmethod
    def _sync_primary_contact_name(*, user, customer):
        full_name = " ".join(
            part.strip()
            for part in (user.first_name, user.last_name)
            if part and part.strip()
        )
        if not full_name:
            raise ValidationError({
                "first_name": [
                    "Primary contact name cannot be empty."
                ]
            })
        if customer.contact_person != full_name:
            customer.contact_person = full_name
            customer.save(
                update_fields=["contact_person", "updated_at"]
            )

    @staticmethod
    @transaction.atomic
    def _update_user_fields(*, user, validated_data):
        data = validated_data.copy()
        # Country is a validation hint, not a User model field.
        data.pop("country", None)

        # Identify the company before taking locks.
        membership_ref = (
            CustomerUser.objects
            .filter(
                user_id=user.pk,
                is_primary_contact=True,
            )
            .values("customer_id")
            .first()
        )

        customer = None
        if membership_ref:
            customer = (
                Customer.all_objects
                .select_for_update()
                .get(pk=membership_ref["customer_id"])
            )

        # Keep the lock order consistent:
        # Customer -> CustomerUser -> User.
        if customer:
            memberships = list(
                CustomerUser.objects
                .select_for_update()
                .filter(customer_id=customer.pk)
                .order_by("pk")
            )

        user = User.objects.select_for_update().get(pk=user.pk)

        current_primary_membership = (
            CustomerUser.objects
            .select_for_update()
            .filter(
                user=user,
                is_primary_contact=True,
            )
            .first()
        )

        if current_primary_membership:
            if (
                customer is None
                or current_primary_membership.customer_id != customer.pk
            ):
                raise ValidationError({
                    "detail": (
                        "Primary contact assignment changed concurrently. "
                        "Please try again."
                    )
                })
        else:
            customer = None

        if customer is not None:
            if "phone" in data:
                from apps.accounts.validators import validate_phone_for_country
                from rest_framework import serializers as drf_serializers

                try:
                    user.phone = validate_phone_for_country(
                        value=data["phone"],
                        country=customer.country,
                    )
                except drf_serializers.ValidationError as exc:
                    raise ValidationError({
                        "phone": exc.detail
                    })

        if data:
            for field, value in data.items():
                if field == "phone" and customer is not None:
                    continue
                setattr(user, field, value)

            user.save(
                update_fields=[
                    *data.keys(),
                    "updated_at",
                ]
            )

        if customer is not None:
            UserService._sync_primary_contact_name(
                user=user,
                customer=customer,
            )
            if "phone" in data and customer.phone != user.phone:
                customer.phone = user.phone
                customer.save(
                    update_fields=["phone", "updated_at"]
                )

        return user

    @staticmethod
    @transaction.atomic
    def update_profile(*, user, validated_data):
        return UserService._update_user_fields(
            user=user,
            validated_data=validated_data,
        )

    @staticmethod
    @transaction.atomic
    def update_user(*, user, validated_data):
        old_email = (user.email or "").strip().lower()
        new_email = validated_data.get("email")

        if (
            new_email is not None
            and new_email.strip().lower() != old_email
        ):
            raise ValidationError({
                "email": (
                    "Email cannot be changed through this form. "
                    "Please use the email change verification process."
                )
            })

        data = validated_data.copy()
        data.pop("email", None)

        return UserService._update_user_fields(
            user=user,
            validated_data=data,
        )

    
    @staticmethod
    @transaction.atomic
    def create_user(*, performed_by, validated_data):
        data = validated_data.copy()

        # Read the actor's company ID without acquiring a row lock.
        # This is only a reference lookup, not an authorization check.
        actor_membership_ref = (
            CustomerUser.objects
            .filter(user_id=performed_by.pk)
            .values("customer_id")
            .first()
        )

        requested_customer_id = data.get("customer_id")

        # Lock all existing companies involved before locking users.
        # Sorting avoids inconsistent company-to-company lock order.
        company_ids = set()

        if actor_membership_ref:
            company_ids.add(actor_membership_ref["customer_id"])

        if requested_customer_id:
            company_ids.add(requested_customer_id)

        locked_customers = {}

        for company_id in sorted(company_ids, key=str):
            customer = (
                Customer.all_objects
                .select_for_update()
                .filter(pk=company_id)
                .first()
            )

            if customer:
                locked_customers[company_id] = customer

        # After company locks, lock and reload the actor.
        actor = (
            User.objects
            .select_for_update()
            .get(pk=performed_by.pk)
        )


        if (
            not actor.is_active
            or actor.account_status != User.AccountStatus.ACTIVE
        ):
            raise PermissionDenied(
                "Only active users can create users."
            )

        role_code = data.pop("role_code")
        customer_id = data.pop("customer_id", None)
        customer_data = data.pop("customer", None)

        password = data.pop("password", None)
        data.pop("password_confirm", None)

        role = Role.objects.filter(
            code=role_code,
            is_assignable=True,
        ).first()

        if not role or role.code == "ADMIN":
            raise ValidationError({
                "role_code": "The selected role is not available for assignment."
            })

        is_admin = (
            actor.is_superuser
            or UserRole.objects.filter(
                user=actor,
                role__code="ADMIN",
            ).exists()
        )

        membership = (
            CustomerUser.objects
            .select_for_update()
            .filter(user=actor)
            .first()
        )

        # Confirm the membership still references a company
        # that was locked before the actor.
        if membership:
            if membership.customer_id not in locked_customers:
                raise PermissionDenied(
                    "We couldn't verify your company access. Please try again."
                )

        # A customer membership cannot be used to exercise
        # internal administrator privileges.
        if membership and is_admin:
            raise PermissionDenied(
                "Customer accounts cannot use internal administrator privileges."
            )

        def has_permission(permission_name):
            actor_user_role = (
                UserRole.objects
                .select_related("role")
                .filter(user=actor)
                .first()
            )
            return role_has_permission(
                actor_user_role.role if actor_user_role else None,
                permission_name,
            )

        is_customer_role = role.code == "CUSTOMER"

        if membership:
            # Customer-side creator.
            if not is_customer_role:
                raise PermissionDenied(
                    "Customer users cannot create internal users."
                )

            if not (
                membership.is_primary_contact
                and primary_has_customer_permission(USERS_CREATE_CUSTOMER)
            ):
                raise PermissionDenied(
                    "You do not have permission to create customer users."
                )

            if customer_data:
                raise ValidationError({
                    "customer": "Customer users cannot create a company."
                })

            if customer_id != membership.customer_id:
                raise PermissionDenied(
                    "Customer users can create users only for their own company."
                )

            customer = locked_customers[membership.customer_id]

            if (
                customer.deleted_at is not None
                or not customer.is_active
                or customer.account_status != Customer.AccountStatus.ACTIVE
            ):
                raise PermissionDenied("Your company is inactive.")

        elif is_customer_role:
            # Internal/admin creator creating a customer user.
            if not is_admin and not has_permission(USERS_CREATE_CUSTOMER):
                raise PermissionDenied(
                    "You do not have permission to create customer users."
                )

            if bool(customer_id) == bool(customer_data):
                raise ValidationError({
                    "customer": "Provide exactly one of customer_id or customer."
                })

        else:
            # Internal/admin creator creating an internal user.
            if not is_admin and not has_permission(USERS_CREATE_INTERNAL):
                raise PermissionDenied(
                    "You do not have permission to create internal users."
                )

            if customer_id or customer_data:
                raise ValidationError({
                    "customer": "Customer information is not allowed for internal users."
                })

        if is_customer_role:
            if password:
                raise ValidationError({
                    "password": "Password cannot be provided for customer users."
                })
        elif not password:
            raise ValidationError({
                "password": "A password is required when creating an internal user."
            })

        # Existing user creation workflow.
        user = User(
            **data,
            phone=customer_data.get("phone", "") if customer_data else data.get("phone", ""),
            address=customer_data.get("address", "") if customer_data else data.get("address", ""),
            email_verified=not is_customer_role,
            is_active=not is_customer_role,
            account_status=(
                User.AccountStatus.PENDING_VERIFICATION
                if is_customer_role
                else User.AccountStatus.ACTIVE
            ),
        )

        if password:
            user.set_password(password)
        else:
            user.set_unusable_password()

        user.save()

        UserRole.objects.create(
            user=user,
            role=role,
        )

        customer = None

        if is_customer_role:
            if customer_id:
                customer = locked_customers.get(customer_id)

                if (
                    not customer
                    or customer.deleted_at is not None
                    or not customer.is_active
                    or customer.account_status != Customer.AccountStatus.ACTIVE
                ):
                    raise ValidationError({
                        "customer_id": "Cannot add users to an inactive customer company."
                    })

            elif customer_data:
                company_name = normalize_company_name(customer_data["company_name"])

                with transaction.atomic():
                    customer = Customer.objects.create(
                        company_name=company_name,
                        contact_person=customer_data["contact_person"],
                        email=user.email,
                        phone=customer_data["phone"].strip(),
                        address=customer_data["address"].strip(),
                        state=customer_data["state"].strip(),
                        country=customer_data["country"].strip(),
                        is_active=False,
                        account_status=Customer.AccountStatus.PENDING_VERIFICATION,
                        account_manager=None,
                        notes="",
                    )

            CustomerUser.objects.create(
                customer=customer,
                user=user,
                is_primary_contact=(customer_data is not None),
            )

            AuthenticationService.send_password_setup_email(
                user=user,
                expires_in_hours=72,
            )

        return user


    #user activation / deactivation 
    @staticmethod
    @transaction.atomic
    def set_user_active_status(*, user, is_active, performed_by):
        if not is_active and connection.vendor == "postgresql":
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT pg_advisory_xact_lock(%s)",
                    [73120491],
                )

        actor_membership_ref = (
            CustomerUser.objects
            .filter(user_id=performed_by.pk)
            .values("customer_id")
            .first()
        )
        target_membership_ref = (
            CustomerUser.objects
            .filter(user_id=user.pk)
            .values("customer_id")
            .first()
        )
        company_ids = {
            ref["customer_id"]
            for ref in (
                actor_membership_ref,
                target_membership_ref,
            )
            if ref is not None
        }
        for company_id in sorted(company_ids, key=str):
            try:
                Customer.all_objects.select_for_update().get(
                    pk=company_id
                )
            except Customer.DoesNotExist:
                raise ValidationError({
                    "customer": "Associated customer company not found."
                })

        # Lock and reload the target user.
        locked_users = {}

        for user_id in sorted(
            {user.id, performed_by.id},
            key=str,
        ):
            locked_users[user_id] = (
                User.objects.select_for_update().get(
                    pk=user_id
                )
            )

        user = locked_users[user.id]
        performed_by = locked_users[performed_by.id]
        
        if (
            not performed_by.is_active
            or performed_by.account_status != User.AccountStatus.ACTIVE
        ):
            raise PermissionDenied(
                "Only active users can change account status."
            )
        
        #No user can change their own account status.
        if user.id == performed_by.id:
            raise ValidationError(
                "You cannot change your own account status."
            )

        if user.account_status == User.AccountStatus.PENDING_VERIFICATION:
            raise ValidationError(
                "This user has not completed onboarding. "
                "Pending user cancellation must be handled separately."
            )

        required_permission = (
            USERS_ACTIVATE if is_active else USERS_DEACTIVATE
        )

        is_admin = (
            performed_by.is_superuser
            or UserRole.objects.filter(
                user=performed_by,
                role__code="ADMIN",
            ).exists()
        )
        target_is_admin = (
            user.is_superuser
            or UserRole.objects.filter(
                user=user,
                role__code="ADMIN",
            ).exists()
        )

        # Only an administrator may change an administrator's status.
        if (
            not is_admin
            and target_is_admin
        ): 
            raise PermissionDenied(
                "Only administrators can change administrator account status."
            )

        memberships = {
            membership.user_id: membership
            for membership in (
                CustomerUser.objects
                .select_for_update()
                .select_related("customer")
                .filter(user_id__in=[performed_by.pk, user.pk])
                .order_by("user_id", "pk")
            )
        }

        actor_customer = memberships.get(performed_by.pk)
        target_customer = memberships.get(user.pk)

        if actor_customer is not None and is_admin:
            raise PermissionDenied(
                "Customer accounts cannot use internal administrator privileges."
            )

        # A non-admin must have the specific status permission.
        if not is_admin:
            if actor_customer is not None:
                has_status_perm = (
                    actor_customer.is_primary_contact
                    and primary_has_customer_permission(required_permission)
                )
            else:
                actor_user_role = (
                    UserRole.objects
                    .select_related("role")
                    .filter(user=performed_by)
                    .first()
                )
                has_status_perm = role_has_permission(
                    actor_user_role.role if actor_user_role else None,
                    required_permission,
                )

            if not has_status_perm:
                raise PermissionDenied(
                    "You do not have permission to change user status."
                )

        # Company-level status is managed separately.
        # Individual status changes must not bypass company deactivation.
        if target_customer and (
            target_customer.customer.deleted_at is not None
            or not target_customer.customer.is_active
            or target_customer.customer.account_status
            != Customer.AccountStatus.ACTIVE
        ):
            raise ValidationError(
                "This user's customer company is not active. "
                "Company reactivation must be handled separately."
            )

        if actor_customer and (
            actor_customer.customer.deleted_at is not None
            or not actor_customer.customer.is_active
            or actor_customer.customer.account_status
            != Customer.AccountStatus.ACTIVE
        ):
            raise PermissionDenied(
                "Your company is not active."
            )

        #A customer-side user can manage only eligible users
        #belonging to the same customer company.
        if actor_customer:
            if (
                target_customer is None
                or target_customer.customer_id
                != actor_customer.customer_id
            ):
                raise PermissionDenied(
                    "You can manage users only within your own company."
                )

            if target_customer.is_primary_contact:
                raise PermissionDenied(
                    "Customer users cannot change the primary contact's status."
                )

            if not UserRole.objects.filter(
                user=user,
                role__code="CUSTOMER",
            ).exists():
                raise PermissionDenied(
                    "Customer users cannot manage internal or administrator accounts."
                )

        if (
            not is_active
            and target_customer is not None
            and target_customer.is_primary_contact
        ): 
            raise ValidationError(
                "Transfer the primary contact to another active "
                "customer user before deactivating this account."
            )

        if is_active:
            if (
                user.is_active
                or user.account_status != User.AccountStatus.DEACTIVATED
            ):
                raise ValidationError(
                    "Only a deactivated user can be manually reactivated."
                )
        else:
            if (
                not user.is_active
                or user.account_status != User.AccountStatus.ACTIVE
            ):
                raise ValidationError(
                    "Only an active user can be deactivated."
                )
        
        if is_active and target_customer is not None:
            if target_customer.is_primary_contact:
                raise ValidationError({
                    "detail": (
                        "The primary contact cannot be reactivated through "
                        "individual user activation. Please review the "
                        "primary contact assignment."
                    )
                })

            # Customer user reactivation: email confirmation required.
            if not user.email_verified:
                raise ValidationError(
                    "The customer user's email must be verified "
                    "before reactivation."
                )

            if not user.has_usable_password():
                raise ValidationError(
                    "The customer user must complete password setup "
                    "before reactivation."
                )

            _, raw_token = (
                AuthenticationService.create_email_verification_request(
                    user=user,
                    purpose=EmailVerificationRequest.Purpose.USER_REACTIVATION,
                    requested_by=performed_by,
                )
            )

            EmailJob.objects.create(
                user=user,
                recipient_email=user.email,
                template="email_verification",
                subject="Confirm your account activation",
                payload={
                    "token": raw_token,
                    "user_id": str(user.id),
                },
                status=EmailJob.Status.PENDING,
            )

            return {
                "user": user,
                "activation_pending": True,
            }

        if is_active:
            # Internal user: immediate activation.
            if not user.has_usable_password():
                raise ValidationError(
                    "The internal user must have a configured password "
                    "before activation."
                )

            user.account_status = User.AccountStatus.ACTIVE
            user.is_active = True

        else:
            if not is_active and target_is_admin:
                other_active_admin_exists = (
                    User.objects
                    .filter(
                        is_active=True,
                        account_status=User.AccountStatus.ACTIVE,
                    )
                    .exclude(pk=user.pk)
                    .filter(
                        Q(is_superuser=True)
                        | Q(user_role__role__code="ADMIN")
                    )
                    .exists()
                )

                if not other_active_admin_exists:
                    raise ValidationError(
                        "Cannot deactivate the last active administrator."
                    )

            # Internal or customer user: immediate deactivation.
            user.account_status = User.AccountStatus.DEACTIVATED
            user.is_active = False

            now = timezone.now()

            EmailVerificationRequest.objects.filter(
                user=user,
                used_at__isnull=True,
            ).update(used_at=now)

            PasswordResetRequest.objects.filter(
                user=user,
                used_at__isnull=True,
            ).update(used_at=now)

        user.save(
            update_fields=[
                "is_active",
                "account_status",
                "updated_at",
            ]
        )

        return {
            "user": user,
            "activation_pending": False,
        }
        

    #update user role
    @staticmethod
    @transaction.atomic
    def update_user_role(*, user, role_id, performed_by):
        if not performed_by or not performed_by.is_active or performed_by.account_status != User.AccountStatus.ACTIVE:
            raise PermissionDenied(
                "Only active users can assign roles."
            )

        if user.pk == performed_by.pk:
            raise ValidationError(
                "You cannot change your own role."
            )

        actor_is_customer = CustomerUser.objects.filter(
            user=performed_by
        ).exists()

        if actor_is_customer:
            raise PermissionDenied(
                "Customer users cannot change user roles."
            )

        actor_is_admin = (
            performed_by.is_superuser
            or UserRole.objects.filter(
                user=performed_by,
                role__code="ADMIN",
            ).exists()
        )

        if not actor_is_admin:
            actor_user_role = (
                UserRole.objects
                .select_related("role")
                .filter(user=performed_by)
                .first()
            )
            has_assign_perm = role_has_permission(
                actor_user_role.role if actor_user_role else None,
                ROLES_ASSIGN,
            )
            if not has_assign_perm:
                raise PermissionDenied(
                    "You do not have permission to assign roles."
                )

        target_is_admin = (
            user.is_superuser
            or UserRole.objects.filter(
                user=user,
                role__code="ADMIN",
            ).exists()
        )

        if target_is_admin:
            raise PermissionDenied(
                "Administrator roles cannot be changed through this API."
            )

        role = (
            Role.objects
            .filter(
                id=role_id,
                is_assignable=True,
            )
            .first()
        )

        if not role:
            raise ValidationError(
                "The selected role is not available for assignment."
            )

        if role.code == "ADMIN":
            raise ValidationError(
                "The Admin role cannot be assigned through this API."
            )

        is_customer_user = CustomerUser.objects.filter(
            user=user
        ).exists()

        if is_customer_user:
            raise ValidationError({
                "role_id": (
                    "Customer user roles cannot be changed."
                )
            })

        if role.code == "CUSTOMER":
            raise ValidationError({
                "role_id": (
                    "An internal user cannot be assigned the Customer role."
                )
            })

        user_role = (
            UserRole.objects
            .select_for_update()
            .filter(user=user)
            .first()
        )

        if user_role and user_role.role_id == role.id:
            raise ValidationError({
                "role_id": (
                    "The user is already assigned this role."
                )
            })

        if user_role is None:
            user_role = UserRole.objects.create(
                user=user,
                role=role,
            )
        else:
            user_role.role = role
            user_role.save(update_fields=["role", "updated_at"])

        return user_role
        