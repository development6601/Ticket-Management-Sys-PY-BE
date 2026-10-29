from django.db import models
from django.conf import settings
# Create your models here.

from apps.accounts.models import BaseModel

class AuditLog(BaseModel):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="audit_logs",
        null=True,
        blank=True,
    )
    action = models.CharField(
        max_length= 100
    )
    entity_type = models.CharField(
        max_length=100
    )
    entity_id = models.UUIDField(
        null=True, 
        blank=True
    )
    old_values = models.JSONField(
        null=True,
        blank=True,
    )
    new_values = models.JSONField(
        null=True,
        blank=True,
    )
    metadata = models.JSONField(
        null=True,
        blank=True,
    )
    ip_address = models.GenericIPAddressField(
        null=True,
        blank=True,
    )
    user_agent = models.TextField(
        null=True,
        blank=True,
    )

    def __str__(self):
        return f"{self.action} - {self.entity_type}"
