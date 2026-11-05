from django.core.exceptions import ValidationError as DjangoValidationError
from django.test import TestCase
from rest_framework.exceptions import ValidationError as DRFValidationError

from apps.accounts.models import Permission, User
from apps.accounts.permissions.constants import (
    TICKET_CONFIRM,
    TICKET_REOPEN,
    TICKET_RESOLVE,
    TICKET_START,
)
from apps.customers.models import Customer
from apps.tickets.models import (
    Ticket,
    TicketCategory,
    TicketStatus,
    TicketStatusTransition,
)
from apps.tickets.services.workflow import get_configured_transition


class TicketStatusTransitionValidationTests(TestCase):
    def setUp(self):
        self.customer_a = Customer.objects.create(
            company_name="Acme Corp",
            ticket_key="ACME",
        )
        self.customer_b = Customer.objects.create(
            company_name="Globex Corp",
            ticket_key="GLBX",
        )

        self.perm_start, _ = Permission.objects.get_or_create(
            module="tickets",
            action="start",
            defaults={"name": "Ticket Start"},
        )
        self.perm_resolve, _ = Permission.objects.get_or_create(
            module="tickets",
            action="resolve",
            defaults={"name": "Ticket Resolve"},
        )
        self.perm_close, _ = Permission.objects.get_or_create(
            module="tickets",
            action="close",
            defaults={"name": "Ticket Close"},
        )
        self.perm_reopen, _ = Permission.objects.get_or_create(
            module="tickets",
            action="reopen",
            defaults={"name": "Ticket Reopen"},
        )
        self.perm_cancel, _ = Permission.objects.get_or_create(
            module="tickets",
            action="cancel",
            defaults={"name": "Ticket Cancel"},
        )

        self.status_new_a = TicketStatus.objects.create(
            customer=self.customer_a,
            name="New",
            is_initial=True,
            is_active=True,
        )
        self.status_in_progress_a = TicketStatus.objects.create(
            customer=self.customer_a,
            name="In Progress",
            is_active=True,
        )
        self.status_resolved_a = TicketStatus.objects.create(
            customer=self.customer_a,
            name="Resolved",
            is_resolved=True,
            is_active=True,
        )
        self.status_closed_a = TicketStatus.objects.create(
            customer=self.customer_a,
            name="Done",
            is_closed=True,
            is_active=True,
        )
        self.status_cancelled_a = TicketStatus.objects.create(
            customer=self.customer_a,
            name="Cancelled",
            is_cancelled=True,
            is_active=True,
        )
        self.status_inactive_a = TicketStatus.objects.create(
            customer=self.customer_a,
            name="On Hold",
            is_active=False,
        )

        self.status_new_b = TicketStatus.objects.create(
            customer=self.customer_b,
            name="New",
            is_initial=True,
            is_active=True,
        )

    def test_same_status_transition_rejected(self):
        transition = TicketStatusTransition(
            from_status=self.status_new_a,
            to_status=self.status_new_a,
            required_permission=self.perm_start,
            is_active=True,
        )
        with self.assertRaises(DjangoValidationError) as ctx:
            transition.save()
        self.assertIn("to_status", ctx.exception.message_dict)
        self.assertEqual(
            ctx.exception.message_dict["to_status"],
            ["Source and destination statuses must differ."],
        )

    def test_cross_company_transition_rejected(self):
        transition = TicketStatusTransition(
            from_status=self.status_new_a,
            to_status=self.status_new_b,
            required_permission=self.perm_start,
            is_active=True,
        )
        with self.assertRaises(DjangoValidationError) as ctx:
            transition.save()
        self.assertIn("to_status", ctx.exception.message_dict)
        self.assertEqual(
            ctx.exception.message_dict["to_status"],
            ["Both statuses must belong to the same customer."],
        )

    def test_active_transition_with_inactive_from_status_rejected(self):
        transition = TicketStatusTransition(
            from_status=self.status_inactive_a,
            to_status=self.status_resolved_a,
            required_permission=self.perm_resolve,
            is_active=True,
        )
        with self.assertRaises(DjangoValidationError) as ctx:
            transition.save()
        self.assertIn("to_status", ctx.exception.message_dict)
        self.assertEqual(
            ctx.exception.message_dict["to_status"],
            ["Active transitions require two active statuses."],
        )

    def test_active_transition_with_inactive_to_status_rejected(self):
        transition = TicketStatusTransition(
            from_status=self.status_in_progress_a,
            to_status=self.status_inactive_a,
            required_permission=self.perm_resolve,
            is_active=True,
        )
        with self.assertRaises(DjangoValidationError) as ctx:
            transition.save()
        self.assertIn("to_status", ctx.exception.message_dict)
        self.assertEqual(
            ctx.exception.message_dict["to_status"],
            ["Active transitions require two active statuses."],
        )

    def test_inactive_transition_with_inactive_status_allowed(self):
        transition = TicketStatusTransition(
            from_status=self.status_in_progress_a,
            to_status=self.status_inactive_a,
            required_permission=self.perm_resolve,
            is_active=False,
        )
        transition.save()
        self.assertIsNotNone(transition.pk)

    def test_valid_active_transition_saved_successfully(self):
        transition = TicketStatusTransition(
            from_status=self.status_new_a,
            to_status=self.status_in_progress_a,
            required_permission=self.perm_start,
            is_active=True,
        )
        transition.save()
        self.assertIsNotNone(transition.pk)

    def test_transition_from_cancelled_status_rejected(self):
        transition = TicketStatusTransition(
            from_status=self.status_cancelled_a,
            to_status=self.status_in_progress_a,
            required_permission=self.perm_reopen,
            is_active=True,
        )
        with self.assertRaises(DjangoValidationError) as ctx:
            transition.save()
        self.assertIn("from_status", ctx.exception.message_dict)
        self.assertEqual(
            ctx.exception.message_dict["from_status"],
            ["Cancelled tickets cannot transition to another status."],
        )

    def test_active_transition_without_required_permission_rejected(self):
        transition = TicketStatusTransition(
            from_status=self.status_new_a,
            to_status=self.status_in_progress_a,
            required_permission=None,
            is_active=True,
        )
        with self.assertRaises(DjangoValidationError) as ctx:
            transition.clean()
        self.assertIn("required_permission", ctx.exception.message_dict)
        self.assertEqual(
            ctx.exception.message_dict["required_permission"],
            ["An active transition must have a required permission."],
        )

    def test_transition_to_initial_status_rejected(self):
        transition = TicketStatusTransition(
            from_status=self.status_in_progress_a,
            to_status=self.status_new_a,
            required_permission=self.perm_start,
            is_active=True,
        )
        with self.assertRaises(DjangoValidationError) as ctx:
            transition.save()
        self.assertIn("to_status", ctx.exception.message_dict)
        self.assertEqual(
            ctx.exception.message_dict["to_status"],
            ["Cannot transition to the initial status."],
        )

    def test_closed_without_prior_resolution_rejected(self):
        transition = TicketStatusTransition(
            from_status=self.status_in_progress_a,
            to_status=self.status_closed_a,
            required_permission=self.perm_close,
            is_active=True,
        )
        with self.assertRaises(DjangoValidationError) as ctx:
            transition.save()
        self.assertIn("to_status", ctx.exception.message_dict)
        self.assertEqual(
            ctx.exception.message_dict["to_status"],
            ["A ticket must be resolved before closure."],
        )

    def test_closed_to_resolved_transition_rejected(self):
        transition = TicketStatusTransition(
            from_status=self.status_closed_a,
            to_status=self.status_resolved_a,
            required_permission=self.perm_resolve,
            is_active=True,
        )
        with self.assertRaises(DjangoValidationError) as ctx:
            transition.save()
        self.assertIn("to_status", ctx.exception.message_dict)
        self.assertEqual(
            ctx.exception.message_dict["to_status"],
            ["A closed ticket can only be reopened into an active working status."],
        )

    def test_cancellation_after_resolution_or_closure_rejected(self):
        transition = TicketStatusTransition(
            from_status=self.status_resolved_a,
            to_status=self.status_cancelled_a,
            required_permission=self.perm_cancel,
            is_active=True,
        )
        with self.assertRaises(DjangoValidationError) as ctx:
            transition.save()
        self.assertIn("to_status", ctx.exception.message_dict)
        self.assertEqual(
            ctx.exception.message_dict["to_status"],
            ["Only active tickets can be cancelled."],
        )

    def test_permission_mismatch_for_protected_transition_rejected(self):
        transition = TicketStatusTransition(
            from_status=self.status_in_progress_a,
            to_status=self.status_resolved_a,
            required_permission=self.perm_start,
            is_active=True,
        )
        with self.assertRaises(DjangoValidationError) as ctx:
            transition.save()
        self.assertIn("required_permission", ctx.exception.message_dict)
        self.assertEqual(
            ctx.exception.message_dict["required_permission"],
            ["This transition requires tickets.resolve."],
        )

    def test_valid_cancellation_transition_allowed(self):
        transition = TicketStatusTransition(
            from_status=self.status_new_a,
            to_status=self.status_cancelled_a,
            required_permission=self.perm_cancel,
            is_active=True,
        )
        transition.save()
        self.assertIsNotNone(transition.pk)

    def test_valid_close_transition_allowed(self):
        transition = TicketStatusTransition(
            from_status=self.status_resolved_a,
            to_status=self.status_closed_a,
            required_permission=self.perm_close,
            is_active=True,
        )
        transition.save()
        self.assertIsNotNone(transition.pk)

    def test_valid_reopen_transition_allowed(self):
        transition = TicketStatusTransition(
            from_status=self.status_resolved_a,
            to_status=self.status_in_progress_a,
            required_permission=self.perm_reopen,
            is_active=True,
        )
        transition.save()
        self.assertIsNotNone(transition.pk)


class WorkflowServiceTransitionLookupTests(TestCase):
    def setUp(self):
        self.customer = Customer.objects.create(
            company_name="Acme Corp",
            ticket_key="ACME",
        )
        self.other_customer = Customer.objects.create(
            company_name="Globex Corp",
            ticket_key="GLBX",
        )

        self.user = User.objects.create_user(
            email="user@example.com",
            password="Password123!",
        )

        self.category = TicketCategory.objects.create(
            customer=self.customer,
            name="Support",
        )

        self.perm_start, _ = Permission.objects.get_or_create(
            module="tickets",
            action="start",
            defaults={"name": "Ticket Start"},
        )
        self.perm_resolve, _ = Permission.objects.get_or_create(
            module="tickets",
            action="resolve",
            defaults={"name": "Ticket Resolve"},
        )

        self.status_new = TicketStatus.objects.create(
            customer=self.customer,
            name="New",
            is_initial=True,
            is_active=True,
        )
        self.status_in_progress = TicketStatus.objects.create(
            customer=self.customer,
            name="In Progress",
            is_active=True,
        )
        self.status_resolved = TicketStatus.objects.create(
            customer=self.customer,
            name="Resolved",
            is_resolved=True,
            is_active=True,
        )
        self.status_inactive = TicketStatus.objects.create(
            customer=self.customer,
            name="Inactive Status",
            is_active=False,
        )

        self.status_other_customer = TicketStatus.objects.create(
            customer=self.other_customer,
            name="Other New",
            is_initial=True,
            is_active=True,
        )

        self.transition_start = TicketStatusTransition.objects.create(
            from_status=self.status_new,
            to_status=self.status_in_progress,
            required_permission=self.perm_start,
            is_active=True,
        )

        self.ticket = Ticket.objects.create(
            ticket_number="ACME-001",
            subject="Test ticket",
            description="Testing transition lookup",
            customer=self.customer,
            created_by=self.user,
            category=self.category,
            status=self.status_new,
        )

    def test_successful_configured_transition_lookup(self):
        transition = get_configured_transition(
            ticket=self.ticket,
            to_status=self.status_in_progress,
        )
        self.assertEqual(transition.id, self.transition_start.id)
        self.assertEqual(
            transition.required_permission.id, self.perm_start.id
        )

    def test_ticket_status_belongs_to_different_customer_rejected(self):
        # Mismatched ticket customer and current status customer
        self.ticket.customer = self.other_customer
        with self.assertRaises(DRFValidationError) as ctx:
            get_configured_transition(
                ticket=self.ticket,
                to_status=self.status_in_progress,
            )
        self.assertEqual(
            ctx.exception.detail["status"],
            "Ticket status does not belong to its customer.",
        )

    def test_destination_status_belongs_to_different_customer_rejected(self):
        with self.assertRaises(DRFValidationError) as ctx:
            get_configured_transition(
                ticket=self.ticket,
                to_status=self.status_other_customer,
            )
        self.assertEqual(
            ctx.exception.detail["status"],
            "Destination status belongs to another customer.",
        )

    def test_inactive_ticket_status_rejected(self):
        self.ticket.status = self.status_inactive
        with self.assertRaises(DRFValidationError) as ctx:
            get_configured_transition(
                ticket=self.ticket,
                to_status=self.status_in_progress,
            )
        self.assertEqual(
            ctx.exception.detail["status"],
            "Inactive statuses cannot be used.",
        )

    def test_inactive_destination_status_rejected(self):
        with self.assertRaises(DRFValidationError) as ctx:
            get_configured_transition(
                ticket=self.ticket,
                to_status=self.status_inactive,
            )
        self.assertEqual(
            ctx.exception.detail["status"],
            "Inactive statuses cannot be used.",
        )

    def test_unconfigured_transition_rejected(self):
        # New -> Resolved is not configured in setUp
        with self.assertRaises(DRFValidationError) as ctx:
            get_configured_transition(
                ticket=self.ticket,
                to_status=self.status_resolved,
            )
        self.assertEqual(
            ctx.exception.detail["status"],
            "This status transition is not allowed.",
        )


class TicketModelValidationTests(TestCase):
    def setUp(self):
        self.customer_a = Customer.objects.create(
            company_name="Acme Corp",
            ticket_key="ACME",
        )
        self.customer_b = Customer.objects.create(
            company_name="Globex Corp",
            ticket_key="GLBX",
        )
        self.user = User.objects.create_user(
            email="tester@example.com",
            password="Password123!",
        )
        self.category_a = TicketCategory.objects.create(
            customer=self.customer_a,
            name="General A",
        )
        self.category_b = TicketCategory.objects.create(
            customer=self.customer_b,
            name="General B",
        )
        self.status_new_a = TicketStatus.objects.create(
            customer=self.customer_a,
            name="New",
            is_initial=True,
            is_active=True,
        )
        self.status_progress_a = TicketStatus.objects.create(
            customer=self.customer_a,
            name="In Progress",
            is_active=True,
        )
        self.status_inactive_initial_a = TicketStatus.objects.create(
            customer=self.customer_a,
            name="Inactive Init",
            is_initial=False,
            is_active=False,
        )
        self.status_new_b = TicketStatus.objects.create(
            customer=self.customer_b,
            name="New B",
            is_initial=True,
            is_active=True,
        )

    def test_new_ticket_must_start_in_initial_status(self):
        ticket = Ticket(
            ticket_number="ACME-100",
            subject="Invalid initial status",
            description="Testing",
            customer=self.customer_a,
            created_by=self.user,
            category=self.category_a,
            status=self.status_progress_a,
        )
        with self.assertRaises(DjangoValidationError) as ctx:
            ticket.clean()
        self.assertIn("status", ctx.exception.message_dict)
        self.assertEqual(
            ctx.exception.message_dict["status"],
            ["New tickets must start in the active initial status."],
        )

    def test_new_ticket_status_must_belong_to_customer(self):
        ticket = Ticket(
            ticket_number="ACME-101",
            subject="Wrong customer status",
            description="Testing",
            customer=self.customer_a,
            created_by=self.user,
            category=self.category_a,
            status=self.status_new_b,
        )
        with self.assertRaises(DjangoValidationError) as ctx:
            ticket.clean()
        self.assertIn("status", ctx.exception.message_dict)
        self.assertEqual(
            ctx.exception.message_dict["status"],
            ["Ticket status must belong to the ticket's customer."],
        )

    def test_new_ticket_category_must_belong_to_customer(self):
        ticket = Ticket(
            ticket_number="ACME-102",
            subject="Wrong customer category",
            description="Testing",
            customer=self.customer_a,
            created_by=self.user,
            category=self.category_b,
            status=self.status_new_a,
        )
        with self.assertRaises(DjangoValidationError) as ctx:
            ticket.clean()
        self.assertIn("category", ctx.exception.message_dict)
        self.assertEqual(
            ctx.exception.message_dict["category"],
            ["Ticket category must belong to the ticket's customer."],
        )

    def test_ticket_customer_cannot_be_transferred(self):
        ticket = Ticket.objects.create(
            ticket_number="ACME-103",
            subject="Transfer test",
            description="Testing",
            customer=self.customer_a,
            created_by=self.user,
            category=self.category_a,
            status=self.status_new_a,
        )
        ticket.customer = self.customer_b
        with self.assertRaises(DjangoValidationError) as ctx:
            ticket.clean()
        self.assertIn("customer", ctx.exception.message_dict)
        self.assertEqual(
            ctx.exception.message_dict["customer"],
            ["A ticket cannot be transferred to another customer."],
        )

    def test_inactive_transition_rejected(self):
        inactive_transition = TicketStatusTransition(
            from_status=self.status_new,
            to_status=self.status_resolved,
            required_permission=self.perm_resolve,
            is_active=False,
        )
        inactive_transition.save()

        with self.assertRaises(DRFValidationError) as ctx:
            get_configured_transition(
                ticket=self.ticket,
                to_status=self.status_resolved,
            )
        self.assertEqual(
            ctx.exception.detail["status"],
            "This status transition is not allowed.",
        )


class TicketModelValidationTests(TestCase):
    def setUp(self):
        self.customer_a = Customer.objects.create(
            company_name="Acme Corp",
            ticket_key="ACME",
        )
        self.customer_b = Customer.objects.create(
            company_name="Globex Corp",
            ticket_key="GLBX",
        )
        self.user = User.objects.create_user(
            email="tester@example.com",
            password="Password123!",
        )
        self.category_a = TicketCategory.objects.create(
            customer=self.customer_a,
            name="General A",
        )
        self.category_b = TicketCategory.objects.create(
            customer=self.customer_b,
            name="General B",
        )
        self.status_new_a = TicketStatus.objects.create(
            customer=self.customer_a,
            name="New",
            is_initial=True,
            is_active=True,
        )
        self.status_progress_a = TicketStatus.objects.create(
            customer=self.customer_a,
            name="In Progress",
            is_active=True,
        )
        self.status_inactive_initial_a = TicketStatus.objects.create(
            customer=self.customer_a,
            name="Inactive Init",
            is_initial=False,
            is_active=False,
        )
        self.status_new_b = TicketStatus.objects.create(
            customer=self.customer_b,
            name="New B",
            is_initial=True,
            is_active=True,
        )

    def test_new_ticket_must_start_in_initial_status(self):
        ticket = Ticket(
            ticket_number="ACME-100",
            subject="Invalid initial status",
            description="Testing",
            customer=self.customer_a,
            created_by=self.user,
            category=self.category_a,
            status=self.status_progress_a,
        )
        with self.assertRaises(DjangoValidationError) as ctx:
            ticket.clean()
        self.assertIn("status", ctx.exception.message_dict)
        self.assertEqual(
            ctx.exception.message_dict["status"],
            ["New tickets must start in the active initial status."],
        )

    def test_new_ticket_status_must_belong_to_customer(self):
        ticket = Ticket(
            ticket_number="ACME-101",
            subject="Wrong customer status",
            description="Testing",
            customer=self.customer_a,
            created_by=self.user,
            category=self.category_a,
            status=self.status_new_b,
        )
        with self.assertRaises(DjangoValidationError) as ctx:
            ticket.clean()
        self.assertIn("status", ctx.exception.message_dict)
        self.assertEqual(
            ctx.exception.message_dict["status"],
            ["Ticket status must belong to the ticket's customer."],
        )

    def test_new_ticket_category_must_belong_to_customer(self):
        ticket = Ticket(
            ticket_number="ACME-102",
            subject="Wrong customer category",
            description="Testing",
            customer=self.customer_a,
            created_by=self.user,
            category=self.category_b,
            status=self.status_new_a,
        )
        with self.assertRaises(DjangoValidationError) as ctx:
            ticket.clean()
        self.assertIn("category", ctx.exception.message_dict)
        self.assertEqual(
            ctx.exception.message_dict["category"],
            ["Ticket category must belong to the ticket's customer."],
        )

    def test_ticket_customer_cannot_be_transferred(self):
        ticket = Ticket.objects.create(
            ticket_number="ACME-103",
            subject="Transfer test",
            description="Testing",
            customer=self.customer_a,
            created_by=self.user,
            category=self.category_a,
            status=self.status_new_a,
        )
        ticket.customer = self.customer_b
        with self.assertRaises(DjangoValidationError) as ctx:
            ticket.clean()
        self.assertIn("customer", ctx.exception.message_dict)
        self.assertEqual(
            ctx.exception.message_dict["customer"],
            ["A ticket cannot be transferred to another customer."],
        )
