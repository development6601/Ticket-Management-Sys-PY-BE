from django.conf import settings


class EmailService:

    @staticmethod
    def build_verification_url(*, token):
        frontend_url = settings.FRONTEND_URL.rstrip("/")

        return (
            f"{frontend_url}/verify-email"
            f"?token={token}"
        )

    @staticmethod
    def build_verification_email(*, token):
        verification_url = EmailService.build_verification_url(
            token=token
        )

        return {
            "subject": "Verify your email address",
            "body": (
                "Please verify your email address by opening this link:\n\n"
                f"{verification_url}\n\n"
                "This link will expire in 24 hours."
            ),
        }

    @staticmethod
    def build_email_change_url(*, token):
        return EmailService.build_verification_url(token=token)

    @staticmethod
    def build_email_change_email(*, token):
        confirmation_url = EmailService.build_email_change_url(
            token=token
        )

        return {
            "subject": "Verify your new email address",
            "body": (
                "Please confirm your new email address by opening this link:\n\n"
                f"{confirmation_url}\n\n"
                "This link will expire in 24 hours."
            ),
        }
