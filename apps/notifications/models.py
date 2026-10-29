from django.db import models
from django.conf import settings
# Create your models here.

from apps.accounts.models import BaseModel
from apps.tickets.models import Ticket

class Notification(BaseModel):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="notifications",
    )

    ticket = models.ForeignKey(
        Ticket,
        on_delete=models.PROTECT,
        related_name="notifications",
        null=True,
        blank=True,
    )

    type = models.CharField(
        max_length=100
        )
    title = models.CharField(
        max_length=255
        )
    message = models.TextField()
    is_read = models.BooleanField(default=False)
    read_at = models.DateTimeField(null=True, blank=True)
    metadata = models.JSONField(null = True, blank=True)

    def __str__(self):
        return f"{self.user.email} - {self.title}"


class EmailJob(BaseModel):
    class Status(models.TextChoices):
        PENDING = "PENDING", "Pending"
        PROCESSING = "PROCESSING", "Processing"
        SENT = "SENT","Sent"
        FAILED = "FAILED", "Failed"

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="email_jobs",
        null=True,
        blank=True,
    )
    ticket = models.ForeignKey(
        Ticket,
        on_delete=models.PROTECT,
        related_name="email_jobs",
        null=True,
        blank=True,
    )

    recipient_email = models.EmailField(
        max_length=255
    )
    template = models.CharField(
        max_length=100
    )
    subject = models.CharField(
        max_length=255
    )
    payload = models.JSONField(
        null=True, 
        blank=True
    )

    status = models.CharField(
        max_length=30,
        choices=Status.choices,
        default=Status.PENDING,
    )
    attempts = models.PositiveIntegerField(
        default=0
    )

    scheduled_at = models.DateTimeField(
        null=True,
        blank=True,
    )
    sent_at = models.DateTimeField(
        null=True,
        blank=True,
    )
    failed_at = models.DateTimeField(
        null=True,
        blank=True,
    )
    error_message = models.TextField(
        null=True,
        blank=True,
    )

    def __str__(self):
        return f"{self.recipient_email} - {self.template}"