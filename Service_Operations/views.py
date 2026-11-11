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
                    "token_refresh": "/api/v1/auth/token/refresh/",
                    "me": "/api/v1/auth/me/",
                    "logout": "/api/v1/auth/logout/",
                    "change_password": "/api/v1/auth/password/change/",
                    "forgot_password": "/api/v1/auth/password/forgot/",
                    "reset_password": "/api/v1/auth/password/reset/",
                    "password_setup": "/api/v1/auth/password/setup/",
                },

                "users": {
                    "list": "/api/v1/users/",
                    "detail": "/api/v1/users/{id}/",
                    "status": "/api/v1/users/{id}/status/",
                    "role": "/api/v1/users/{id}/role/",
                },

                "roles": {
                    "list": "/api/v1/roles/",
                    "detail": "/api/v1/roles/{id}/",
                },

                "role_permissions": {
                    "list": "/api/v1/role-permissions/",
                    "assign": "/api/v1/role-permissions/assign/",
                    "remove": "/api/v1/role-permissions/remove/",
                },

                "permissions": {
                    "list": "/api/v1/permissions/",
                    "detail": "/api/v1/permissions/{id}/",
                },

                "user_permissions": {
                    "list": "/api/v1/user-permissions/",
                    "assign": "/api/v1/user-permissions/assign/",
                    "remove": "/api/v1/user-permissions/remove/",
                },

                "customers": {
                    "list": "/api/v1/customers/",
                    "detail": "/api/v1/customers/{id}/",
                    "status": "/api/v1/customers/{id}/status/",
                },

                "ticket_categories": {
                    "list": "/api/v1/ticket-categories/",
                    "detail": "/api/v1/ticket-categories/{id}/",
                },

                "tickets": {
                    "list": "/api/v1/tickets/",
                    "detail": "/api/v1/tickets/{id}/",
                    "assignment": "/api/v1/tickets/{id}/assignment/",
                    "comments": "/api/v1/tickets/{id}/comments/",
                    "attachments": "/api/v1/tickets/{id}/attachments/",
                    "transition": "/api/v1/tickets/{id}/transition/",
                },

                "ticket_statuses": {
                    "list": "/api/v1/ticket-statuses/",
                    "detail": "/api/v1/ticket-statuses/{id}/",
                },

                "support_teams": {
                    "list": "/api/v1/support-teams/",
                    "detail": "/api/v1/support-teams/{id}/",
                },

                "team_members": {
                    "list": "/api/v1/support-teams/{team_id}/members/",
                    "detail": "/api/v1/support-teams/{team_id}/members/{id}/",
                },
            },
        })
