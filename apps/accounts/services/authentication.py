from django.db import transaction
from django.db.models import Count

#for email verification :
from apps.notifications.models import EmailJob

import hashlib
import secrets

from datetime import timedelta
from django.utils import timezone
from django.contrib.auth import password_validation
from django.core.exceptions import ValidationError as DjangoValidationError


from apps.accounts.models import (
    User, Role, UserRole,
    EmailVerificationRequest,
    EmailChangeRequest,
    PasswordResetRequest,
)
from apps.customers.models import Customer, CustomerUser, normalize_company_name

from rest_framework.exceptions import (
    AuthenticationFailed,
    ValidationError,
    PermissionDenied,
    ErrorDetail
)
from rest_framework_simplejwt.exceptions import TokenError
from rest_framework_simplejwt.tokens import RefreshToken
from rest_framework_simplejwt.token_blacklist.models import (
    OutstandingToken,
    BlacklistedToken,
)

class AuthenticationService:

    @staticmethod
    def _verification_purpose_allowed(*, user, purpose, customer=None):
        Purpose = EmailVerificationRequest.Purpose
        membership = CustomerUser.objects.filter(user=user).first()

        if purpose == Purpose.INITIAL_VERIFICATION:
            if (
                customer is not None
                or membership is None
                or user.is_active
                or user.email_verified
                or user.account_status != User.AccountStatus.PENDING_VERIFICATION
                or not user.has_usable_password()
            ):
                return False

            company = Customer.all_objects.filter(
                pk=membership.customer_id
            ).first()

            if company is None or company.deleted_at is not None:
                return False

            if membership.is_primary_contact:
                return (
                    not company.is_active
                    and company.account_status
                    == Customer.AccountStatus.PENDING_VERIFICATION
                )

            return (
                company.is_active
                and company.account_status == Customer.AccountStatus.ACTIVE
            )

        if purpose == Purpose.USER_REACTIVATION:
            if (
                customer is not None
                or membership is None
                or user.is_active
                or user.account_status != User.AccountStatus.DEACTIVATED
                or not user.email_verified
                or not user.has_usable_password()
            ):
                return False

            return Customer.objects.filter(
                pk=membership.customer_id,
                is_active=True,
                account_status=Customer.AccountStatus.ACTIVE,
                deleted_at__isnull=True,
            ).exists()

        if purpose == Purpose.COMPANY_REACTIVATION:
            return (
                customer is not None
                and customer.deleted_at is None
                and not customer.is_active
                and customer.account_status == Customer.AccountStatus.DEACTIVATED
                and membership is not None
                and membership.customer_id == customer.pk
                and membership.is_primary_contact
                and user.email_verified
                and user.has_usable_password()
            )

        return False
    

    @staticmethod
    @transaction.atomic
    def create_email_verification_request(
        *,
        user,
        purpose=EmailVerificationRequest.Purpose.INITIAL_VERIFICATION,
        customer=None,
        requested_by=None,
    ):
        Purpose = EmailVerificationRequest.Purpose

        if purpose not in Purpose.values:
            raise ValidationError({
                "purpose": "Invalid email verification purpose."
            })

        if purpose != Purpose.COMPANY_REACTIVATION and customer is not None:
            raise ValidationError({
                "customer": "A company can only be specified for company reactivation."
            })

        if purpose != Purpose.INITIAL_VERIFICATION:
            if requested_by is None:
                raise ValidationError({
                    "requested_by": "An authorized requester is required."
                })
            if (
                not requested_by.is_active
                or requested_by.account_status != User.AccountStatus.ACTIVE
            ):
                raise ValidationError({
                    "requested_by": "The requesting account must be active."
                })

        # Determine company before acquiring locks.
        membership = (
            CustomerUser.objects
            .filter(user_id=user.pk)
            .values("customer_id")
            .first()
        )

        company_id = (
            customer.pk if customer is not None
            else membership["customer_id"] if membership
            else None
        )

        locked_company = None

        if company_id:
            locked_company = Customer.all_objects.select_for_update().get(
                pk=company_id
            )

        locked_user = User.objects.select_for_update().get(
            pk=user.pk
        )

        if purpose == Purpose.COMPANY_REACTIVATION:
            if customer is None:
                raise ValidationError("Invalid activation request.")
            if (
                membership is None
                or membership["customer_id"] != customer.pk
            ):
                raise ValidationError("Invalid activation request.")
            purpose_customer = locked_company
        else:
            purpose_customer = None

        if not AuthenticationService._verification_purpose_allowed(
            user=locked_user,
            purpose=purpose,
            customer=purpose_customer,
        ):
            messages = {
                Purpose.INITIAL_VERIFICATION:
                    "This account is not eligible for initial email verification.",
                Purpose.USER_REACTIVATION:
                    "Customer user activation is unavailable for the current account or company status.",
                Purpose.COMPANY_REACTIVATION:
                    "Company activation is unavailable. Check the company status and primary contact.",
            }
            raise ValidationError({
                "detail": messages[purpose]
            })

        now = timezone.now()

        # Revoke all outstanding verification tokens for this user.
        EmailVerificationRequest.objects.filter(
            user=locked_user,
            # purpose=purpose,
            used_at__isnull=True,
        ).update(used_at=now)

        raw_token = secrets.token_urlsafe(32)

        verification = EmailVerificationRequest.objects.create(
            user=locked_user,
            purpose=purpose,
            customer=purpose_customer,
            requested_by=requested_by,
            token_hash=hashlib.sha256(
                raw_token.encode("utf-8")
            ).hexdigest(),
            expires_at=now + timedelta(hours=24),
        )

        return verification, raw_token      
            

    @staticmethod
    @transaction.atomic
    def register_user(*, validated_data):
        validated_data = validated_data.copy()

        validated_data.pop("password_confirm")
        password = validated_data.pop("password")

        customer = validated_data.pop("customer")
        contact_person = validated_data.pop("contact_person")

        name_parts = contact_person.split(maxsplit=1)
        first_name = name_parts[0]
        last_name = name_parts[1] if len(name_parts) > 1 else ""

        phone = validated_data.pop("phone")
        address = validated_data.pop("address")
        state = validated_data.pop("state")
        country = validated_data.pop("country")

        #1. get fixed customer role
        try : 
            customer_role = Role.objects.get(
                code="CUSTOMER",
                is_assignable = True,
            )
        except Role.DoesNotExist:
            raise ValidationError({
                "detail": "Customer role is not configured."
            })

        #2. create user
        user = User(
            **validated_data,
            first_name=first_name,
            last_name=last_name,
            phone=phone,
            address=address,
            email_verified=False,
            is_active=False,
            account_status=User.AccountStatus.PENDING_VERIFICATION,
        )

        user.set_password(password)
        user.save()

        #3.create the customer company
        with transaction.atomic():
            customer = Customer.objects.create(
                company_name=normalize_company_name(customer),
                contact_person=contact_person,
                email=user.email,
                phone=phone,
                address=address,
                state=state,
                country=country,
                is_active=False,
                account_status=Customer.AccountStatus.PENDING_VERIFICATION,
                account_manager=None,
                notes="",
            )

        #4. Link the first user to the customer 
        CustomerUser.objects.create(
            customer=customer,
            user=user,
            is_primary_contact=True,
        )

        #5. Assign Customer Role
        UserRole.objects.create(
            user=user,
            role=customer_role,
        )

        #6. Create email verification request
        _, raw_token = (
             AuthenticationService.create_email_verification_request(
                  user=user
             )
        )

        #7. Queue verification email
        EmailJob.objects.create(
            user = user,
            recipient_email = user.email,
            template="email_verification",
            subject="Verify your email address",
            payload={
                "token": raw_token,
                "user_id": str(user.id),
            },
            status = EmailJob.Status.PENDING,
        )
        
        return user, customer, raw_token
    

    @staticmethod
    @transaction.atomic
    def verify_email(*, raw_token):
        invalid_message = (
            "This verification link is invalid, expired, or already used. "
            "Please request a new verification link."
        )

        if not isinstance(raw_token, str) or not raw_token.strip():
            raise ValidationError({"token": [invalid_message]})

        token_hash = hashlib.sha256(
            raw_token.encode("utf-8")
        ).hexdigest()

        # Step 1: Find token information without locking.
        # This allows us to identify the company before acquiring locks.
        token_reference = (
            EmailVerificationRequest.objects
            .filter(token_hash=token_hash)
            .values(
                "id",
                "user_id",
                "customer_id",
            )
            .first()
        )

        if token_reference is None:
            if EmailChangeRequest.objects.filter(
                token_hash=token_hash
            ).exists():
                from apps.customers.services.customer_updates import (
                    CustomerUpdateService,
                )

                _, user = CustomerUpdateService.confirm_email_change(
                    raw_token=raw_token
                )
                return {
                    "user": user,
                    "password_setup_required": False,
                    "password_setup_token": None,
                    "activation_purpose": "EMAIL_CHANGE",
                }

            raise ValidationError({"token": [invalid_message]})

        membership_reference = (
            CustomerUser.objects
            .filter(user_id=token_reference["user_id"])
            .values(
                "customer_id",
                "is_primary_contact",
            )
            .first()
        )

        company_id = (
            token_reference["customer_id"]
            or (
                membership_reference["customer_id"]
                if membership_reference
                else None
            )
        )

        # Step 2: Lock Customer -> User -> Verification Token.
        customer = None

        if company_id is not None:
            customer = Customer.all_objects.select_for_update().get(
                pk=company_id
            )

        user = User.objects.select_for_update().get(
            pk=token_reference["user_id"]
        )

        verification_request = (
            EmailVerificationRequest.objects
            .select_for_update()
            .get(pk=token_reference["id"])
        )

        if verification_request.user_id != user.pk:
            raise ValidationError({"token": [invalid_message]})

        # Step 3: Revalidate token after acquiring locks.
        now = timezone.now()

        if (
            verification_request.token_hash != token_hash
            or verification_request.used_at is not None
            or verification_request.expires_at <= now
        ):
            raise ValidationError({"token": [invalid_message]})

        purpose = verification_request.purpose
        Purpose = EmailVerificationRequest.Purpose

        if purpose not in Purpose.values:
            raise ValidationError({"token": [invalid_message]})
        if (
            purpose != Purpose.COMPANY_REACTIVATION
            and verification_request.customer_id is not None
        ):
            raise ValidationError({"token": [invalid_message]})
        if (
            purpose == Purpose.COMPANY_REACTIVATION
            and verification_request.customer_id is None
        ):
            raise ValidationError({"token": [invalid_message]})

        # Recheck membership after acquiring locks.
        membership = (
            CustomerUser.objects
            .filter(user=user)
            .first()
        )

        if membership is None:
            raise ValidationError({"token": [invalid_message]})
        if (
            customer is None
            or membership.customer_id != customer.pk
            or customer.deleted_at is not None
        ):
            raise ValidationError({"token": [invalid_message]})

        if purpose == Purpose.INITIAL_VERIFICATION:

            # Initial verification is only for pending users.
            if (
                user.is_active
                or user.email_verified
                or user.account_status
                != User.AccountStatus.PENDING_VERIFICATION
            ):
                raise ValidationError({"token": [invalid_message]})

            # A user without a usable password must complete setup,
            # rather than being activated by email verification alone.
            if not user.has_usable_password():
                raise ValidationError({
                    "token": [
                        "Please complete password setup using your invitation link."
                    ]
                })

            if membership:
                if customer is None or customer.deleted_at is not None:
                    raise ValidationError({"token": [invalid_message]})

                if membership.is_primary_contact:
                    # The first primary contact may activate a pending company.
                    if (
                        customer.is_active
                        or customer.account_status
                        != Customer.AccountStatus.PENDING_VERIFICATION
                    ):
                        raise ValidationError({"token": [invalid_message]})

                    customer.account_status = Customer.AccountStatus.ACTIVE
                    customer.is_active = True
                    customer.save(
                        update_fields=[
                            "account_status",
                            "is_active",
                            "updated_at",
                        ]
                    )

                else:
                    # Other invited users cannot activate a company.
                    if (
                        not customer.is_active
                        or customer.account_status
                        != Customer.AccountStatus.ACTIVE
                    ):
                        raise ValidationError({"token": [invalid_message]})

            user.email_verified = True
            user.account_status = User.AccountStatus.ACTIVE
            user.is_active = True

        elif purpose == Purpose.USER_REACTIVATION:

            # Fresh confirmation is mandatory, regardless of whether
            # email_verified was already True.
            if (
                user.is_active
                or user.account_status
                != User.AccountStatus.DEACTIVATED
            ):
                raise ValidationError({"token": [invalid_message]})

            # Only customer users use email-confirmed reactivation.
            if membership is None:
                raise ValidationError({"token": [invalid_message]})

            # Primary contact must be transferred before individual deactivation.
            if membership.is_primary_contact:
                raise ValidationError({"token": [invalid_message]})

            if (
                customer is None
                or customer.deleted_at is not None
                or not customer.is_active
                or customer.account_status
                != Customer.AccountStatus.ACTIVE
            ):
                raise ValidationError({"token": [invalid_message]})

            if not user.has_usable_password():
                raise ValidationError({
                    "token": [
                        "Password setup must be completed before activation."
                    ]
                })

            if not user.email_verified:
                raise ValidationError({"token": [invalid_message]})
            
            user.account_status = User.AccountStatus.ACTIVE
            user.is_active = True

        elif purpose == Purpose.COMPANY_REACTIVATION:

            # A company token must explicitly identify the company.
            if (
                customer is None
                or verification_request.customer_id != customer.pk
                or customer.deleted_at is not None
            ):
                raise ValidationError({"token": [invalid_message]})

            if not user.email_verified or not user.has_usable_password():
                raise ValidationError({"token": [invalid_message]})

            if (
                customer.is_active
                or customer.account_status
                != Customer.AccountStatus.DEACTIVATED
            ):
                raise ValidationError({"token": [invalid_message]})

            # Only the current primary contact may confirm.
            if (
                membership is None
                or membership.customer_id != customer.pk
                or not membership.is_primary_contact
            ):
                raise ValidationError({"token": [invalid_message]})

            customer.account_status = Customer.AccountStatus.ACTIVE
            customer.is_active = True
            customer.save(
                update_fields=[
                    "account_status",
                    "is_active",
                    "updated_at",
                ]
            )

        else:
            raise ValidationError({"token": [invalid_message]})

        # Step 4: Save individual user status only when appropriate.
        if purpose != Purpose.COMPANY_REACTIVATION:
            user.save(
                update_fields=[
                    "email_verified",
                    "account_status",
                    "is_active",
                    "updated_at",
                ]
            )

        # Step 5: Consume the token.
        verification_request.used_at = now
        verification_request.save(
            update_fields=[
                "used_at",
                "updated_at",
            ]
        )

        if purpose == Purpose.COMPANY_REACTIVATION:
            EmailVerificationRequest.objects.filter(
                customer=customer,
                purpose=Purpose.COMPANY_REACTIVATION,
                used_at__isnull=True,
            ).update(used_at=now)
        else:
            EmailVerificationRequest.objects.filter(
                user=user,
                used_at__isnull=True,
            ).update(used_at=now)

        result = {
            "user": user,
            "password_setup_required": False,
            "password_setup_token": None,
            "activation_purpose": purpose,
        }

        if purpose == Purpose.INITIAL_VERIFICATION and user.is_active:
            refresh = RefreshToken.for_user(user)
            result["refresh"] = str(refresh)
            result["access"] = str(refresh.access_token)

        return result


    @staticmethod
    @transaction.atomic
    def resend_email_verification(*, email):
        user = (
            User.objects
            .filter(email__iexact=email)
            .first()
        )

        # Same behaviour for every email: never reveal whether an account
        # exists. Mail is only queued for pending, unverified users who
        # already have a password. Invited users (no password yet) must
        # use their password-setup link, which also verifies the email.
        if (
            not user
            or user.account_status != User.AccountStatus.PENDING_VERIFICATION
            or user.email_verified
            or not user.has_usable_password()
        ):
            return None

        membership = (
            CustomerUser.objects
            .select_related("customer")
            .filter(user=user)
            .first()
        )
        if membership is None:
            return None
        customer = membership.customer
        if customer.deleted_at is not None:
            return None
        if membership.is_primary_contact:
            eligible_company = (
                not customer.is_active
                and customer.account_status
                == Customer.AccountStatus.PENDING_VERIFICATION
            )
        else:
            eligible_company = (
                customer.is_active
                and customer.account_status
                == Customer.AccountStatus.ACTIVE
            )
        if not eligible_company:
            return None

        try:
            _, raw_token = (
                AuthenticationService.create_email_verification_request(
                    user=user
                )
            )
        except ValidationError:
            return None

        EmailJob.objects.create(
            user=user,
            recipient_email=user.email,
            template="email_verification",
            subject="Verify your email address",
            payload={
                "token": raw_token,
                "user_id": str(user.id),
            },
            status="PENDING",
        )

        return raw_token



    @staticmethod
    def login_user(*, email, password):
        user = User.objects.filter(
            email__iexact = email
        ).first()

        # Check the password FIRST so nobody without the correct password
        # can learn whether an account exists, is inactive or unverified.
        if not user or not user.check_password(password):
            raise AuthenticationFailed(
                "Invalid email or password."
            )

        if user.account_status == User.AccountStatus.DEACTIVATED:
            raise AuthenticationFailed(
                "This account is deactivated. Please request reactivation."
            )

        if user.account_status == User.AccountStatus.PENDING_VERIFICATION:
            raise AuthenticationFailed(
                "Please verify your email before logging in."
            )

        if not user.is_active:
            raise AuthenticationFailed(
                "This account is inactive."
            )

        membership = CustomerUser.objects.filter(
            user=user
        ).select_related("customer").first()
        if membership:
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


        # Record the time of the successful login.
        user.last_login = timezone.now()
        user.save(update_fields=["last_login", "updated_at"])

        refresh = RefreshToken.for_user(user)

        return {
            "refresh": str(refresh),
            "access": str(refresh.access_token),
            "user": user,
        }

    #logout
    @staticmethod
    def logout_user(*, user, refresh_token):
        try: 
            refresh = RefreshToken(refresh_token)
            token_user_id = refresh.get("user_id")

            if str(token_user_id) != str(user.pk):
                raise PermissionDenied(
                    "Refresh token does not belong to the authenticated user."
                )
            refresh.blacklist()

        except TokenError:
            raise ValidationError({
                "refresh": "Invalid, expired, or already blacklisted refresh token."
            })


    #change password
    @staticmethod
    @transaction.atomic
    def change_password(*, user, current_password, new_password):
        membership_ref = (
            CustomerUser.objects
            .filter(user_id=user.pk)
            .values("customer_id")
            .first()
        )
        customer = None
        if membership_ref:
            customer = Customer.all_objects.select_for_update().get(
                pk=membership_ref["customer_id"]
            )
        user = User.objects.select_for_update().get(pk=user.pk)

        if (
            not user.is_active
            or user.account_status != User.AccountStatus.ACTIVE
        ):
            raise AuthenticationFailed(
                "This account is inactive."
            )

        if customer is not None:
            if (
                customer.deleted_at is not None
                or not customer.is_active
                or customer.account_status != Customer.AccountStatus.ACTIVE
            ):
                raise AuthenticationFailed(
                    "Your customer company is inactive."
                )
            if not user.email_verified:
                raise AuthenticationFailed(
                    "Your email is not verified."
                )

        if not user.check_password(current_password):
            raise ValidationError({
                "current_password": "Current password is incorrect."
            })

        user.set_password(new_password)
        user.save(update_fields=["password", "updated_at"])
       
        now = timezone.now()

        # 1. Cancel any unused password reset links
        PasswordResetRequest.objects.filter(
            user=user,
            used_at__isnull=True,
        ).update(used_at=now)

        # 2. Blacklist all existing refresh tokens for this user
        unblacklisted_tokens = OutstandingToken.objects.filter(
            user=user
        ).exclude(
            blacklistedtoken__isnull=False
        )
        BlacklistedToken.objects.bulk_create(
            [BlacklistedToken(token=t) for t in unblacklisted_tokens],
            ignore_conflicts=True,
        )
        
        return user


    #forgot password 
    @staticmethod
    @transaction.atomic
    def forgot_password(*, email):
        user = User.objects.filter(
            email__iexact=email,
            is_active=True,
            account_status=User.AccountStatus.ACTIVE,
        ).first()

        if not user or not user.has_usable_password():
            return None
        
        membership_ref = (
            CustomerUser.objects
            .filter(user_id=user.pk)
            .values("customer_id")
            .first()
        )
        if membership_ref and not user.email_verified:
            return None
        
        customer = None
        if membership_ref:
            customer = Customer.all_objects.select_for_update().filter(
                pk=membership_ref["customer_id"]
            ).first()
            if customer is None:
                return None

        user = User.objects.select_for_update().get(pk=user.pk)

        if (
            not user.is_active
            or user.account_status != User.AccountStatus.ACTIVE
        ):
            return None

        if customer is not None:
            if (
                customer.deleted_at is not None
                or not customer.is_active
                or customer.account_status != Customer.AccountStatus.ACTIVE
            ):
                return None

        now = timezone.now()


        # Invalidate previous unused reset requests.
        PasswordResetRequest.objects.filter(
            user=user,
            used_at__isnull=True,
        ).update(used_at=now)

        raw_token = secrets.token_urlsafe(32)

        token_hash = hashlib.sha256(
            raw_token.encode("utf-8")
        ).hexdigest()

        expires_at = now + timedelta(hours=1)

        PasswordResetRequest.objects.create(
            user=user,
            purpose=PasswordResetRequest.Purpose.PASSWORD_RESET,
            token_hash=token_hash,
            expires_at=expires_at,
        )

        EmailJob.objects.create(
            user=user,
            recipient_email=user.email,
            template="password_reset",
            subject="Reset your password",
            payload={
                "token": raw_token,
                "user_id": str(user.id),
            },
            status=EmailJob.Status.PENDING,
        )

        return raw_token


    #RESET PASSWORD
    @staticmethod
    @transaction.atomic
    def reset_password(*, raw_token, new_password):
        invalid_message = (
            "Invalid or already used password reset token."
        )

        if not isinstance(raw_token, str) or not raw_token.strip():
            raise ValidationError({"token": [invalid_message]})
      
        token_hash = hashlib.sha256(
            raw_token.encode("utf-8")
        ).hexdigest()

        reference = (
            PasswordResetRequest.objects
            .filter(
                token_hash=token_hash,
                purpose=PasswordResetRequest.Purpose.PASSWORD_RESET,
            )
            .values("id", "user_id")
            .first()
        )
        if reference is None:
            raise ValidationError({"token": [invalid_message]})

        membership_reference = (
            CustomerUser.objects
            .filter(user_id=reference["user_id"])
            .values("customer_id")
            .first()
        )
        customer = None
        if membership_reference:
            customer = Customer.all_objects.select_for_update().get(
                pk=membership_reference["customer_id"]
            )
        user = User.objects.select_for_update().get(
            pk=reference["user_id"]
        )
        reset_request = (
            PasswordResetRequest.objects
            .select_for_update()
            .get(pk=reference["id"])
        )

        now = timezone.now()
        if (
            reset_request.user_id != user.pk
            or reset_request.token_hash != token_hash
            or reset_request.purpose
            != PasswordResetRequest.Purpose.PASSWORD_RESET
            or reset_request.used_at is not None
            or reset_request.expires_at <= now
        ):
            raise ValidationError({"token": [invalid_message]})

        if (
            not user.is_active
            or user.account_status != User.AccountStatus.ACTIVE
        ):
            raise ValidationError({
                "token": [
                    "Password reset is unavailable for this account."
                ]
            })

        if customer is not None:
            if (
                customer.deleted_at is not None
                or not customer.is_active
                or customer.account_status
                != Customer.AccountStatus.ACTIVE
            ):
                raise ValidationError({
                    "token": [
                        "Password reset is unavailable for this account."
                    ]
                })
            if not user.email_verified:
                raise ValidationError({
                    "token": [
                        "Password reset is unavailable for this account."
                    ]
                })


        try:
            password_validation.validate_password(new_password, user=user)
        except DjangoValidationError as exc:
            raise ValidationError({"new_password": list(exc.messages)})


        user.set_password(new_password)
        user.save(update_fields=["password", "updated_at"])

        reset_request.used_at = now
        reset_request.save(
            update_fields=["used_at", "updated_at"]
        )

        # Invalidate other outstanding password tokens.
        PasswordResetRequest.objects.filter(
            user=user,
            used_at__isnull=True,
        ).update(used_at=now)

        # 2. Blacklist all existing refresh tokens for this user so when password is changed all device token removes
        unblacklisted_tokens = OutstandingToken.objects.filter(
            user=user
        ).exclude(
            blacklistedtoken__isnull=False
        )
        BlacklistedToken.objects.bulk_create(
            [BlacklistedToken(token=t) for t in unblacklisted_tokens],
            ignore_conflicts=True,
        )

        return user


    @staticmethod
    @transaction.atomic
    def create_password_setup_request(*, user, expires_in_hours=72):
        membership_ref = (
            CustomerUser.objects
            .filter(user_id=user.pk)
            .values("customer_id")
            .first()
        )
        locked_customer = None
        if membership_ref:
            locked_customer = Customer.all_objects.select_for_update().get(
                pk=membership_ref["customer_id"]
            )
        user = User.objects.select_for_update().get(pk=user.pk)

        if (
            user.account_status != User.AccountStatus.PENDING_VERIFICATION
            or user.is_active
        ):
            raise ValidationError(
                "Password setup is available only for pending accounts."
            )

        membership = (
            CustomerUser.objects
            .filter(user=user)
            .first()
        )

        if membership and (
            locked_customer is None
            or membership.customer_id != locked_customer.pk
        ):
            raise ValidationError("Invalid customer membership.")

        if membership:
            customer = locked_customer

            company_active = (
                customer.is_active
                and customer.account_status == Customer.AccountStatus.ACTIVE
                and customer.deleted_at is None
            )

            initial_primary_setup = (
                membership.is_primary_contact
                and not customer.is_active
                and customer.account_status
                == Customer.AccountStatus.PENDING_VERIFICATION
                and customer.deleted_at is None
                and user.account_status
                == User.AccountStatus.PENDING_VERIFICATION
            )

            if not company_active and not initial_primary_setup:
                raise ValidationError(
                    "Cannot create a password setup link for this customer."
                )

        if user.has_usable_password():
            raise ValidationError(
                "Password setup is not required for this account."
            )

        now = timezone.now()

        # Invalidate previous unused reset request
        PasswordResetRequest.objects.filter(
            user=user,
            used_at__isnull=True,
        ).update(
            used_at=now
        )

        raw_token = secrets.token_urlsafe(32)

        token_hash = hashlib.sha256(
            raw_token.encode("utf-8")
        ).hexdigest()

        expires_at = now + timedelta(hours=expires_in_hours)

        PasswordResetRequest.objects.create(
            user=user,
            purpose=PasswordResetRequest.Purpose.PASSWORD_SETUP,
            token_hash=token_hash,
            expires_at=expires_at,
        )

        return raw_token

    @staticmethod
    @transaction.atomic
    def setup_password(*, raw_token, new_password):
        invalid_message = (
            "Invalid or already used password setup token."
        )

        if not isinstance(raw_token, str) or not raw_token.strip():
            raise ValidationError({"token": [invalid_message]})

        token_hash = hashlib.sha256(
            raw_token.encode("utf-8")
        ).hexdigest()

        reference = (
            PasswordResetRequest.objects
            .filter(
                token_hash=token_hash,
                purpose=PasswordResetRequest.Purpose.PASSWORD_SETUP,
            )
            .values("id", "user_id")
            .first()
        )
        if reference is None:
            raise ValidationError({"token": [invalid_message]})

        membership_reference = (
            CustomerUser.objects
            .filter(user_id=reference["user_id"])
            .values("customer_id")
            .first()
        )
        customer = None
        if membership_reference:
            customer = Customer.all_objects.select_for_update().get(
                pk=membership_reference["customer_id"]
            )
        user = User.objects.select_for_update().get(
            pk=reference["user_id"]
        )
        setup_request = (
            PasswordResetRequest.objects
            .select_for_update()
            .get(pk=reference["id"])
        )

        now = timezone.now()

        if (
            setup_request.user_id != user.pk
            or setup_request.token_hash != token_hash
            or setup_request.purpose
            != PasswordResetRequest.Purpose.PASSWORD_SETUP
            or setup_request.used_at is not None
        ):
            raise ValidationError({"token": [invalid_message]})

        latest_setup_request = (
            PasswordResetRequest.objects
            .filter(
                user=user,
                purpose=PasswordResetRequest.Purpose.PASSWORD_SETUP,
            )
            .order_by("-created_at", "-pk")
            .first()
        )

        if (
            latest_setup_request is None
            or latest_setup_request.pk != setup_request.pk
        ):
            raise ValidationError({"token": [invalid_message]})

        if (
            user.is_active
            or user.account_status
            != User.AccountStatus.PENDING_VERIFICATION
            or user.has_usable_password()
        ):
            raise ValidationError({"token": [invalid_message]})

        if customer is None:
            raise ValidationError({
                "token": [
                    "Password setup requires a valid customer membership."
                ]
            })

        if customer.deleted_at is not None:
            raise ValidationError({"token": [invalid_message]})

        membership = CustomerUser.objects.filter(
            user=user,
            customer=customer,
        ).first()

        if membership is None:
            raise ValidationError({"token": [invalid_message]})

        if customer.account_status == Customer.AccountStatus.DEACTIVATED:
            raise ValidationError({
                "token": [
                    "This customer company is deactivated."
                ]
            })

        if membership.is_primary_contact:
            if customer.account_status == Customer.AccountStatus.PENDING_VERIFICATION:
                if customer.is_active:
                    raise ValidationError({
                        "token": [
                            "Invalid customer activation state."
                        ]
                    })

        company_active = (
            customer.deleted_at is None
            and customer.is_active
            and customer.account_status == Customer.AccountStatus.ACTIVE
        )

        initial_primary_setup = (
            customer.deleted_at is None
            and membership.is_primary_contact
            and not customer.is_active
            and customer.account_status
            == Customer.AccountStatus.PENDING_VERIFICATION
        )

        if not company_active and not initial_primary_setup:
            raise ValidationError({
                "token": [
                    "The customer company is not active."
                ]
            })

        if setup_request.expires_at <= now:
            raise ValidationError({
                "token": [
                    ErrorDetail(
                        "Your password setup link has expired. "
                        "Request a new setup link to continue.",
                        code="SETUP_TOKEN_EXPIRED",
                    )
                ]
            })

        if membership.is_primary_contact:
            if customer.account_status == Customer.AccountStatus.PENDING_VERIFICATION:
                customer.account_status = Customer.AccountStatus.ACTIVE
                customer.is_active = True
                customer.save(
                    update_fields=[
                        "account_status",
                        "is_active",
                        "updated_at",
                    ]
                )

        if (
            not customer.is_active
            or customer.account_status != Customer.AccountStatus.ACTIVE
        ):
            raise ValidationError({
                "token": [
                    "The customer company is not active."
                ]
            })
        

        try:
            password_validation.validate_password(new_password, user=user)
        except DjangoValidationError as exc:
            raise ValidationError({"new_password": list(exc.messages)})

        user.set_password(new_password)

        user.email_verified = True
        user.account_status = User.AccountStatus.ACTIVE
        user.is_active = True
        user.save(
            update_fields=[
                "password",
                "email_verified",
                "updated_at",
                "account_status",
                "is_active",
            ]
        )

        setup_request.used_at = now
        setup_request.save(
            update_fields=[
                "used_at",
                "updated_at",
            ]
        )

        EmailVerificationRequest.objects.filter(
            user=user,
            used_at__isnull=True,
        ).update(used_at=now)

        # Invalidate other outstanding password tokens.
        PasswordResetRequest.objects.filter(
            user=user,
            used_at__isnull=True,
        ).update(used_at=now)

        return user

    @staticmethod
    @transaction.atomic
    def send_password_setup_email(
        *,
        user,
        expires_in_hours=72,
        is_resend=False,
    ):
        raw_token = AuthenticationService.create_password_setup_request(
            user=user,
            expires_in_hours=expires_in_hours,
        )

        EmailJob.objects.create(
            user=user,
            recipient_email=user.email,
            template="password_setup",
            subject="Set up your password",
            payload={
                "token": raw_token,
                "user_id": str(user.id),
                "is_resend": is_resend,
            },
            status=EmailJob.Status.PENDING,
        )

        return raw_token

    
    @staticmethod
    @transaction.atomic
    def resend_expired_password_setup(*, raw_token):
        invalid_message = "Invalid or unavailable password setup token."

        if not isinstance(raw_token, str) or not raw_token.strip():
            raise ValidationError({"token": [invalid_message]})

        token_hash = hashlib.sha256(
            raw_token.encode("utf-8")
        ).hexdigest()

        reference = (
            PasswordResetRequest.objects
            .filter(
                token_hash=token_hash,
                purpose=PasswordResetRequest.Purpose.PASSWORD_SETUP,
            )
            .values("id", "user_id")
            .first()
        )

        if reference is None:
            raise ValidationError({"token": [invalid_message]})

        membership_ref = (
            CustomerUser.objects
            .filter(user_id=reference["user_id"])
            .values("customer_id")
            .first()
        )

        if membership_ref is None:
            raise ValidationError({"token": [invalid_message]})

        customer = (
            Customer.all_objects
            .select_for_update()
            .filter(pk=membership_ref["customer_id"])
            .first()
        )

        if customer is None:
            raise ValidationError({"token": [invalid_message]})


        # Lock the user to serialize concurrent resend requests.
        user = (
            User.objects
            .select_for_update()
            .filter(pk=reference["user_id"])
            .first()
        )

        if user is None:
            raise ValidationError({"token": [invalid_message]})

        setup_request = (
            PasswordResetRequest.objects
            .select_for_update()
            .filter(pk=reference["id"], user=user)
            .first()
        )

        if setup_request is None or setup_request.used_at is not None:
            raise ValidationError({"token": [invalid_message]})

        latest_setup_request = (
            PasswordResetRequest.objects
            .filter(
                user=user,
                purpose=PasswordResetRequest.Purpose.PASSWORD_SETUP,
            )
            .order_by("-created_at", "-pk")
            .first()
        )

        if (
            latest_setup_request is None
            or latest_setup_request.pk != setup_request.pk
        ):
            raise ValidationError({"token": [invalid_message]})


        # Reject accounts that have completed setup or are no longer eligible.
        if (
            user.has_usable_password()
            or user.is_active
            or user.account_status != User.AccountStatus.PENDING_VERIFICATION
        ):
            raise ValidationError({"token": [invalid_message]})

        membership = CustomerUser.objects.filter(
            user=user,
            customer=customer,
        ).first()

        if membership is None:
            raise ValidationError({"token": [invalid_message]})

        company_active = (
            customer.deleted_at is None
            and customer.is_active
            and customer.account_status == Customer.AccountStatus.ACTIVE
        )

        initial_primary_setup = (
            customer.deleted_at is None
            and membership.is_primary_contact
            and not customer.is_active
            and customer.account_status
            == Customer.AccountStatus.PENDING_VERIFICATION
        )

        if not company_active and not initial_primary_setup:
            raise ValidationError({"token": [invalid_message]})

        now = timezone.now()

        # Only expired tokens can request a replacement.
        if setup_request.expires_at > now:
            raise ValidationError({
                "token": [
                    "This setup link is still valid. Please use it."
                ]
            })

        # Limit replacement emails to three per rolling hour.
        one_hour_ago = now - timedelta(hours=1)

        recent_resends = EmailJob.objects.filter(
            user=user,
            template="password_setup",
            created_at__gte=one_hour_ago,
            payload__is_resend=True,
        ).count()

        if recent_resends >= 3:
            raise ValidationError({
                "token": [
                    "Too many setup link requests. Please try again later."
                ]
            })

        # Reuse the existing token generation and email queue workflow.
        raw_token = AuthenticationService.send_password_setup_email(
            user=user,
            expires_in_hours=72,
            is_resend=True,
        )

        return user, raw_token
