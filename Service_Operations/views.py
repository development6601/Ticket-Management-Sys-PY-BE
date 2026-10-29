from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView
from drf_spectacular.utils import extend_schema


@extend_schema(exclude=True)
class ApiRootView(APIView):
    permission_classes = [AllowAny]

    def get(self, request):
        return Response({
            "project": "Service Operations & Ticket Management API",
            "status": "operational",
            "version": "v1",

            "endpoints": {
                "admin": "/admin/",
                "schema": "/api/schema/",
                "swagger": "/api/docs/",

                "auth": {
                    "register": "/api/v1/auth/register/",
                    "verify_email": "/api/v1/auth/verify-email/",
                    "resend_verification": "/api/v1/auth/resend-verification/",
                    "login": "/api/v1/auth/login/",
                    "me": "/api/v1/auth/me/",
                    "logout": "/api/v1/auth/logout/",
                    "change_password": "/api/v1/auth/password/change/",
                    "forgot_password": "/api/v1/auth/password/forgot/",
                    "reset_password": "/api/v1/auth/password/reset/",
                },

                "users": {
                    "list": "/api/v1/users/",
                    "detail": "/api/v1/users/{id}/",
                    "status": "/api/v1/users/{id}/status/",
                },

                "roles": {
                    "list": "/api/v1/roles/",
                    "detail": "/api/v1/roles/{id}/",
                },

                "role_permissions": {
                    "assign": "/api/v1/role-permissions/assign/",
                    "remove": "/api/v1/role-permissions/remove/",
                },

                "permissions": {
                    "list": "/api/v1/permissions/",
                    "detail": "/api/v1/permissions/{id}/",
                },
            },
        })