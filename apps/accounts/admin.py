from django.contrib import admin

from apps.accounts.models import (
    User,
    Role,
    Permission,
    UserRole,
    RolePermission,
    PasswordResetRequest,
    EmailVerificationRequest,
)


@admin.register(User)
class UserAdmin(admin.ModelAdmin):
    list_display = (
        "email",
        "first_name",
        "last_name",
        "is_active",
        "is_staff",
        "is_superuser",
        "email_verified",
        "created_at",
    )
    search_fields = ("email", "first_name", "last_name")
    list_filter = ("is_active", "is_staff", "is_superuser", "email_verified")
    readonly_fields = ("id", "created_at", "updated_at")


@admin.register(Role)
class RoleAdmin(admin.ModelAdmin):
    list_display = (
        "name",
        "description",
        "is_system_role",
        "created_at",
    )
    search_fields = ("name", "description")
    list_filter = ("is_system_role",)
    readonly_fields = ("id", "created_at", "updated_at")


@admin.register(Permission)
class PermissionAdmin(admin.ModelAdmin):
    list_display = (
        "name",
        "module",
        "action",
        "created_at",
    )
    search_fields = ("name", "module", "action")
    list_filter = ("module", "action")
    readonly_fields = ("id", "created_at", "updated_at")


@admin.register(UserRole)
class UserRoleAdmin(admin.ModelAdmin):
    list_display = (
        "user",
        "role",
        "created_at",
    )
    search_fields = (
        "user__email",
        "role__name",
    )
    list_filter = ("role",)
    readonly_fields = ("id", "created_at", "updated_at")


@admin.register(RolePermission)
class RolePermissionAdmin(admin.ModelAdmin):
    list_display = (
        "role",
        "permission",
        "created_at",
    )
    search_fields = (
        "role__name",
        "permission__name",
    )
    list_filter = ("role", "permission")
    readonly_fields = ("id", "created_at", "updated_at")


@admin.register(PasswordResetRequest)
class PasswordResetRequestAdmin(admin.ModelAdmin):
    list_display = (
        "user",
        "expires_at",
        "used_at",
        "created_at",
    )
    search_fields = ("user__email",)
    readonly_fields = (
        "id",
        "user",
        "token_hash",
        "expires_at",
        "used_at",
        "created_at",
        "updated_at",
    )


@admin.register(EmailVerificationRequest)
class EmailVerificationRequestAdmin(admin.ModelAdmin):
    list_display = (
        "user",
        "expires_at",
        "used_at",
        "created_at",
    )
    search_fields = ("user__email",)
    readonly_fields = (
        "id",
        "user",
        "token_hash",
        "expires_at",
        "used_at",
        "created_at",
        "updated_at",
    )