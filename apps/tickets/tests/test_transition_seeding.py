import importlib
from django.apps import apps
from django.core.exceptions import ValidationError
from django.db import connection
from django.test import TestCase

from apps.accounts.models import Permission
from apps.customers.models import Customer
from apps.customers.services.customers import CustomerCreationService
from apps.tickets.models import TicketStatus, TicketStatusTransition
from apps.tickets.services.status import seed_customer_statuses
from apps.tickets.services.transition_seed import (
    DEFAULT_TRANSITIONS,
    seed_customer_transitions,
)


class TicketTransitionSeedingTests(TestCase):
    def setUp(self):
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

        self.customer = Customer.objects.create(
            company_name="Alpha Corp",
            ticket_key="ALPH",
            is_active=True,
            account_status=Customer.AccountStatus.ACTIVE,
        )

    def _create_default_statuses(self, customer):
        status_new = TicketStatus.objects.create(
            customer=customer,
            name="New",
            is_initial=True,
            is_resolved=False,
            is_closed=False,
            is_active=True,
            position=1,
        )
        status_in_progress = TicketStatus.objects.create(
            customer=customer,
            name="In Progress",
            is_initial=False,
            is_resolved=False,
            is_closed=False,
            is_active=True,
            position=2,
        )
        status_resolved = TicketStatus.objects.create(
            customer=customer,
            name="Resolved",
            is_initial=False,
            is_resolved=True,
            is_closed=False,
            is_active=True,
            position=3,
        )
        status_done = TicketStatus.objects.create(
            customer=customer,
            name="Done",
            is_initial=False,
            is_resolved=False,
            is_closed=True,
            is_active=True,
            position=4,
        )
        return {
            "new": status_new,
            "in progress": status_in_progress,
            "resolved": status_resolved,
            "done": status_done,
        }

    def test_customer_has_four_valid_default_statuses_creates_six_transitions(self):
        """Customer has four valid default statuses -> Six transitions created"""
        self._create_default_statuses(self.customer)
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

    def test_seeding_called_twice_still_six_transitions(self):
        """Seeding called twice -> Still six transitions"""
        self._create_default_statuses(self.customer)
        seed_customer_transitions(self.customer)
        result = seed_customer_transitions(self.customer)
        self.assertTrue(result)

        transitions = TicketStatusTransition.objects.filter(
            from_status__customer=self.customer
        )
        self.assertEqual(transitions.count(), 6)

    def test_new_customer_gets_same_six_transitions(self):
        """New customer -> Gets the same six transitions"""
        new_customer = Customer.objects.create(
            company_name="Beta Corp",
            ticket_key="BETA",
            is_active=True,
            account_status=Customer.AccountStatus.ACTIVE,
        )
        seed_customer_statuses(new_customer)

        statuses = TicketStatus.objects.filter(customer=new_customer)
        self.assertEqual(statuses.count(), 4)

        transitions = TicketStatusTransition.objects.filter(
            from_status__customer=new_customer
        )
        self.assertEqual(transitions.count(), 6)

    def test_customer_a_and_customer_b_independent_transitions(self):
        """Customer A and Customer B -> Each has independent transitions"""
        customer_b = Customer.objects.create(
            company_name="Gamma Corp",
            ticket_key="GAMM",
            is_active=True,
            account_status=Customer.AccountStatus.ACTIVE,
        )
        self._create_default_statuses(self.customer)
        self._create_default_statuses(customer_b)

        seed_customer_transitions(self.customer)
        seed_customer_transitions(customer_b)

        transitions_a = TicketStatusTransition.objects.filter(
            from_status__customer=self.customer
        )
        transitions_b = TicketStatusTransition.objects.filter(
            from_status__customer=customer_b
        )

        self.assertEqual(transitions_a.count(), 6)
        self.assertEqual(transitions_b.count(), 6)

        # Verify Customer A's transitions only reference Customer A's statuses
        for t in transitions_a:
            self.assertEqual(t.from_status.customer_id, self.customer.id)
            self.assertEqual(t.to_status.customer_id, self.customer.id)

        # Verify Customer B's transitions only reference Customer B's statuses
        for t in transitions_b:
            self.assertEqual(t.from_status.customer_id, customer_b.id)
            self.assertEqual(t.to_status.customer_id, customer_b.id)

    def test_existing_custom_transition_not_overwritten(self):
        """Existing custom transition -> Not overwritten"""
        statuses = self._create_default_statuses(self.customer)
        custom_status = TicketStatus.objects.create(
            customer=self.customer,
            name="Review",
            is_active=True,
            position=5,
        )
        custom_transition = TicketStatusTransition.objects.create(
            from_status=statuses["in progress"],
            to_status=custom_status,
            required_permission=self.perm_transition,
            is_active=True,
        )

        seed_customer_transitions(self.customer)

        transitions = TicketStatusTransition.objects.filter(
            from_status__customer=self.customer
        )
        # 6 default transitions + 1 custom transition = 7
        self.assertEqual(transitions.count(), 7)

        # Custom transition is still present and unaltered
        custom_transition.refresh_from_db()
        self.assertEqual(custom_transition.required_permission, self.perm_transition)
        self.assertTrue(custom_transition.is_active)

    def test_missing_permission_transaction_rolls_back(self):
        """Missing permission -> Transaction rolls back"""
        self._create_default_statuses(self.customer)
        # Delete tickets.close
        Permission.objects.filter(module="tickets", action="close").delete()

        with self.assertRaises(ValidationError) as ctx:
            seed_customer_transitions(self.customer)

        self.assertIn("Missing permission", str(ctx.exception))

        # Because transaction is atomic, no transitions should have been persisted
        transitions = TicketStatusTransition.objects.filter(
            from_status__customer=self.customer
        )
        self.assertEqual(transitions.count(), 0)

    def test_missing_default_status_raises_validation_error(self):
        """Missing default status -> Validation error"""
        statuses = self._create_default_statuses(self.customer)
        statuses["done"].delete()

        with self.assertRaises(ValidationError) as ctx:
            seed_customer_transitions(self.customer)

        self.assertIn("Missing default ticket statuses", str(ctx.exception))

    def test_incorrect_lifecycle_flag_raises_validation_error(self):
        """Incorrect lifecycle flag -> Validation error"""
        statuses = self._create_default_statuses(self.customer)
        statuses["resolved"].is_resolved = False
        statuses["resolved"].save()

        with self.assertRaises(ValidationError) as ctx:
            seed_customer_transitions(self.customer)

        self.assertIn("invalid or inactive lifecycle configuration", str(ctx.exception))

    def test_disabled_existing_default_transition_conflict_reported(self):
        """Disabled existing default transition -> Conflict reported"""
        statuses = self._create_default_statuses(self.customer)
        TicketStatusTransition.objects.create(
            from_status=statuses["new"],
            to_status=statuses["in progress"],
            required_permission=self.perm_start,
            is_active=False,
        )

        with self.assertRaises(ValidationError) as ctx:
            seed_customer_transitions(self.customer)

        self.assertIn("differs from the default", str(ctx.exception))

    def test_existing_customer_without_statuses_migration_skips(self):
        """Existing customer without statuses -> Migration skips without creating partial configuration"""
        # self.customer has no statuses created
        migration_0015 = importlib.import_module(
            "apps.tickets.migrations.0015_seed_default_ticket_transitions"
        )
        migration_0015.seed_default_ticket_transitions(apps, connection.schema_editor())

        transitions = TicketStatusTransition.objects.filter(
            from_status__customer=self.customer
        )
        self.assertEqual(transitions.count(), 0)
