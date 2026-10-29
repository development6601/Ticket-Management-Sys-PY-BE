from drf_spectacular.utils import extend_schema, OpenApiExample, OpenApiParameter, OpenApiTypes, PolymorphicProxySerializer
from rest_framework import status 
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework.throttling import ScopedRateThrottle

from apps.accounts.api.serializers.auth import *

from apps.accounts.services.authentication import AuthenticationService
from apps.accounts.api.serializers.users import UserResponseSerializer
from apps.accounts.models import EmailVerificationRequest

from django.conf import settings
from django.db import transaction


class RegisterView(APIView):
    permission_classes = [AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "auth_register"

    @extend_schema(
            tags = ["Authentication"],
            request=RegisterSerializer,
            responses={
                201: RegisterResponseSerializer,
            },
            summary="Register a new user",
            description="Create a new user account using email and password.",
    )

    def post(self, request):
        serializer = RegisterSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        user, customer, raw_token = AuthenticationService.register_user(
            validated_data=serializer.validated_data
        )

        response_serializer = RegisterDataSerializer(
            {
                "customer": customer,
                "primary_user": user,
                "next_step": "VERIFY_EMAIL",
            }
        )

        response_data = dict(response_serializer.data)
        if getattr(settings, "EXPOSE_TEST_TOKENS", False):
            response_data["_test_token"] = raw_token
            response_data["_test_verify_url"] = f"/api/v1/auth/verify-email/?token={raw_token}"

        return Response(
            {
                "success": True,
                "message": "Registration successful. Please verify your email.",
                "data": response_data,
            },
            status=status.HTTP_201_CREATED,
        )


#for email verification
from rest_framework.exceptions import ValidationError
from django.core.exceptions import ValidationError as DjangoValidationError

class VerifyEmailView(APIView):
    permission_classes = [AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "auth_verify_email"

    @extend_schema(
        tags=["Authentication"],
        parameters=[
            OpenApiParameter(
                name="token",
                type=OpenApiTypes.STR,
                location=OpenApiParameter.QUERY,
                required=True,
                description="Email verification token received in the verification email.",
            ),
        ],
        responses={
            200: PolymorphicProxySerializer(
                component_name="VerifyEmailResult",
                serializers=[
                    VerifyEmailResponseSerializer,
                    VerifyEmailPasswordSetupResponseSerializer,
                ],
                resource_type_field_name=None,
            )
        },
        summary="Verify user email",
        description=(
            "Verify a user's email address using the verification token "
            "and automatically authenticate the user."
        ),
    )

    def get(self, request):
        serializer = VerifyEmailSerializer(data=request.query_params)
        serializer.is_valid(raise_exception=True)

        try:
            result = AuthenticationService.verify_email(
                raw_token=serializer.validated_data["token"]
            )
        except ValidationError as exc:
            if isinstance(exc.detail, list):
                raise ValidationError({"token": exc.detail})
            raise


        # Passwordless (or inactive) users get no login tokens.
        if "access" not in result:
            if result.get("password_setup_required"):
                message = "Email verified. Please set your password."
            else:
                purpose = result.get("activation_purpose")

                if purpose == EmailVerificationRequest.Purpose.USER_REACTIVATION:
                    message = "Your account has been reactivated successfully."

                elif purpose == EmailVerificationRequest.Purpose.COMPANY_REACTIVATION:
                    message = "Your company has been reactivated successfully."

                elif purpose == "EMAIL_CHANGE":
                    message = "Email address updated successfully."

                else:
                    message = "Email verified."

            return Response(
                {
                    "success": True,
                    "message": message,
                    "data": {
                        "password_setup_required": result[
                            "password_setup_required"
                        ],
                        "user": UserResponseSerializer(
                            result["user"]
                        ).data,
                    },
                },
                status=status.HTTP_200_OK,
            )

        return Response(
            {
                "success": True,
                "message": "Email verified successfully.",
                "data": {
                    "access": result["access"],
                    "refresh": result["refresh"],
                    "user": UserResponseSerializer(
                        result["user"]
                    ).data,
                },
            },
            status=status.HTTP_200_OK,
        )


class ResendVerificationView(APIView):
    permission_classes = [AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "auth_resend_verification"

    @extend_schema(
        tags=["Authentication"],
        request=ResendVerificationSerializer,
        responses={200: ResendVerificationResponseSerializer},
        summary="Resend email verification",
        description="Send a new email verification link.",
    )
    def post(self, request):
        serializer = ResendVerificationSerializer(
            data=request.data
        )
        serializer.is_valid(raise_exception=True)

        try:
            raw_token = AuthenticationService.resend_email_verification(
                email=serializer.validated_data["email"]
            )
        except DjangoValidationError as exc:
            raise ValidationError({
                "email": exc.messages,
            })

        response_payload = {
            "success": True,
            "message": (
                "If the account requires verification, "
                "a verification email has been sent."
            ),
        }
        if getattr(settings, "EXPOSE_TEST_TOKENS", False) and raw_token:
            response_payload["data"] = {
                "_test_token": raw_token,
                "_test_verify_url": f"/api/v1/auth/verify-email/?token={raw_token}",
            }

        return Response(response_payload, status=status.HTTP_200_OK)


#login view
class LoginView(APIView):
    permission_classes = [AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "auth_login"

    @extend_schema(
        tags=['Authentication'],
        request=LoginSerializer,
        responses={200: LoginResponseSerializer},
        summary= "Login user",
        description="Authenticate a user using email and password and return JWT tokens.",
    )

    def post(self, request):
        serializer = LoginSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        result = AuthenticationService.login_user(
            email=serializer.validated_data["email"],
            password=serializer.validated_data["password"],
        )

        return Response(
            {
                "success": True,
                "message": "Login Successful.",
                "data": {
                    "access": result["access"],
                    "refresh": result["refresh"],
                    "user": UserResponseSerializer(
                        result["user"]
                    ).data,
                },
            },
            status=status.HTTP_200_OK,
        )

class SessionTokenRefreshView(APIView):
    permission_classes = [AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "auth_token_refresh"

    @extend_schema(
        tags=["Authentication"],
        request=SessionTokenRefreshSerializer,
        responses={
            200: SessionTokenRefreshResponseSerializer,
        },
        summary="Refresh authentication tokens",
        description=(
            "Exchange a valid refresh token for a new access token "
            "and rotated refresh token. The previous refresh token "
            "is blacklisted."
        ),
    )

    def post(self, request):
        serializer = SessionTokenRefreshSerializer(
            data=request.data
        )
        serializer.is_valid(raise_exception=True)

        return Response(
            {
                "success": True,
                "message": "Tokens refreshed successfully.",
                "data": serializer.validated_data,
            },
            status=status.HTTP_200_OK,
        )
    

class LogoutView(APIView):
    permission_classes = [IsAuthenticated]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "auth_logout"

    @extend_schema(
        tags=["Authentication"],
        request=LogoutSerializer,
        responses={200: LogoutResponseSerializer},
        summary="Logout user",
        description=(
            "Logout the authenticated user by blacklisting "
            "the provided refresh token."
        ),
    )

    def post(self, request):
        serializer = LogoutSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        AuthenticationService.logout_user(
            user=request.user,
            refresh_token=serializer.validated_data["refresh"],
        )

        return Response(
            {
                "success": True,
                "message": "Logout successful.",
                "data": {
                    "email": request.user.email,
                },
            },
            status=status.HTTP_200_OK,
        )


#password change
class ChangePasswordView(APIView):
    permission_classes = [IsAuthenticated]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "auth_password_change"

    @extend_schema(
        tags=["Authentication"],
        request=ChangePasswordSerializer,
        responses={200: ChangePasswordResponseSerializer},
        summary="Change password",
        description="Change the authenticated user's password.",
    )

    def post(self, request):
        serializer = ChangePasswordSerializer(data=request.data, context={"request": request})        
        serializer.is_valid(raise_exception=True)

        AuthenticationService.change_password(
            user=request.user,
            current_password=serializer.validated_data["current_password"],
            new_password=serializer.validated_data["new_password"],
        )

        return Response(
            {
                "success": True,
                "message": "Password changed successfully.",
            },
            status=status.HTTP_200_OK,
        )


class ForgotPasswordView(APIView):
    permission_classes = [AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "auth_forgot_password"

    @extend_schema(
        tags=["Authentication"],
        request=ForgotPasswordSerializer,
        responses={200: ForgotPasswordResponseSerializer},
        summary="Request password reset",
        description="Send a password reset email if the account exists.",
    )
    def post(self, request):
        serializer = ForgotPasswordSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        raw_token = AuthenticationService.forgot_password(
            email=serializer.validated_data["email"]
        )

        response_payload = {
            "success": True,
            "message": (
                "If an account exists with this email, "
                "a password reset link has been sent."
            ),
        }
        if getattr(settings, "EXPOSE_TEST_TOKENS", False) and raw_token:
            response_payload["data"] = {
                "_test_token": raw_token,
                "_test_reset_endpoint": "/api/v1/auth/password/reset/",
            }

        return Response(response_payload, status=status.HTTP_200_OK)


class ResetPasswordView(APIView):
    permission_classes = [AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "auth_reset_password"

    @extend_schema(
        tags=["Authentication"],
        request=ResetPasswordSerializer,
        responses={200: ResetPasswordResponseSerializer},
        summary="Reset password",
        description="Reset password using a valid password reset token.",
    )
    def post(self, request):
        serializer = ResetPasswordSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        AuthenticationService.reset_password(
            raw_token=serializer.validated_data["token"],
            new_password=serializer.validated_data["new_password"],
        )

        return Response(
            {
                "success": True,
                "message": "Password reset successfully.",
            },
            status=status.HTTP_200_OK,
        )


class PasswordSetupView(APIView):
    permission_classes = [AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "auth_password_setup"

    def get_throttles(self):
        action = (
            self.request.data.get("action")
            if isinstance(self.request.data, dict)
            else None
        )
        self.throttle_scope = (
            "auth_password_setup_resend"
            if action == "renew"
            else "auth_password_setup"
        )
        return super().get_throttles()

    @extend_schema(
        tags=["Authentication"],
        request=PasswordSetupSerializer,
        responses={200: PasswordSetupResponseSerializer},
        summary="Set or renew initial password setup",
        examples=[
            OpenApiExample(
                name="1. Complete Password Setup (Default)",
                summary="Set initial password using setup token",
                description=(
                    "Default workflow when action is omitted (or set to 'setup'). "
                    "Provide the setup token from the email along with the new password."
                ),
                value={
                    "token": "string",
                    "new_password": "string",
                    "new_password_confirm": "string",
                },
                request_only=True,
            ),
            OpenApiExample(
                name="2. Renew Expired Setup Link",
                summary="Request a new 72-hour setup link using an expired token",
                description=(
                    "When the initial setup token has expired, pass action='renew' "
                    "and the expired token to receive a new setup email."
                ),
                value={
                    "action": "renew",
                    "token": "string",
                },
                request_only=True,
            ),
        ],
        description=(
            "Use action='setup' with token and passwords to "
            "complete initial setup. "
            "Use action='renew' with an expired token to request "
            "a new 72-hour setup link by email. "
            "If action is omitted, setup is assumed."
        ),
    )
    def post(self, request):
        serializer = PasswordSetupSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        data = serializer.validated_data

        if data.get("action") == "renew":
            user, raw_token = AuthenticationService.resend_expired_password_setup(
                raw_token=data["token"]
            )

            response_payload = {
                "success": True,
                "message": (
                    "A new password setup link has been "
                    "sent to your registered email."
                ),
            }
            if getattr(settings, "EXPOSE_TEST_TOKENS", False) and raw_token:
                response_payload["data"] = {
                    "_test_token": raw_token,
                }

            return Response(response_payload, status=status.HTTP_200_OK)

        AuthenticationService.setup_password(
            raw_token=data["token"],
            new_password=data["new_password"],
        )

        return Response(
            {
                "success": True,
                "message": "Password created successfully.",
            },
            status=status.HTTP_200_OK,
        )
