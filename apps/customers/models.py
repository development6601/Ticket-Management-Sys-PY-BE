from django.db import models
from django.conf import settings

import uuid
from django.utils import timezone

import re
from django.core.exceptions import ValidationError

# Create your models here.
from apps.accounts.models import BaseModel


def normalize_company_name(value: str) -> str:
    if not isinstance(value, str):
        return ""
    return " ".join(value.strip().split())


class NormalizedCompanyName(models.Func):
    function = "REGEXP_REPLACE"
    template = "REGEXP_REPLACE(LOWER(BTRIM(%(expressions)s)), '\\s+', ' ', 'g')"
    output_field = models.CharField()


class SoftDeleteQuerySet(models.QuerySet):
    def alive(self):
        return self.filter(deleted_at__isnull=True)

    def deleted(self):
        return self.filter(deleted_at__isnull=False)


class CustomerQuerySet(SoftDeleteQuerySet):
    def filter_by_normalized_name(self, name: str):
        normalized = normalize_company_name(name).lower()
        return self.annotate(
            _normalized_company_name=NormalizedCompanyName("company_name")
        ).filter(_normalized_company_name=normalized)

    def update(self, **kwargs):
        if "company_name" in kwargs and isinstance(kwargs["company_name"], str):
            kwargs["company_name"] = normalize_company_name(kwargs["company_name"])
        return super().update(**kwargs)

    def bulk_create(self, objs, *args, **kwargs):
        for obj in objs:
            if isinstance(getattr(obj, "company_name", None), str):
                obj.company_name = normalize_company_name(obj.company_name)
        return super().bulk_create(objs, *args, **kwargs)


class ActiveCustomerManager(
    models.Manager.from_queryset(CustomerQuerySet)
):
    def get_queryset(self):
        return super().get_queryset().alive()


# For ticket key 
TICKET_KEY_PATTERN = re.compile(r"^[A-Z][A-Z0-9]{1,9}$")


def normalize_ticket_key(value):
    if not isinstance(value, str):
        raise ValidationError("Invalid ticket key.")

    key = value.strip().upper()

    if not TICKET_KEY_PATTERN.fullmatch(key):
        raise ValidationError(
            "Ticket key must contain 2-10 uppercase letters "
            "or numbers and start with a letter."
        )

    return key 

class Customer(BaseModel):
    objects = ActiveCustomerManager()
    all_objects = CustomerQuerySet.as_manager()

    company_name = models.CharField(
        max_length=255,
    )

    ticket_key = models.CharField(
        max_length=10,
        unique=True,
    )

    ticket_counter = models.PositiveBigIntegerField(
        default=0,
        editable=False,
    )

    contact_person = models.CharField(
        max_length = 255,
    )

    email = models.EmailField(
        max_length=255,
    )

    phone = models.CharField(
        max_length = 50,
    )
    address = models.TextField(
        max_length=512,
    )

    state = models.CharField(
        max_length=100,
    )

    country = models.CharField(
        max_length=100
    )

    is_active = models.BooleanField(default=True)

    class AccountStatus(models.TextChoices):
        PENDING_VERIFICATION = (
            "PENDING_VERIFICATION",
            "Pending verification",
        )
        ACTIVE = "ACTIVE", "Active"
        DEACTIVATED = "DEACTIVATED", "Deactivated"

    account_status = models.CharField(
        max_length=30,
        choices=AccountStatus.choices,
        default=AccountStatus.ACTIVE,
        db_index=True,
    )

    account_manager = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete = models.PROTECT,
        related_name="manager_customers",
        null = True,
        blank=True,
    )

    # default team system use 
    default_support_team = models.ForeignKey(
        "support.SupportTeam",
        on_delete=models.PROTECT,
        related_name="default_for_customers",
        null=True,
        blank=True,
    )

    notes = models.TextField(
        blank=True
    )

    deleted_at = models.DateTimeField(
        null=True,
        blank=True,
    )

    class Meta:
        base_manager_name = "all_objects"
        # constraints = [
        #     models.UniqueConstraint(
        #         NormalizedCompanyName("company_name"),
        #         name="unique_customer_normalized_company_name",
        #     ),
        # ]

    def soft_delete(self):
        if self.deleted_at is None:
            now = timezone.now()
            self.deleted_at = now
            self.save(update_fields=["deleted_at", "updated_at"])

            from apps.accounts.models import EmailChangeRequest

            EmailChangeRequest.objects.filter(
                customer=self,
                used_at__isnull=True,
            ).update(
                used_at=now,
                updated_at=now,
            )

    def save(self, *args, **kwargs):
        if self.ticket_key is not None:
            self.ticket_key = normalize_ticket_key(self.ticket_key)

        if isinstance(self.company_name, str):
            self.company_name = normalize_company_name(self.company_name)
        
        super().save(*args, **kwargs)

    def __str__(self):
        return self.company_name

class CustomerUser(BaseModel):
    class Role(models.TextChoices):
        ADMIN = "ADMIN", "Admin"
        MEMBER = "MEMBER", "Member"

    customer = models.ForeignKey(
        Customer,
        on_delete=models.PROTECT,
        related_name="customer_users",
    )

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="customer_members",
    )

    is_primary_contact = models.BooleanField(
        default=False,
    )

    role = models.CharField(
        max_length=20,
        choices=Role.choices,
        default=Role.MEMBER,
    )

    def save(self, *args, **kwargs):
        if self.is_primary_contact:
            self.role = self.Role.ADMIN
            update_fields = kwargs.get("update_fields")
            if update_fields is not None and "role" not in update_fields:
                kwargs["update_fields"] = [*update_fields, "role"]
        super().save(*args, **kwargs)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["customer", "user"],
                name="unique_customer_user",
            ),
            models.UniqueConstraint(
                fields=["customer"],
                condition=models.Q(is_primary_contact=True),
                name="unique_primary_customer_contact",
            ),
            models.UniqueConstraint(
                fields=["user"],
                name="unique_customer_company_per_user",
            ),
        ]

    def __str__(self):
        return f"{self.customer.company_name} - {self.user.email}"
