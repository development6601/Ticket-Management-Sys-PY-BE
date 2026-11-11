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

    is_default = models.BooleanField(default=False)

    # which supportteam receive tickets created under this category.
    default_support_team = models.ForeignKey(
        "support.SupportTeam",
        on_delete=models.PROTECT,
        related_name="ticket_categories",
        null=True,
        blank=True,
    )

    position = models.PositiveIntegerField(default=0)

    objects = TicketCategoryQuerySet.as_manager()

    class Meta:
        constraints = [
            models.UniqueConstraint(
                "customer",
                Lower("name"),
                name="unique_customer_ticket_category",
            ),
            models.UniqueConstraint(
                fields=["customer"],
                condition=models.Q(is_default=True),
                name="one_default_ticket_category_per_customer"
            )
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

        if self.default_support_team_id:
            # from apps.support.models import SupportTeam

            team = SupportTeam.objects.filter(
                pk=self.default_support_team_id,
                is_active=True,
            ).first()

            if team is None:
                raise ValidationError({
                    "default_support_team": (
                        "Select an active support team."
                    )
                })

            def save(self, *args, **kwargs):
                self.clean()
                return super().save(*args, **kwargs)

            def __str__(self):
                return self.name

class TicketStatus(BaseModel):
    customer = models.ForeignKey(
        "customers.Customer",
        on_delete=models.PROTECT,
        related_name="ticket_statuses",
    )
    
    name = models.CharField(
        max_length = 100,
        # unique=True,
    )

    description = models.TextField(
        blank=True,
    )

    position = models.PositiveIntegerField(
        default=0,
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
    # Cancelled status field commented out in case cancellation is re-added in future:
    # is_cancelled = models.BooleanField(
    #     default=False,
    # )

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete = models.PROTECT,
        related_name="created_ticket_statuses",
        null=True,
        blank=True,
    )

    class Meta:
        ordering = ["position", "id"]
        constraints = [
            models.UniqueConstraint(
                "customer",
                Lower("name"),
                name="unique_customer_ticket_status_name",
            ),
            models.UniqueConstraint(
                fields=["customer"],
                condition=models.Q(is_initial=True),
                name="one_initial_status_per_customer",
            ),
            models.UniqueConstraint(
                fields=["customer"],
                condition=models.Q(is_resolved=True),
                name="one_resolved_status_per_customer",
            ),
            models.UniqueConstraint(
                fields=["customer"],
                condition=models.Q(is_closed=True),
                name="one_closed_status_per_customer",
            ),
            # Cancelled constraint commented out in case cancellation is re-added in future:
            # models.UniqueConstraint(
            #     fields=["customer"],
            #     condition=models.Q(is_cancelled=True),
            #     name="one_cancelled_status_per_customer",
            # ),
            models.CheckConstraint(
                condition=(
                    models.Q(is_initial=False)
                    | models.Q(is_active=True)
                ),
                name="initial_status_must_be_active",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(
                        is_initial=False,
                        is_resolved=False,
                        is_closed=False,
                    )
                    | models.Q(
                        is_initial=True,
                        is_resolved=False,
                        is_closed=False,
                    )
                    | models.Q(
                        is_initial=False,
                        is_resolved=True,
                        is_closed=False,
                    )
                    | models.Q(
                        is_initial=False,
                        is_resolved=False,
                        is_closed=True,
                    )
                ),
                name="ticket_status_lifecycle_exclusive",
            ),
        ]

    def clean(self):
        super().clean()
        lifecycle_flags = [
            self.is_initial,
            self.is_resolved,
            self.is_closed,
        ]
        if sum(lifecycle_flags) > 1:
            raise ValidationError(
                "A status cannot represent multiple lifecycle states."
            )
        if self.is_initial and not self.is_active:
            raise ValidationError({
                "is_active": "The initial status must remain active."
            })

    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)

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

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=[
                    "from_status",
                    "to_status",
                ],
                name="unique_ticket_status_transition",
            ),
        ]

    def clean(self):
        super().clean()
        if not self.from_status_id or not self.to_status_id:
            return
        if self.from_status_id == self.to_status_id:
            raise ValidationError({
                "to_status": (
                    "Source and destination statuses must differ."
                )
            })
        source = self.from_status
        target = self.to_status
        if source.customer_id != target.customer_id:
            raise ValidationError({
                "to_status": (
                    "Both statuses must belong to the same customer."
                )
            })
        # Inactive transitions can be retained as historical
        # configuration without requiring active endpoints.
        if not self.is_active:
            return
        if not source.is_active or not target.is_active:
            raise ValidationError({
                "to_status": (
                    "Active transitions require two active statuses."
                )
            })

        if not self.required_permission_id:
            raise ValidationError({
                "required_permission": (
                    "An active transition must have a required permission."
                )
            })

        permission = self.required_permission
        permission_code = (
            f"{permission.module}.{permission.action}".lower()
            if permission else ""
        )

        # Cancelled workflow commented out in case cancellation is re-added in future:
        # 1. Cancelled is a terminal status.
        # if getattr(source, "is_cancelled", False):
        #     raise ValidationError({
        #         "from_status": (
        #             "Cancelled tickets cannot transition to another status."
        #         )
        #     })

        # 2. Initial status cannot be a destination.
        if target.is_initial:
            raise ValidationError({
                "to_status": (
                    "Cannot transition to the initial status."
                )
            })

        # Cancelled workflow commented out in case cancellation is re-added in future:
        # 3. Cancellation is only allowed before resolution.
        # if getattr(target, "is_cancelled", False) and (
        #     source.is_resolved 
        #     or source.is_closed 
        #     or getattr(source, "is_cancelled", False)
        # ):
        #     raise ValidationError({
        #         "to_status": (
        #             "Only active tickets can be cancelled."
        #         )
        #     })

        # 4. Done can only move back into active work.
        if source.is_closed and (
            target.is_closed
            or target.is_resolved
            # or getattr(target, "is_cancelled", False)  # Cancelled workflow commented out
        ):
            raise ValidationError({
                "to_status": (
                    "A closed ticket can only be reopened "
                    "into an active working status."
                )
            })

        # 5. Resolved can only be closed or reopened.
        if source.is_resolved and (
            target.is_resolved
            # or getattr(target, "is_cancelled", False)  # Cancelled workflow commented out
        ):
            raise ValidationError({
                "to_status": (
                    "A resolved ticket can only be closed "
                    "or reopened into active work."
                )
            })

        # 6. Successful closure requires resolution.
        if target.is_closed and not source.is_resolved:
            raise ValidationError({
                "to_status": (
                    "A ticket must be resolved before closure."
                )
            })

        # 7. Identify protected operations using status flags.
        is_reopening = (
            source.is_resolved or source.is_closed
        ) and not (
            target.is_initial
            or target.is_resolved
            or target.is_closed
            # or getattr(target, "is_cancelled", False)  # Cancelled workflow commented out
        )

        if target.is_resolved:
            expected_permission = "tickets.resolve"
        elif target.is_closed:
            expected_permission = "tickets.close"
        # Cancelled code commented out: tickets.cancel permission in case cancellation is re-added in future
        # elif getattr(target, "is_cancelled", False):
        #     expected_permission = "tickets.cancel"
        elif is_reopening:
            expected_permission = "tickets.reopen"
        else:
            expected_permission = None

        # 8. Protected operations must use their own permission.
        if (
            expected_permission
            and permission_code != expected_permission
        ):
            raise ValidationError({
                "required_permission": (
                    f"This transition requires {expected_permission}."
                )
            })

    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)

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

    class ResolutionType(models.TextChoices):
        FIXED = "FIXED", "Fixed"
        WORKAROUND = "WORKAROUND", "Workaround"
        DUPLICATE = "DUPLICATE", "Duplicate"
        NOT_A_BUG = "NOT_A_BUG", "Not a Bug"

    resolution_type = models.CharField(
        max_length=20,
        choices=ResolutionType.choices,
        null=True,
        blank=True,
    )

    resolution_notes = models.TextField(
        blank=True,
        default="",
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

    # Cancellation fields commented out in case cancellation is re-added in future:
    # cancelled_at = models.DateTimeField(
    #     null=True,
    #     blank=True,
    # )
    # cancelled_by = models.ForeignKey(
    #     settings.AUTH_USER_MODEL,
    #     on_delete=models.PROTECT,
    #     related_name="cancelled_tickets",
    #     null=True,
    #     blank=True,
    # )
    # cancellation_reason = models.TextField(
    #     blank=True,
    #     default="",
    # )

    reopen_count = models.PositiveIntegerField(
        default=0,
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

        if not self.customer_id:
            return

        # 1. Validate customer on ticket creation vs prevent transfer on update
        if self._state.adding:
            if not Customer.objects.filter(
                pk=self.customer_id,
            ).exists():
                raise ValidationError({
                    "customer": "Customer does not exist."
                })
        elif self.pk:
            original_customer_id = (
                Ticket.objects
                .filter(pk=self.pk)
                .values_list("customer_id", flat=True)
                .first()
            )
            if (
                original_customer_id is not None
                and original_customer_id != self.customer_id
            ):
                raise ValidationError({
                    "customer": (
                        "A ticket cannot be transferred to another customer."
                    )
                })

        # 2. Validate status belongs to the ticket's customer
        if self.status_id:
            status = TicketStatus.objects.filter(
                pk=self.status_id,
                customer_id=self.customer_id,
            ).first()
            if status is None:
                raise ValidationError({
                    "status": (
                        "Ticket status must belong to the ticket's customer."
                    )
                })
            if self._state.adding and (
                not status.is_initial or not status.is_active
            ):
                raise ValidationError({
                    "status": (
                        "New tickets must start in the active initial status."
                    )
                })

        # 3. Validate category belongs to the ticket's customer
        if self.category_id:
            if not TicketCategory.objects.filter(
                pk=self.category_id,
                customer_id=self.customer_id,
            ).exists():
                raise ValidationError({
                    "category": (
                        "Ticket category must belong to the ticket's customer."
                    )
                })

        # An employee assignment requires a team assignment.
        if self.assigned_employee_id and not self.assigned_team_id:
            raise ValidationError({
                "assigned_employee": (
                    "An assigned employee requires an assigned team."
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

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["ticket"],
                condition=models.Q(
                    unassigned_at__isnull=True,
                    employee__isnull=False,
                ),
                name="one_active_employee_assignment",
            ),
        ]

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
