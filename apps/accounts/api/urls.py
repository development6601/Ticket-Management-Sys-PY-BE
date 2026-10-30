from django.urls import path

from apps.accounts.api.views.auth import *
from apps.accounts.api.views.users import *
from apps.accounts.api.views.roles import *
from apps.accounts.api.views.permissions import *
from apps.accounts.api.views.user_permissions import *

urlpatterns = [
    path("auth/register/", RegisterView.as_view(), name="auth-register"),
    path("auth/verify-email/", VerifyEmailView.as_view(), name="auth-verify-email"),
    path("auth/resend-verification/", ResendVerificationView.as_view(), name="auth-resend-verification"),

    path("auth/login/", LoginView.as_view(), name="auth-login"),

    path("auth/token/refresh/", SessionTokenRefreshView.as_view(), name="auth-token-refresh"),

    path("auth/me/", MeView.as_view(), name="auth-me"),
    path("auth/logout/", LogoutView.as_view(), name="auth-logout"),

    path("auth/password/change/", ChangePasswordView.as_view(), name="change-password"),
    path("auth/password/forgot/", ForgotPasswordView.as_view(), name="forgot-password"),
    path("auth/password/reset/", ResetPasswordView.as_view(), name="reset-password"),
    path("auth/password/setup/", PasswordSetupView.as_view(), name="password-setup"),

    path("users/", UserListView.as_view(), name="user-list"),
    path("users/<uuid:id>/", UserDetailView.as_view(), name="user-detail"),
    path("users/<uuid:id>/status/", UserStatusView.as_view(), name="user-status"),


    path("roles/", RoleListView.as_view(), name="role-list"),
    path("roles/<uuid:id>/", RoleDetailView.as_view(), name="role-detail"),

    path("role-permissions/", RolePermissionListView.as_view(), name="role-permission-list"),
    path("role-permissions/assign/", RolePermissionCreateView.as_view(), name="role-permission-assign"),
    path("role-permissions/remove/", RolePermissionRemoveView.as_view(), name="role-permission-remove"),

    path("permissions/", PermissionListView.as_view(), name="permission-list"),
    path("permissions/<uuid:id>/", PermissionDetailView.as_view(), name="permission-detail"),

    path("users/<uuid:id>/role/", UserRoleView.as_view(), name="user-role"),

    path("user-permissions/", UserPermissionListView.as_view(), name="user-permission-list"),
    path("user-permissions/assign/", UserPermissionAssignView.as_view(), name="user-permission-assign"),
    path("user-permissions/remove/", UserPermissionRemoveView.as_view(), name="user-permission-remove"),

]
