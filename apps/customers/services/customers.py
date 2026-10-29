from django.db import transaction
from django.utils import timezone

from rest_framework.exceptions import (
    ValidationError,
    PermissionDenied,
    NotFound,
)

from apps.accounts.models import (
    User,
    UserRole,
    EmailChangeRequest,
    EmailVerificationRequest,
    PasswordResetRequest,
)
from apps.accounts.services.authentication import AuthenticationService
from apps.customers.models import Customer, CustomerUser
from apps.notifications.models import EmailJob


class CustomerStatusService:

    @staticmethod
    @transaction.atomic
    def set_status(*, customer_id, is_active, performed_by):
        # Company lock comes before user locks.
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

        actor = User.objects.select_for_update().get(
            pk=performed_by.pk
        )

        if (
            not actor.is_active
            or actor.account_status != User.AccountStatus.ACTIVE
        ):
            raise PermissionDenied(
                "Your account must be active to manage company status."
            )

        is_admin = (
            actor.is_superuser
            or UserRole.objects.filter(
                user=actor,
                role__code="ADMIN",
            ).exists()
        )

        # Keep company status restricted to internal administrators.
        if (
            CustomerUser.objects.filter(user=actor).exists()
            or not is_admin
        ):
            raise PermissionDenied(
                "Only an internal administrator can change company status."
            )

        if customer.account_status == Customer.AccountStatus.PENDING_VERIFICATION:
            raise ValidationError({
                "detail": (
                    "This company has not completed onboarding. "
                    "The primary contact must complete initial verification."
                )
            })

        if is_active:
            if (
                customer.is_active
                or customer.account_status
                != Customer.AccountStatus.DEACTIVATED
            ):
                raise ValidationError({
                    "detail": "Only a deactivated company can be reactivated."
                })

            primary = (
                CustomerUser.objects
                .filter(
                    customer=customer,
                    is_primary_contact=True,
                )
                .select_related("user")
                .first()
            )

            if primary is None:
                raise ValidationError({
                    "detail": (
                        "Assign a primary contact before requesting "
                        "company activation."
                    )
                })

            primary_user = User.objects.select_for_update().get(
                pk=primary.user_id
            )

            if (
                not primary_user.email_verified
                or not primary_user.has_usable_password()
            ):
                raise ValidationError({
                    "detail": (
                        "The primary contact must have a verified email "
                        "and configured password."
                    )
                })

            _, raw_token = (
                AuthenticationService.create_email_verification_request(
                    user=primary_user,
                    purpose=(
                        EmailVerificationRequest.Purpose.COMPANY_REACTIVATION
                    ),
                    customer=customer,
                    requested_by=actor,
                )
            )

            EmailJob.objects.create(
                user=primary_user,
                recipient_email=primary_user.email,
                template="email_verification",
                subject="Confirm company activation",
                payload={
                    "token": raw_token,
                    "user_id": str(primary_user.pk),
                    "customer_id": str(customer.pk),
                },
                status=EmailJob.Status.PENDING,
            )

            return {
                "customer": customer,
                "activation_pending": True,
            }

        # Immediate company deactivation.
        if (
            not customer.is_active
            or customer.account_status != Customer.AccountStatus.ACTIVE
        ):
            raise ValidationError({
                "detail": "Only an active company can be deactivated."
            })

        customer.is_active = False
        customer.account_status = Customer.AccountStatus.DEACTIVATED
        customer.save(
            update_fields=[
                "is_active",
                "account_status",
                "updated_at",
            ]
        )

        now = timezone.now()

        user_ids = CustomerUser.objects.filter(
            customer=customer
        ).values_list("user_id", flat=True)

        EmailVerificationRequest.objects.filter(
            user_id__in=user_ids,
            used_at__isnull=True,
        ).update(
            used_at=now,
            updated_at=now,
        )

        PasswordResetRequest.objects.filter(
            user_id__in=user_ids,
            used_at__isnull=True,
        ).update(
            used_at=now,
            updated_at=now,
        )

        EmailChangeRequest.objects.filter(
            customer=customer,
            used_at__isnull=True,
        ).update(
            used_at=now,
            updated_at=now,
        )

        return {
            "customer": customer,
            "activation_pending": False,
        }
