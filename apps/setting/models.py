from django.db import models
from django.conf import settings

# Create your models here.
from apps.accounts.models import BaseModel

class SystemSetting(BaseModel):
    class Category(models.TextChoices):
        GENERAL = "GENERAL", "General"
        TICKET = "TICKET", "Ticket"
        WORKFLOW = "WORKFLOW", "Workflow"
        NOTIFICATION = "NOTIFICATION", "Notification"
        EMAIL = "EMAIL", "Email"
        SECURITY = "SECURITY", "Security"
        ATTACHMENT = "ATTACHMENT", "Attachment"
        DASHBOARD = "DASHBOARD", "Dashboard"
        REPORT = "REPORT", "Report"
        BACKGROUND_TASK = "BACKGROUND_TASK", "Background Task"

    class ValueType(models.TextChoices):
        STRING = "STRING", "String"
        INTEGER = "INTEGER", "Integer"
        DECIMAL = "DECIMAL", "Decimal"
        BOOLEAN = "BOOLEAN", "Boolean"
        JSON = "JSON", "JSON"
        EMAIL = "EMAIL", "Email"
        URL = "URL", "URL"

    key = models.CharField(
        max_length=150,
        unique=True,
    )
    category = models.CharField(
        max_length=50,
        choices=Category.choices,
    )
    value = models.JSONField()

    default_value = models.JSONField(
        null=True,
        blank=True,
    )
    value_type = models.CharField(
        max_length=20,
        choices=ValueType.choices,
    )
    description = models.TextField(
        blank=True,
    )

    is_active = models.BooleanField(
        default=True,
    )

    is_editable = models.BooleanField(
        default=True,
    )

    is_sensitive = models.BooleanField(
        default=False,
    )

    is_public = models.BooleanField(
        default=False,
    )

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="created_system_settings",
    )

    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="updated_system_settings",
    )

    class Meta :
        db_table = "settings_systemsetting"
        ordering = ["category","key"]
        indexes = [
            models.Index(
                fields=["category", "is_active"],
                name = "setting_category_active_idx",
            ),
            models.Index(
                fields=["is_active"],
                name="setting_active_idx",
            ),
        ]

    def __str__(self):
        return self.key