# accounts/models.py
from django.db import models
from django.db.models.functions import Lower

# Create your models here.
import uuid
from django.contrib.auth.models import AbstractUser
from django.conf import settings

#super admin
from django.contrib.auth.base_user import BaseUserManager

class UserManager(BaseUserManager):
    def create_user(self, email, password=None, **extra_fields):
        if not email:
            raise ValueError("Email is required.")

        email = self.normalize_email(email)

        user = self.model(
            email=email,
            **extra_fields,
        )

        user.set_password(password)
        user.save(using=self._db)

        return user

    def create_superuser(self, email, password=None, **extra_fields):
        extra_fields.setdefault("is_staff", True)
        extra_fields.setdefault("is_superuser", True)
        extra_fields.setdefault("is_active", True)
        extra_fields.setdefault("email_verified", True)

        if extra_fields.get("is_staff") is not True:
            raise ValueError("Superuser must have is_staff=True.")

        if extra_fields.get("is_superuser") is not True:
            raise ValueError("Superuser must have is_superuser=True.")

        user = self.create_user(
            email=email,
            password=password,
            **extra_fields,
        )

        admin_role, _ = Role.objects.get_or_create(
            code="ADMIN",
            defaults={
                "name": "Admin",
                "description": "System administrator",
                "is_system_role": True,
                "is_assignable": False,
            },
        )

        UserRole.objects.create(
            user=user,
            role=admin_role,
        )

        return user
    

class BaseModel(models.Model):
    id = models.UUIDField(
        primary_key=True,
        default = uuid.uuid4,
        editable=False,
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta :
        abstract = True

class User(AbstractUser):

    class AccountStatus(models.TextChoices):
        PENDING_VERIFICATION = "PENDING_VERIFICATION", "Pending verification"
        ACTIVE = "ACTIVE", "Active"
        DEACTIVATED = "DEACTIVATED", "Deactivated"

    account_status = models.CharField(
        max_length=30,
        choices=AccountStatus.choices,
        default=AccountStatus.ACTIVE,
        db_index=True,
    )

    id = models.UUIDField(
        primary_key=True, 
        default = uuid.uuid4,
        editable = False,
        )
    
    email = models.EmailField(unique=True)
    email_verified = models.BooleanField(
        default=False,
    )

    phone = models.CharField(
        max_length=50,
        blank=True,
        default="",
    )

    address = models.TextField(
        blank=True,
        default="",
    )

    groups = None
    user_permissions = None
    date_joined = None
    username = None

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS = []

    objects = UserManager()

    class Meta:
        constraints = [
            models.UniqueConstraint(
                Lower("email"),
                name="unique_user_email_case_insensitive",
            ),
        ]

    def __str__(self):
        return self.email


class Role(BaseModel):
    code = models.CharField(
        max_length=50,
        unique=True,
        editable=False,
    )
    name = models.CharField(max_length=100, unique=True)
    description = models.TextField(blank=True, null = True)
    is_system_role = models.BooleanField(default=True)
    is_assignable = models.BooleanField(
        default=True,
    )
    
    def __str__(self):
        return self.name

class Permission(BaseModel):
    name = models.CharField(
        max_length = 150,
        unique=True
    )
    module = models.CharField(
        max_length=100,
    )

    action = models.CharField(max_length=100)
    description = models.TextField(blank=True, null=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["module", "action"],
                name="unique_permission_module_action",
            ),
            models.UniqueConstraint(
                Lower("module"),
                Lower("action"),
                name="unique_permission_module_action_ci",
            ),
        ]

    def __str__(self):
        return self.name


class UserRole(BaseModel):
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete = models.PROTECT,
        related_name="user_role",
    )

    role = models.ForeignKey(
        Role,
        on_delete=models.PROTECT,
        related_name="user_roles",
    )

    # class Meta:
    #     constraints = [
    #         models.UniqueConstraint(
    #             fields=["user","role"],
    #             name = "unique_user_role"
    #         ),
    #     ]

    def __str__(self):
        return f"{self.user.email} - {self.role.name}"

class RolePermission(BaseModel):
    role = models.ForeignKey(
        Role,
        on_delete=models.PROTECT,
        related_name="role_permissions",
    )

    permission = models.ForeignKey(
        Permission,
        on_delete=models.PROTECT,
        related_name="role_permissions",
    )

    class Meta :
        constraints = [
            models.UniqueConstraint(
                fields=["role","permission"],
                name = "unique_role_permission"
            ),
        ]

    def __str__(self):
        return f"{self.role.name} - {self.permission.name}"


class PasswordResetRequest(BaseModel):

    class Purpose(models.TextChoices):
        PASSWORD_SETUP = "PASSWORD_SETUP", "Password setup"
        PASSWORD_RESET = "PASSWORD_RESET", "Password reset"

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete = models.CASCADE,
        related_name="password_reset_requests"
    )

    token_hash = models.TextField(
        max_length= 128,
        unique = True,
    )

    expires_at = models.DateTimeField()

    used_at = models.DateTimeField(null=True, blank=True,)

    purpose = models.CharField(
        max_length=20,
        choices=Purpose.choices,
        default=Purpose.PASSWORD_RESET,
        db_index=True,
    )

# email verification

class EmailVerificationRequest(BaseModel):

    class Purpose(models.TextChoices):
        INITIAL_VERIFICATION = (
            "INITIAL_VERIFICATION",
            "Initial email verification",
        )
        USER_REACTIVATION = (
            "USER_REACTIVATION",
            "User reactivation",
        )
        COMPANY_REACTIVATION = (
            "COMPANY_REACTIVATION",
            "Company reactivation",
        )

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="email_verification_requests",
    )

    token_hash = models.CharField(
        max_length=128,
        unique=True,
    )

    expires_at = models.DateTimeField()

    used_at = models.DateTimeField(
        null=True,
        blank=True,
    )

    purpose = models.CharField(
        max_length=30,
        choices=Purpose.choices,
        default=Purpose.INITIAL_VERIFICATION,
        db_index=True,
    )

    customer = models.ForeignKey(
        "customers.Customer",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="activation_verifications",
    )

    requested_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="requested_activation_verifications",
    )

    class Meta:
        ordering = ["-created_at"]

        constraints = [
            models.CheckConstraint(
                condition=(
                    ~models.Q(
                        purpose="COMPANY_REACTIVATION"
                    )
                    | models.Q(customer__isnull=False)
                ),
                name = "company_reactivation_requires_customer",
            ),
        ]

    def __str__(self):
        return f"Email verification - {self.user.email}"


class EmailChangeRequest(BaseModel):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="email_change_requests",
    )

    customer = models.ForeignKey(
        "customers.Customer",
        on_delete=models.PROTECT,
        related_name="email_change_requests",
    )

    requested_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="requested_email_changes",
    )

    old_email = models.EmailField(max_length=255)
    new_email = models.EmailField(max_length=255)

    token_hash = models.CharField(
        max_length=64,
        unique=True,
    )

    expires_at = models.DateTimeField()

    used_at = models.DateTimeField(
        null=True,
        blank=True,
    )

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"Email change - {self.old_email} -> {self.new_email}"
