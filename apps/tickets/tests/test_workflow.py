from django.apps import apps
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import connection
from django.test import TestCase
from rest_framework.exceptions import ValidationError as DRFValidationError

from apps.accounts.models import Permission, Role, RolePermission, User, UserPermission
from apps.accounts.permissions.constants import (
    TICKET_CLOSE,
    TICKET_REOPEN,
    TICKET_RESOLVE,
    TICKET_START,
    TICKET_TRANSITION,
)
from apps.customers.models import Customer
from apps.customers.services.customers import CustomerCreationService
import importlib
from apps.tickets.models import (
    Ticket,
    TicketCategory,
    TicketStatus,
    TicketStatusTransition,
)
from apps.tickets.services.status import seed_customer_statuses
from apps.tickets.services.transition_seed import (
    DEFAULT_TRANSITIONS,
    seed_customer_transitions,
)
from apps.tickets.services.workflow import (
    get_configured_transition,
    get_transition_action,
)


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
        self.perm_transition, _ = Permission.objects.get_or_create(
            module="tickets",
            action="transition",
            defaults={"name": "Ticket Transition"},
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

    def test_new_to_in_progress_using_tickets_start_valid(self):
        transition = TicketStatusTransition(
            from_status=self.status_new_a,
            to_status=self.status_in_progress_a,
            required_permission=self.perm_start,
            is_active=True,
        )
        transition.save()
        self.assertIsNotNone(transition.pk)

    def test_new_to_resolved_using_tickets_resolve_valid(self):
        transition = TicketStatusTransition(
            from_status=self.status_new_a,
            to_status=self.status_resolved_a,
            required_permission=self.perm_resolve,
            is_active=True,
        )
        transition.save()
        self.assertIsNotNone(transition.pk)

    def test_in_progress_to_resolved_using_tickets_resolve_valid(self):
        transition = TicketStatusTransition(
            from_status=self.status_in_progress_a,
            to_status=self.status_resolved_a,
            required_permission=self.perm_resolve,
            is_active=True,
        )
        transition.save()
        self.assertIsNotNone(transition.pk)

    def test_resolved_to_done_using_tickets_close_valid(self):
        transition = TicketStatusTransition(
            from_status=self.status_resolved_a,
            to_status=self.status_closed_a,
            required_permission=self.perm_close,
            is_active=True,
        )
        transition.save()
        self.assertIsNotNone(transition.pk)

    def test_resolved_to_in_progress_using_tickets_reopen_valid(self):
        transition = TicketStatusTransition(
            from_status=self.status_resolved_a,
            to_status=self.status_in_progress_a,
            required_permission=self.perm_reopen,
            is_active=True,
        )
        transition.save()
        self.assertIsNotNone(transition.pk)

    def test_done_to_in_progress_using_tickets_reopen_valid(self):
        transition = TicketStatusTransition(
            from_status=self.status_closed_a,
            to_status=self.status_in_progress_a,
            required_permission=self.perm_reopen,
            is_active=True,
        )
        transition.save()
        self.assertIsNotNone(transition.pk)

    def test_new_to_done_rejected(self):
        transition = TicketStatusTransition(
            from_status=self.status_new_a,
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

    def test_in_progress_to_done_rejected(self):
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

    def test_done_to_resolved_transition_rejected(self):
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
        with self.assertRaises(DRFValidationError) as ctx:
            get_configured_transition(
                ticket=self.ticket,
                to_status=self.status_resolved,
            )
        self.assertEqual(
            ctx.exception.detail["status"],
            "This status transition is not allowed.",
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


class WorkflowServiceGetTransitionActionTests(TestCase):
    def setUp(self):
        self.customer = Customer.objects.create(
            company_name="Acme Corp",
            ticket_key="ACME",
        )
        self.status_initial = TicketStatus.objects.create(
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
        self.status_closed = TicketStatus.objects.create(
            customer=self.customer,
            name="Done",
            is_closed=True,
            is_active=True,
        )

    def test_get_transition_action_close(self):
        action = get_transition_action(self.status_resolved, self.status_closed)
        self.assertEqual(action, "close")

    def test_get_transition_action_resolve(self):
        action = get_transition_action(self.status_in_progress, self.status_resolved)
        self.assertEqual(action, "resolve")

    def test_get_transition_action_reopen_from_resolved(self):
        action = get_transition_action(self.status_resolved, self.status_in_progress)
        self.assertEqual(action, "reopen")

    def test_get_transition_action_reopen_from_closed(self):
        action = get_transition_action(self.status_closed, self.status_in_progress)
        self.assertEqual(action, "reopen")

    def test_get_transition_action_start_from_initial(self):
        action = get_transition_action(self.status_initial, self.status_in_progress)
        self.assertEqual(action, "start")

    def test_get_transition_action_ordinary_transition(self):
        status_review = TicketStatus.objects.create(
            customer=self.customer,
            name="Review",
            is_active=True,
        )
        action = get_transition_action(self.status_in_progress, status_review)
        self.assertEqual(action, "transition")


class TicketWorkflowPermissionMigrationTests(TestCase):
    def test_migration_preserves_foreign_keys_and_updates_permission(self):
        # Clean up any existing tickets.close or tickets.transition for test isolation
        Permission.objects.filter(module="tickets", action="close").delete()
        Permission.objects.filter(name="Ticket Close").delete()
        Permission.objects.filter(module="tickets", action="transition").delete()

        # Create or fetch tickets.confirm permission
        confirm_perm, _ = Permission.objects.get_or_create(
            module="tickets",
            action="confirm",
            defaults={
                "name": "Ticket Confirm",
                "description": "Confirm resolution of a ticket (Resolved to Done)",
            },
        )
        confirm_id = confirm_perm.id

        role, _ = Role.objects.get_or_create(
            code="test_workflow_role",
            defaults={"name": "Test Workflow Role"},
        )
        role_perm = RolePermission.objects.create(
            role=role,
            permission=confirm_perm,
        )

        user = User.objects.create_user(
            email="workflow_perm_user@example.com",
            password="Password123!",
        )
        user_perm = UserPermission.objects.create(
            user=user,
            permission=confirm_perm,
        )

        migration_0014 = importlib.import_module(
            "apps.tickets.migrations.0014_update_ticket_workflow_permissions"
        )
        update_ticket_workflow_permissions = (
            migration_0014.update_ticket_workflow_permissions
        )

        # Execute migration function
        update_ticket_workflow_permissions(apps, connection.schema_editor())

        # Verify permission was renamed in-place and ID was preserved
        confirm_perm.refresh_from_db()
        self.assertEqual(confirm_perm.id, confirm_id)
        self.assertEqual(confirm_perm.module, "tickets")
        self.assertEqual(confirm_perm.action, "close")
        self.assertEqual(confirm_perm.name, "Ticket Close")

        # Verify foreign keys still point to the same permission
        role_perm.refresh_from_db()
        self.assertEqual(role_perm.permission_id, confirm_id)
        self.assertEqual(role_perm.permission.action, "close")

        user_perm.refresh_from_db()
        self.assertEqual(user_perm.permission_id, confirm_id)
        self.assertEqual(user_perm.permission.action, "close")

        # Verify tickets.transition was created
        transition_perm = Permission.objects.filter(
            module__iexact="tickets",
            action__iexact="transition",
        ).first()
        self.assertIsNotNone(transition_perm)
        self.assertEqual(transition_perm.name, "Ticket Transition")


class TicketTransitionSeedingServiceTests(TestCase):
    def setUp(self):
        self.customer = Customer.objects.create(
            company_name="Transition Seed Corp",
            ticket_key="TSEED",
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

        self.status_new = TicketStatus.objects.create(
            customer=self.customer,
            name="New",
            is_initial=True,
            is_resolved=False,
            is_closed=False,
            is_active=True,
            position=1,
        )
        self.status_in_progress = TicketStatus.objects.create(
            customer=self.customer,
            name="In Progress",
            is_initial=False,
            is_resolved=False,
            is_closed=False,
            is_active=True,
            position=2,
        )
        self.status_resolved = TicketStatus.objects.create(
            customer=self.customer,
            name="Resolved",
            is_initial=False,
            is_resolved=True,
            is_closed=False,
            is_active=True,
            position=3,
        )
        self.status_done = TicketStatus.objects.create(
            customer=self.customer,
            name="Done",
            is_initial=False,
            is_resolved=False,
            is_closed=True,
            is_active=True,
            position=4,
        )

    def test_seed_customer_transitions_creates_six_transitions(self):
        result = seed_customer_transitions(self.customer)
        self.assertTrue(result)

        transitions = TicketStatusTransition.objects.filter(
            from_status__customer=self.customer
        )
        self.assertEqual(transitions.count(), 6)

        expected_pairs = {
            ("New", "In Progress", "start"),
            ("New", "Resolved", "resolve"),
            ("In Progress", "Resolved", "resolve"),
            ("Resolved", "Done", "close"),
            ("Resolved", "In Progress", "reopen"),
            ("Done", "In Progress", "reopen"),
        }
        actual_pairs = {
            (
                t.from_status.name,
                t.to_status.name,
                t.required_permission.action,
            )
            for t in transitions
        }
        self.assertEqual(actual_pairs, expected_pairs)

    def test_seed_customer_transitions_is_idempotent(self):
        seed_customer_transitions(self.customer)
        result = seed_customer_transitions(self.customer)
        self.assertTrue(result)
        self.assertEqual(
            TicketStatusTransition.objects.filter(
                from_status__customer=self.customer
            ).count(),
            6,
        )

    def test_seed_customer_transitions_raises_on_missing_status(self):
        self.status_done.delete()
        with self.assertRaises(DjangoValidationError) as ctx:
            seed_customer_transitions(self.customer)
        self.assertIn("Missing default ticket statuses: done", str(ctx.exception))

    def test_seed_customer_transitions_raises_on_invalid_lifecycle_flag(self):
        self.status_resolved.is_resolved = False
        self.status_resolved.save()
        with self.assertRaises(DjangoValidationError) as ctx:
            seed_customer_transitions(self.customer)
        self.assertIn("has an invalid or inactive lifecycle configuration", str(ctx.exception))

    def test_seed_customer_transitions_raises_on_inactive_status(self):
        self.status_in_progress.is_active = False
        self.status_in_progress.save()
        with self.assertRaises(DjangoValidationError) as ctx:
            seed_customer_transitions(self.customer)
        self.assertIn("has an invalid or inactive lifecycle configuration", str(ctx.exception))

    def test_seed_customer_transitions_raises_on_conflicting_transition(self):
        TicketStatusTransition.objects.create(
            from_status=self.status_new,
            to_status=self.status_in_progress,
            required_permission=self.perm_resolve,  # different from default tickets.start
            is_active=True,
        )
        with self.assertRaises(DjangoValidationError) as ctx:
            seed_customer_transitions(self.customer)
        self.assertIn("differs from the default", str(ctx.exception))

    def test_seed_customer_transitions_raises_on_inactive_existing_transition(self):
        TicketStatusTransition.objects.create(
            from_status=self.status_new,
            to_status=self.status_in_progress,
            required_permission=self.perm_start,
            is_active=False,
        )
        with self.assertRaises(DjangoValidationError) as ctx:
            seed_customer_transitions(self.customer)
        self.assertIn("differs from the default", str(ctx.exception))


class TicketTransitionMigration0015Tests(TestCase):
    def setUp(self):
        self.customer = Customer.objects.create(
            company_name="Migration Test Corp",
            ticket_key="MIGC",
        )
        for action, name in [
            ("start", "Ticket Start"),
            ("resolve", "Ticket Resolve"),
            ("close", "Ticket Close"),
            ("reopen", "Ticket Reopen"),
        ]:
            Permission.objects.get_or_create(
                module="tickets",
                action=action,
                defaults={"name": name},
            )

    def test_migration_seeds_transitions_for_customer_with_statuses(self):
        TicketStatus.objects.create(
            customer=self.customer, name="New", is_initial=True, is_active=True
        )
        TicketStatus.objects.create(
            customer=self.customer, name="In Progress", is_active=True
        )
        TicketStatus.objects.create(
            customer=self.customer, name="Resolved", is_resolved=True, is_active=True
        )
        TicketStatus.objects.create(
            customer=self.customer, name="Done", is_closed=True, is_active=True
        )

        migration_0015 = importlib.import_module(
            "apps.tickets.migrations.0015_seed_default_ticket_transitions"
        )
        migration_0015.seed_default_ticket_transitions(apps, connection.schema_editor())

        transitions = TicketStatusTransition.objects.filter(
            from_status__customer=self.customer
        )
        self.assertEqual(transitions.count(), 6)

    def test_migration_skips_customer_without_statuses(self):
        # Customer has no statuses
        migration_0015 = importlib.import_module(
            "apps.tickets.migrations.0015_seed_default_ticket_transitions"
        )
        # Should execute without error
        migration_0015.seed_default_ticket_transitions(apps, connection.schema_editor())
        self.assertEqual(
            TicketStatusTransition.objects.filter(
                from_status__customer=self.customer
            ).count(),
            0,
        )


class TicketCustomerStatusInitializationIntegrationTests(TestCase):
    def setUp(self):
        for action, name in [
            ("start", "Ticket Start"),
            ("resolve", "Ticket Resolve"),
            ("close", "Ticket Close"),
            ("reopen", "Ticket Reopen"),
        ]:
            Permission.objects.get_or_create(
                module="tickets",
                action=action,
                defaults={"name": name},
            )

    def test_seed_customer_statuses_creates_statuses_and_transitions(self):
        customer = Customer.objects.create(
            company_name="Init Integration Corp",
            ticket_key="INITC",
        )
        seed_customer_statuses(customer)

        statuses = TicketStatus.objects.filter(customer=customer)
        self.assertEqual(statuses.count(), 4)

        transitions = TicketStatusTransition.objects.filter(
            from_status__customer=customer
        )
        self.assertEqual(transitions.count(), 6)

    def test_seed_customer_statuses_existing_valid_statuses_seeds_transitions(self):
        customer = Customer.objects.create(
            company_name="Existing Status Corp",
            ticket_key="EXISTC",
        )
        TicketStatus.objects.create(
            customer=customer, name="New", is_initial=True, is_active=True
        )
        TicketStatus.objects.create(
            customer=customer, name="In Progress", is_active=True
        )
        TicketStatus.objects.create(
            customer=customer, name="Resolved", is_resolved=True, is_active=True
        )
        TicketStatus.objects.create(
            customer=customer, name="Done", is_closed=True, is_active=True
        )

        seed_customer_statuses(customer)

        transitions = TicketStatusTransition.objects.filter(
            from_status__customer=customer
        )
        self.assertEqual(transitions.count(), 6)

    def test_customer_creation_service_calls_seed_customer_statuses_and_transitions(self):
        customer = CustomerCreationService.create_customer(
            company_name="Full Workflow Corp",
            contact_person="Alice Smith",
            email="alice@workflowcorp.com",
            phone="1234567890",
            address="123 Corp St",
            state="California",
            country="United States",
        )
        statuses = TicketStatus.objects.filter(customer=customer)
        self.assertEqual(statuses.count(), 4)

        transitions = TicketStatusTransition.objects.filter(
            from_status__customer=customer
        )
        self.assertEqual(transitions.count(), 6)

