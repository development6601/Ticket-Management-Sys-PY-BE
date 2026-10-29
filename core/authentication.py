from rest_framework_simplejwt.authentication import JWTAuthentication
from rest_framework.exceptions import AuthenticationFailed

from apps.accounts.models import User
from apps.customers.models import Customer, CustomerUser


class CompanyAwareJWTAuthentication(JWTAuthentication):

    def get_user(self, validated_token):
        user = super().get_user(validated_token)

        if (
            not user.is_active
            or user.account_status != User.AccountStatus.ACTIVE
        ):
            raise AuthenticationFailed(
                "This user account is not active."
            )

        inactive_company_exists = (
            CustomerUser.objects
            .filter(user=user)
            .exclude(
                customer__is_active=True,
                customer__account_status=Customer.AccountStatus.ACTIVE,
                customer__deleted_at__isnull=True,
            )
            .exists()
        )

        if inactive_company_exists:
            raise AuthenticationFailed(
                "Your customer company is not active."
            )

        return user