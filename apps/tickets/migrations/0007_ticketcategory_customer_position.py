import re

import django.db.models.deletion
import django.db.models.functions.text
from django.db import migrations, models

DEFAULT_TICKET_CATEGORIES = [
    "Bug",
    "Feature Request",
    "Technical Issue",
    "Billing",
    "Account",
    "General Question",
]


def backfill_and_seed_ticket_categories(apps, schema_editor):
    Customer = apps.get_model("customers", "Customer")
    TicketCategory = apps.get_model("tickets", "TicketCategory")
    Ticket = apps.get_model("tickets", "Ticket")
    db_alias = schema_editor.connection.alias

    customer_manager = getattr(
        Customer, "all_objects", Customer._base_manager
    ).using(db_alias)

    # 1. Handle any existing global categories (customer_id is None).
    global_categories = list(
        TicketCategory.objects.using(db_alias).filter(customer__isnull=True)
    )

    for global_cat in global_categories:
        normalized_name = re.sub(r"\s+", " ", global_cat.name or "").strip()
        if not normalized_name:
            normalized_name = "Uncategorized"

        ticket_customer_ids = list(
            Ticket.objects.using(db_alias)
            .filter(category_id=global_cat.pk)
            .values_list("customer_id", flat=True)
            .distinct()
        )

        if not ticket_customer_ids:
            # Unused global category: attach to the first company that does
            # not already have this category name, or delete if no companies
            # exist or all companies already have it.
            assigned = False
            for cust in customer_manager.order_by("created_at", "id"):
                if not (
                    TicketCategory.objects.using(db_alias)
                    .filter(
                        customer_id=cust.pk,
                        name__iexact=normalized_name,
                    )
                    .exists()
                ):
                    global_cat.customer_id = cust.pk
                    global_cat.name = normalized_name
                    global_cat.save(
                        using=db_alias,
                        update_fields=["customer", "name"],
                    )
                    assigned = True
                    break

            if not assigned:
                global_cat.delete(using=db_alias)
            continue

        # Global category is referenced by tickets.
        first_customer_id = ticket_customer_ids[0]
        existing_first = (
            TicketCategory.objects.using(db_alias)
            .filter(
                customer_id=first_customer_id,
                name__iexact=normalized_name,
            )
            .exclude(pk=global_cat.pk)
            .first()
        )

        # Re-point tickets for any additional companies first.
        for cid in ticket_customer_ids[1:]:
            target_cat = (
                TicketCategory.objects.using(db_alias)
                .filter(
                    customer_id=cid,
                    name__iexact=normalized_name,
                )
                .first()
            )
            if target_cat is None:
                target_cat = TicketCategory.objects.using(db_alias).create(
                    customer_id=cid,
                    name=normalized_name,
                    description=global_cat.description,
                    is_active=global_cat.is_active,
                    position=global_cat.position,
                )
            Ticket.objects.using(db_alias).filter(
                category_id=global_cat.pk,
                customer_id=cid,
            ).update(category_id=target_cat.pk)

        # Now handle the first company.
        if existing_first is not None:
            Ticket.objects.using(db_alias).filter(
                category_id=global_cat.pk,
                customer_id=first_customer_id,
            ).update(category_id=existing_first.pk)
            global_cat.delete(using=db_alias)
        else:
            global_cat.customer_id = first_customer_id
            global_cat.name = normalized_name
            global_cat.save(
                using=db_alias,
                update_fields=["customer", "name"],
            )

    # 2. Ensure any ticket pointing to another company's category is re-pointed
    # to its own company's category.
    mismatched_tickets = (
        Ticket.objects.using(db_alias)
        .select_related("category")
        .exclude(customer_id=models.F("category__customer_id"))
    )
    for ticket in mismatched_tickets:
        cat = ticket.category
        normalized_name = re.sub(r"\s+", " ", cat.name or "").strip()
        target_cat = (
            TicketCategory.objects.using(db_alias)
            .filter(
                customer_id=ticket.customer_id,
                name__iexact=normalized_name,
            )
            .first()
        )
        if target_cat is None:
            target_cat = TicketCategory.objects.using(db_alias).create(
                customer_id=ticket.customer_id,
                name=normalized_name,
                description=cat.description,
                is_active=cat.is_active,
                position=cat.position,
            )
        Ticket.objects.using(db_alias).filter(pk=ticket.pk).update(
            category_id=target_cat.pk
        )

    # 3. Seed the six default categories for every existing customer without
    # overwriting their customized categories, and assign positions to any
    # custom categories that still have position 0.
    for customer in customer_manager.all():
        for position, default_name in enumerate(
            DEFAULT_TICKET_CATEGORIES,
            start=1,
        ):
            existing = (
                TicketCategory.objects.using(db_alias)
                .filter(
                    customer_id=customer.pk,
                    name__iexact=default_name,
                )
                .first()
            )
            if existing is None:
                TicketCategory.objects.using(db_alias).create(
                    customer_id=customer.pk,
                    name=default_name,
                    description="",
                    is_active=True,
                    position=position,
                )
            elif existing.position == 0:
                existing.position = position
                existing.save(using=db_alias, update_fields=["position"])

        max_pos = (
            TicketCategory.objects.using(db_alias)
            .filter(customer_id=customer.pk)
            .aggregate(max_p=models.Max("position"))["max_p"]
            or len(DEFAULT_TICKET_CATEGORIES)
        )
        next_pos = max(max_pos, len(DEFAULT_TICKET_CATEGORIES)) + 1

        custom_zero_pos = (
            TicketCategory.objects.using(db_alias)
            .filter(customer_id=customer.pk, position=0)
            .order_by("created_at", "id")
        )
        for custom_cat in custom_zero_pos:
            custom_cat.position = next_pos
            custom_cat.save(using=db_alias, update_fields=["position"])
            next_pos += 1


class Migration(migrations.Migration):

    dependencies = [
        ("customers", "0010_customeruser_role"),
        ("tickets", "0006_alter_ticketcategory_name_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="ticketcategory",
            name="customer",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="ticket_categories",
                to="customers.customer",
            ),
        ),
        migrations.AddField(
            model_name="ticketcategory",
            name="position",
            field=models.PositiveIntegerField(default=0),
        ),
        migrations.RemoveConstraint(
            model_name="ticketcategory",
            name="unique_ticket_category",
        ),
        migrations.RunPython(
            backfill_and_seed_ticket_categories,
            migrations.RunPython.noop,
        ),
        migrations.AlterField(
            model_name="ticketcategory",
            name="customer",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.PROTECT,
                related_name="ticket_categories",
                to="customers.customer",
            ),
        ),
        migrations.AddConstraint(
            model_name="ticketcategory",
            constraint=models.UniqueConstraint(
                models.F("customer"),
                django.db.models.functions.text.Lower("name"),
                name="unique_customer_ticket_category",
            ),
        ),
    ]
