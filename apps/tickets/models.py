from django.db import models
from django.conf import settings
from django.core.exceptions import ValidationError

# Create your models here.
from apps.accounts.models import BaseModel
from apps.customers.models import Customer 
from apps.support.models import SupportTeam

from django.db.models.functions import Lower
import re 


class TicketCategoryQuerySet(models.QuerySet):
    def update(self, **kwargs):
        if "name" in kwargs:
            raw_name = kwargs["name"]
            if raw_name is None or not isinstance(raw_name, str):
                raise ValidationError({
                    "name": "Category name must be a string."
                })
            normalized_name = re.sub(r"\s+", " ", raw_name).strip()
            if not normalized_name:
                raise ValidationError({
                    "name": "Category name cannot be empty."
                })
            if len(normalized_name) > 100:
                raise ValidationError({
                    "name": (
                        "Ensure this value has at most 100 characters."
                    )
                })
            if not any(char.isalnum() for char in normalized_name):
                raise ValidationError({
                    "name": (
                        "Category name must contain a letter or number."
                    )
                })
            if normalized_name.isdigit() or not any(
                char.isalpha() for char in normalized_name
            ):
                raise ValidationError({
                    "name": (
                        "Category name must contain at least one letter "
                        "and cannot be only numbers."
                    )
                })
            kwargs["name"] = normalized_name

        if "description" in kwargs:
            raw_description = kwargs["description"]
            if raw_description is None:
                kwargs["description"] = ""
            elif not isinstance(raw_description, str):
                raise ValidationError({
                    "description": "Description must be a string."
                })
            else:
                normalized_description = re.sub(
                    r"\s+", " ", raw_description
                ).strip()
                if len(normalized_description) > 2000:
                    raise ValidationError({
                        "description": (
                            "Ensure this value has at most 2000 characters."
                        )
                    })
                if normalized_description and (
                    normalized_description.isdigit()
                    or not any(
                        char.isalpha() for char in normalized_description
                    )
                ):
                    raise ValidationError({
                        "description": (
                            "Description must contain at least one letter "
                            "and cannot be only numbers."
                        )
                    })
                kwargs["description"] = normalized_description

        return super().update(**kwargs)

    def bulk_create(self, objs, *args, **kwargs):
        for obj in objs:
            obj.clean()
        return super().bulk_create(objs, *args, **kwargs)


class TicketCategory(BaseModel):
    customer = models.ForeignKey(
        "customers.Customer",
        on_delete=models.PROTECT,
        related_name="ticket_categories",
    )

    name = models.CharField(max_length=100)
    description = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)

    position = models.PositiveIntegerField(default=0)

    objects = TicketCategoryQuerySet.as_manager()

    class Meta:
        constraints = [
            models.UniqueConstraint(
                "customer",
                Lower("name"),
                name="unique_customer_ticket_category",
            ),
        ]

    def clean(self):
        super().clean()

        if self.name is None or not isinstance(self.name, str):
            raise ValidationError({
                "name": "Category name must be a string."
            })

        self.name = re.sub(r"\s+", " ", self.name).strip()

        if not self.name:
            raise ValidationError({
                "name": "Category name cannot be empty."
            })

        if len(self.name) > 100:
            raise ValidationError({
                "name": "Ensure this value has at most 100 characters."
            })

        if not any(char.isalnum() for char in self.name):
            raise ValidationError({
                "name": "Category name must contain a letter or number."
            })

        if self.name.isdigit() or not any(
            char.isalpha() for char in self.name
        ):
            raise ValidationError({
                "name": (
                    "Category name must contain at least one letter "
                    "and cannot be only numbers."
                )
            })

        if self.description is None:
            self.description = ""
        elif not isinstance(self.description, str):
            raise ValidationError({
                "description": "Description must be a string."
            })
        else:
            self.description = re.sub(
                r"\s+", " ", self.description
            ).strip()
            if len(self.description) > 2000:
                raise ValidationError({
                    "description": (
                        "Ensure this value has at most 2000 characters."
                    )
                })
            if self.description and (
                self.description.isdigit()
                or not any(char.isalpha() for char in self.description)
            ):
                raise ValidationError({
                    "description": (
                        "Description must contain at least one letter "
                        "and cannot be only numbers."
                    )
                })

    def save(self, *args, **kwargs):
        self.clean()
        return super().save(*args, **kwargs)

    def __str__(self):
        return self.name

class TicketStatus(BaseModel):
    name = models.CharField(
        max_length = 100,
        unique=True,
    )

    description = models.TextField(
        blank=True,
    )

    is_system = models.BooleanField(
        default=False,
    )
    is_active = models.BooleanField(
        default=True,
    )
    is_initial = models.BooleanField(
        default=False,
    )
    is_resolved = models.BooleanField(
        default=False,
    )
    is_closed = models.BooleanField(
        default=False,
    )

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete = models.PROTECT,
        related_name="created_ticket_statuses",
        null=True,
        blank=True,
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["is_initial"],
                condition=models.Q(is_initial=True),
                name="only_one_initial_ticket_status",
            ),
            models.UniqueConstraint(
                fields=["is_resolved"],
                condition=models.Q(is_resolved=True),
                name="only_one_resolved_ticket_status",
            ),
            models.UniqueConstraint(
                fields=["is_closed"],
                condition=models.Q(is_closed=True),
                name="only_one_closed_ticket_status",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(is_initial=False)
                    | models.Q(is_active=True)
                ),
                name="initial_status_must_be_active",
            ),
            models.CheckConstraint(
                condition=~(models.Q(is_resolved=True) & models.Q(is_closed=True)),
                name="status_cannot_be_resolved_and_closed",
            ),
        ]

    def __str__(self):
        return self.name

class TicketStatusTransition(BaseModel):
    from_status = models.ForeignKey(
        TicketStatus,
        on_delete=models.PROTECT,
        related_name="outgoing_transitions",
    )

    to_status = models.ForeignKey(
        TicketStatus,
        on_delete= models.PROTECT,
        related_name="incoming_transitions",
    )

    required_permission = models.ForeignKey(
        "accounts.Permission",
        on_delete=models.PROTECT,
        related_name="ticket_status_transitions",
    )

    is_active = models.BooleanField(
        default=True,
    )

    class Meta : 
        constraints = [
            models.UniqueConstraint(
                fields=[
                    "from_status",
                    "to_status",
                ],
                name = "unique_ticket_status_transition",
            ),
        ]

    def __str__(self):
        return f"{self.from_status.name} to {self.to_status.name}"

class Ticket(BaseModel):

    ticket_number = models.CharField(
        max_length=30,
        unique=True,
    )
    subject = models.CharField(
        max_length=255,
    )               
    description = models.TextField()

    customer = models.ForeignKey(
        Customer,
        on_delete = models.PROTECT,
        related_name="customer_tickets"
    ) #customer_id UUID FK → customers.id

    #created_by_id UUID FK → users.id
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="created_tickets"
    )
    
    assigned_team = models.ForeignKey(
        SupportTeam,
        on_delete=models.PROTECT,
        related_name="tickets",
        null=True,
        blank=True,
    )

    assigned_employee = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete = models.PROTECT,
        related_name="assigned_tickets",
        null=True,
        blank=True
    )
   
    category = models.ForeignKey(
        TicketCategory,
        on_delete=models.PROTECT,
        related_name="tickets_category"
    )

    class Priority(models.TextChoices):
        LOW = "LOW", "Low"
        MEDIUM = "MEDIUM", "Medium"
        HIGH = "HIGH", "High"
        CRITICAL = "CRITICAL", "Critical"

    priority = models.CharField(
        max_length=20,
        choices = Priority.choices,
        default = Priority.MEDIUM
    )       

    status = models.ForeignKey(
        TicketStatus,
        on_delete=models.PROTECT,
        related_name = "tickets_status"
    )                
    
    due_at = models.DateTimeField(
        null=True,
        blank=True,
    )

    resolved_at = models.DateTimeField(
        null=True,
        blank=True,
    )

    closed_at = models.DateTimeField(
        null=True,
        blank=True,
    )              

    resolved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="resolved_tickets",
        null=True,
        blank=True,
    )

    closed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="closed_tickets",
        null=True,
        blank=True,
    )

    resolution_confirmed_at = models.DateTimeField(
        null=True,
        blank=True,
    )

    resolution_confirmed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="confirmed_ticket_resolutions",
        null=True,
        blank=True,
    )

    def clean(self):
        super().clean()

        if self.customer_id:
            should_validate_customer = self._state.adding

            if not should_validate_customer and self.pk:
                existing_customer_id = (
                    Ticket.objects
                    .filter(pk=self.pk)
                    .values_list("customer_id", flat=True)
                    .first()
                )
                should_validate_customer = (
                    existing_customer_id != self.customer_id
                )

            if should_validate_customer and not Customer.objects.filter(
                pk=self.customer_id,
            ).exists():
                raise ValidationError({
                    "customer": (
                        "Cannot associate a ticket with a deleted "
                        "customer company."
                    )
                })

    def save(self, *args, **kwargs):
        self.clean()
        return super().save(*args, **kwargs)


class TicketAssignment(BaseModel):
    ticket = models.ForeignKey(
        Ticket,
        on_delete=models.PROTECT,
        related_name="assignments",
    )

    team = models.ForeignKey(
        SupportTeam,
        on_delete=models.PROTECT,
        related_name="ticket_assignments",
    )

    employee = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name = "ticket_assignments",
        null=True,
        blank=True,
    )

    assigned_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="ticket_assignments_created",
    )

    assigned_at = models.DateTimeField(
        auto_now_add=True,
    )

    unassigned_at = models.DateTimeField(
        null=True,
        blank=True,
    )

    reason = models.TextField(
        blank=True,
    )

    def __str__(self):
        employee = self.employee.email if self.employee else "Unassigned"
        return f"{self.ticket.ticket_number} - {self.team.name} - {employee}"


class TicketComment(BaseModel):
    COMMENT_TYPE_CHOICES = [
    ("PUBLIC", "Public"),
    ("INTERNAL_NOTE", "Internal Note"),
    ]

    ticket = models.ForeignKey(
        Ticket,
        on_delete = models.PROTECT,
        related_name="comments",
    )

    author = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete = models.PROTECT,
        related_name="ticket_comments",
    )

    content = models.TextField()

    comment_type = models.CharField(
        max_length=30,
        choices=COMMENT_TYPE_CHOICES,
    )

    def __str__(self):
        return f"{self.ticket.ticket_number} - {self.author.email}"

class TicketAttachment(BaseModel):
    ticket = models.ForeignKey(
        Ticket,
        on_delete = models.PROTECT,
        related_name="attachments",
    )

    comment = models.ForeignKey(
        TicketComment,
        on_delete = models.PROTECT,
        related_name="attachments",
        null=True,
        blank=True,
    )

    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="ticket_attachments",
    )

    original_filename = models.CharField(
        max_length=255,
    )

    stored_filename = models.CharField(
        max_length=255,
    )

    storage_path = models.TextField()

    mime_type = models.CharField(
        max_length=150,
    )

    file_size = models.BigIntegerField()

    def __str__(self):
        return self.original_filename

class TicketActivityLog(BaseModel):
    ticket = models.ForeignKey(
        Ticket,
        on_delete=models.PROTECT,
        related_name="activity_logs",
    )

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="ticket_activity_logs",
    )

    activity_type = models.CharField(
        max_length=100,
    )

    old_value = models.JSONField(
        null=True,
        blank=True,
    )

    new_value = models.JSONField(
        null=True,
        blank=True,
    )

    metadata = models.JSONField(
        null=True,
        blank=True,
    )

    def __str__(self):
        return f"{self.ticket.ticket_number} - {self.activity_type}"
