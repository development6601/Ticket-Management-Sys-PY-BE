import threading
from unittest.mock import patch
import uuid

from django.db import connection
from django.test import TestCase, TransactionTestCase
from rest_framework.exceptions import PermissionDenied, ValidationError

from apps.accounts.models import (
    Permission,
    Role,
    RolePermission,
    User,
    UserPermission,
    UserRole,
)
from apps.customers.models import Customer, CustomerUser
from apps.support.models import SupportTeam, TeamMember
from apps.tickets.models import (
    Ticket,
    TicketActivityLog,
    TicketAssignment,
    TicketCategory,
    TicketStatus,
    TicketStatusTransition,
)
from apps.tickets.services.workflow import execute_ticket_transition
from apps.tickets.tests.test_workflow_authorization import WorkflowAuthorizationTests


class TicketWorkflowExecutionTests(TestCase):
    def setUp(self):
        # Reuse fixtures established in WorkflowAuthorizationTests
        WorkflowAuthorizationTests.setUp(self)

    def test_01_assigned_agent_starts_ticket(self):
        """Assigned agent starts ticket -> Status changes to In Progress; assignee unchanged"""
        self.ticket.status = self.status_new
        self.ticket.save()

        updated_ticket = execute_ticket_transition(
            ticket_id=self.ticket.pk,
            to_status_id=self.status_in_progress.pk,
            user=self.agent_user,
        )

        self.ticket.refresh_from_db()
        self.assertEqual(self.ticket.status, self.status_in_progress)
        self.assertEqual(self.ticket.assigned_employee, self.agent_user)
        self.assertEqual(updated_ticket.status, self.status_in_progress)
        self.assertEqual(updated_ticket.assigned_employee, self.agent_user)

    def test_02_eligible_agent_starts_unassigned_ticket(self):
        """Eligible agent starts unassigned ticket -> Status changes and agent is assigned"""
        ticket = Ticket.objects.create(
            ticket_number=f"ACME-{uuid.uuid4().hex[:6].upper()}",
            subject="Unassigned Start Ticket",
            description="Testing unassigned start",
            customer=self.customer,
            category=self.category,
            status=self.status_new,
            created_by=self.creator_user,
            assigned_team=self.team,
            assigned_employee=None,
        )

        updated_ticket = execute_ticket_transition(
            ticket_id=ticket.pk,
            to_status_id=self.status_in_progress.pk,
            user=self.agent_user,
        )

        ticket.refresh_from_db()
        self.assertEqual(ticket.status, self.status_in_progress)
        self.assertEqual(ticket.assigned_employee, self.agent_user)
        self.assertEqual(updated_ticket.assigned_employee, self.agent_user)
        self.assertTrue(
            TicketAssignment.objects.filter(
                ticket=ticket,
                employee=self.agent_user,
                unassigned_at__isnull=True,
            ).exists()
        )

    def test_03_eligible_agent_directly_resolves_unassigned_ticket(self):
        """Eligible agent directly resolves unassigned ticket -> Claim and resolution commit together"""
        ticket = Ticket.objects.create(
            ticket_number=f"ACME-{uuid.uuid4().hex[:6].upper()}",
            subject="Direct Resolve Ticket",
            description="Testing unassigned direct resolve",
            customer=self.customer,
            category=self.category,
            status=self.status_new,
            created_by=self.creator_user,
            assigned_team=self.team,
            assigned_employee=None,
        )

        updated_ticket = execute_ticket_transition(
            ticket_id=ticket.pk,
            to_status_id=self.status_resolved.pk,
            user=self.agent_user,
            resolution_type=Ticket.ResolutionType.FIXED,
            resolution_notes="Resolved directly from unassigned queue.",
        )

        ticket.refresh_from_db()
        self.assertEqual(ticket.status, self.status_resolved)
        self.assertEqual(ticket.assigned_employee, self.agent_user)
        self.assertEqual(ticket.resolution_type, Ticket.ResolutionType.FIXED)
        self.assertEqual(ticket.resolution_notes, "Resolved directly from unassigned queue.")
        self.assertEqual(ticket.resolved_by, self.agent_user)
        self.assertIsNotNone(ticket.resolved_at)

        self.assertTrue(
            TicketAssignment.objects.filter(
                ticket=ticket,
                employee=self.agent_user,
                unassigned_at__isnull=True,
            ).exists()
        )
        self.assertTrue(
            TicketActivityLog.objects.filter(
                ticket=ticket,
                activity_type="TICKET_RESOLVE",
            ).exists()
        )

    def test_04_manager_resolves_another_agents_ticket(self):
        """Manager resolves another agent's ticket -> Resolution succeeds; assignee unchanged"""
        # self.ticket is In Progress and assigned to self.agent_user
        updated_ticket = execute_ticket_transition(
            ticket_id=self.ticket.pk,
            to_status_id=self.status_resolved.pk,
            user=self.manager_user,
            resolution_type=Ticket.ResolutionType.FIXED,
            resolution_notes="Manager resolving on behalf of team.",
        )

        self.ticket.refresh_from_db()
        self.assertEqual(self.ticket.status, self.status_resolved)
        self.assertEqual(self.ticket.assigned_employee, self.agent_user)
        self.assertEqual(self.ticket.resolved_by, self.manager_user)
        self.assertEqual(updated_ticket.assigned_employee, self.agent_user)

    def test_05_system_admin_resolves_unassigned_ticket(self):
        """System Admin resolves unassigned ticket -> Resolution succeeds; ticket remains unassigned"""
        admin_user = User.objects.create_superuser(
            email=f"sysadmin_{uuid.uuid4().hex[:6]}@example.com",
            password="Password123!",
            account_status=User.AccountStatus.ACTIVE,
        )
        ticket = Ticket.objects.create(
            ticket_number=f"ACME-{uuid.uuid4().hex[:6].upper()}",
            subject="Admin Resolve Ticket",
            description="Testing admin resolve unassigned ticket",
            customer=self.customer,
            category=self.category,
            status=self.status_new,
            created_by=self.creator_user,
            assigned_team=self.team,
            assigned_employee=None,
        )

        updated_ticket = execute_ticket_transition(
            ticket_id=ticket.pk,
            to_status_id=self.status_resolved.pk,
            user=admin_user,
            resolution_type=Ticket.ResolutionType.FIXED,
            resolution_notes="Resolved by system administrator.",
        )

        ticket.refresh_from_db()
        self.assertEqual(ticket.status, self.status_resolved)
        self.assertIsNone(ticket.assigned_employee)
        self.assertEqual(ticket.resolved_by, admin_user)
        self.assertFalse(
            TicketAssignment.objects.filter(ticket=ticket).exists()
        )

    def test_06_customer_closes_resolved_ticket(self):
        """Customer closes resolved ticket -> Done status and confirmation metadata saved"""
        execute_ticket_transition(
            ticket_id=self.ticket.pk,
            to_status_id=self.status_resolved.pk,
            user=self.agent_user,
            resolution_type=Ticket.ResolutionType.FIXED,
            resolution_notes="Initial resolution by agent.",
        )

        UserPermission.objects.create(
            user=self.creator_user,
            permission=self.perm_close,
        )

        updated_ticket = execute_ticket_transition(
            ticket_id=self.ticket.pk,
            to_status_id=self.status_done.pk,
            user=self.creator_user,
        )

        self.ticket.refresh_from_db()
        self.assertEqual(self.ticket.status, self.status_done)
        self.assertEqual(self.ticket.closed_by, self.creator_user)
        self.assertEqual(self.ticket.resolution_confirmed_by, self.creator_user)
        self.assertIsNotNone(self.ticket.closed_at)
        self.assertIsNotNone(self.ticket.resolution_confirmed_at)

    def test_07_customer_reopens_resolved_ticket(self):
        """Customer reopens resolved ticket -> In Progress; reopen count incremented"""
        execute_ticket_transition(
            ticket_id=self.ticket.pk,
            to_status_id=self.status_resolved.pk,
            user=self.agent_user,
            resolution_type=Ticket.ResolutionType.FIXED,
            resolution_notes="Initial resolution.",
        )

        UserPermission.objects.create(
            user=self.creator_user,
            permission=self.perm_reopen,
        )
        initial_reopen_count = self.ticket.reopen_count

        updated_ticket = execute_ticket_transition(
            ticket_id=self.ticket.pk,
            to_status_id=self.status_in_progress.pk,
            user=self.creator_user,
            reason="The problem is still occurring.",
        )

        self.ticket.refresh_from_db()
        self.assertEqual(self.ticket.status, self.status_in_progress)
        self.assertEqual(self.ticket.reopen_count, initial_reopen_count + 1)
        self.assertIsNone(self.ticket.resolution_type)
        self.assertEqual(self.ticket.resolution_notes, "")
        self.assertIsNone(self.ticket.resolved_at)
        self.assertIsNone(self.ticket.resolved_by)

    def test_08_customer_reopens_done_ticket(self):
        """Customer reopens Done ticket -> In Progress; closure fields reset"""
        execute_ticket_transition(
            ticket_id=self.ticket.pk,
            to_status_id=self.status_resolved.pk,
            user=self.agent_user,
            resolution_type=Ticket.ResolutionType.FIXED,
            resolution_notes="Fixed before close.",
        )

        UserPermission.objects.create(
            user=self.creator_user,
            permission=self.perm_close,
        )
        execute_ticket_transition(
            ticket_id=self.ticket.pk,
            to_status_id=self.status_done.pk,
            user=self.creator_user,
        )

        UserPermission.objects.create(
            user=self.creator_user,
            permission=self.perm_reopen,
        )

        updated_ticket = execute_ticket_transition(
            ticket_id=self.ticket.pk,
            to_status_id=self.status_in_progress.pk,
            user=self.creator_user,
            reason="Issue resurfaced after ticket was marked done.",
        )

        self.ticket.refresh_from_db()
        self.assertEqual(self.ticket.status, self.status_in_progress)
        self.assertEqual(self.ticket.reopen_count, 1)
        self.assertIsNone(self.ticket.closed_at)
        self.assertIsNone(self.ticket.closed_by)
        self.assertIsNone(self.ticket.resolution_confirmed_at)
        self.assertIsNone(self.ticket.resolution_confirmed_by)
        self.assertIsNone(self.ticket.resolved_at)
        self.assertIsNone(self.ticket.resolved_by)
        self.assertIsNone(self.ticket.resolution_type)
        self.assertEqual(self.ticket.resolution_notes, "")

    def test_09_resolve_without_notes(self):
        """Resolve without notes -> Validation error; no changes"""
        ticket_id = self.ticket.pk
        original_status = self.ticket.status

        with self.assertRaises(ValidationError) as ctx:
            execute_ticket_transition(
                ticket_id=ticket_id,
                to_status_id=self.status_resolved.pk,
                user=self.agent_user,
                resolution_type=Ticket.ResolutionType.FIXED,
                resolution_notes="",
            )

        self.assertIn("resolution_notes", ctx.exception.detail)
        self.ticket.refresh_from_db()
        self.assertEqual(self.ticket.status, original_status)
        self.assertIsNone(self.ticket.resolved_at)

    def test_10_resolve_with_invalid_resolution_type(self):
        """Resolve with invalid resolution type -> Validation error; no changes"""
        ticket_id = self.ticket.pk
        original_status = self.ticket.status

        with self.assertRaises(ValidationError) as ctx:
            execute_ticket_transition(
                ticket_id=ticket_id,
                to_status_id=self.status_resolved.pk,
                user=self.agent_user,
                resolution_type="INVALID_TYPE",
                resolution_notes="Valid resolution notes.",
            )

        self.assertIn("resolution_type", ctx.exception.detail)
        self.ticket.refresh_from_db()
        self.assertEqual(self.ticket.status, original_status)
        self.assertIsNone(self.ticket.resolved_at)

    def test_11_reopen_without_reason(self):
        """Reopen without reason -> Validation error; no changes"""
        execute_ticket_transition(
            ticket_id=self.ticket.pk,
            to_status_id=self.status_resolved.pk,
            user=self.agent_user,
            resolution_type=Ticket.ResolutionType.FIXED,
            resolution_notes="Resolved before reopen test.",
        )

        UserPermission.objects.create(
            user=self.creator_user,
            permission=self.perm_reopen,
        )

        with self.assertRaises(ValidationError) as ctx:
            execute_ticket_transition(
                ticket_id=self.ticket.pk,
                to_status_id=self.status_in_progress.pk,
                user=self.creator_user,
                reason="",
            )

        self.assertIn("reason", ctx.exception.detail)
        self.ticket.refresh_from_db()
        self.assertEqual(self.ticket.status, self.status_resolved)
        self.assertEqual(self.ticket.reopen_count, 0)

    def test_12_unauthorized_agent_attempts_resolution(self):
        """Unauthorized agent attempts resolution -> Permission denied; no changes"""
        agent_2 = User.objects.create_user(
            email=f"agent2_{uuid.uuid4().hex[:6]}@example.com",
            password="Password123!",
            account_status=User.AccountStatus.ACTIVE,
        )
        UserRole.objects.create(user=agent_2, role=self.role_agent)
        TeamMember.objects.create(team=self.team, user=agent_2, is_active=True)

        with self.assertRaises(PermissionDenied):
            execute_ticket_transition(
                ticket_id=self.ticket.pk,
                to_status_id=self.status_resolved.pk,
                user=agent_2,
                resolution_type=Ticket.ResolutionType.FIXED,
                resolution_notes="Unauthorized resolve attempt.",
            )

        self.ticket.refresh_from_db()
        self.assertEqual(self.ticket.status, self.status_in_progress)
        self.assertEqual(self.ticket.assigned_employee, self.agent_user)
        self.assertIsNone(self.ticket.resolved_at)

    def test_13_activity_failure_rolls_back_transition(self):
        """Activity log creation fails -> Entire transaction rolls back"""
        ticket_id = self.ticket.pk
        original_status_id = self.ticket.status_id
        original_count = TicketActivityLog.objects.filter(
            ticket_id=ticket_id
        ).count()

        with patch(
            "apps.tickets.services.workflow.TicketActivityLog.objects.create",
            side_effect=RuntimeError("Audit write failed"),
        ):
            with self.assertRaises(RuntimeError):
                execute_ticket_transition(
                    ticket_id=ticket_id,
                    to_status_id=self.status_resolved.pk,
                    user=self.agent_user,
                    resolution_type=Ticket.ResolutionType.FIXED,
                    resolution_notes="Valid resolution notes.",
                )

        self.ticket.refresh_from_db()
        self.assertEqual(
            self.ticket.status_id,
            original_status_id,
        )
        self.assertEqual(
            TicketActivityLog.objects.filter(
                ticket_id=ticket_id
            ).count(),
            original_count,
        )

    def test_16_existing_assignment_history_conflicts_with_current_state(self):
        """Existing assignment history conflicts with current state -> Claim rejected without partial updates"""
        ticket = Ticket.objects.create(
            ticket_number=f"ACME-{uuid.uuid4().hex[:6].upper()}",
            subject="Conflict Ticket",
            description="Testing assignment conflict",
            customer=self.customer,
            category=self.category,
            status=self.status_new,
            created_by=self.creator_user,
            assigned_team=self.team,
            assigned_employee=None,
        )

        # Create conflicting open assignment history entry on unassigned ticket
        TicketAssignment.objects.create(
            ticket=ticket,
            team=self.team,
            employee=self.agent_user,
            assigned_by=self.agent_user,
            unassigned_at=None,
        )

        with self.assertRaises(ValidationError) as ctx:
            execute_ticket_transition(
                ticket_id=ticket.pk,
                to_status_id=self.status_in_progress.pk,
                user=self.agent_user,
            )

        self.assertIn("assigned_employee", ctx.exception.detail)
        ticket.refresh_from_db()
        self.assertEqual(ticket.status, self.status_new)
        self.assertIsNone(ticket.assigned_employee)
        self.assertEqual(
            TicketActivityLog.objects.filter(ticket=ticket).count(), 0
        )


class TicketWorkflowConcurrencyTests(TransactionTestCase):
    def setUp(self):
        WorkflowAuthorizationTests.setUp(self)

    def test_14_two_agents_claim_simultaneously(self):
        """Two agents claim simultaneously -> Only one claim succeeds"""
        agent_2 = User.objects.create_user(
            email=f"agent2_concurrent_{uuid.uuid4().hex[:6]}@example.com",
            password="Password123!",
            account_status=User.AccountStatus.ACTIVE,
        )
        UserRole.objects.create(user=agent_2, role=self.role_agent)
        TeamMember.objects.create(team=self.team, user=agent_2, is_active=True)

        ticket = Ticket.objects.create(
            ticket_number=f"ACME-{uuid.uuid4().hex[:6].upper()}",
            subject="Concurrent Claim",
            description="Testing concurrent claim",
            customer=self.customer,
            category=self.category,
            status=self.status_new,
            created_by=self.creator_user,
            assigned_team=self.team,
            assigned_employee=None,
        )

        barrier = threading.Barrier(2)
        results = []
        errors = []

        def claim_worker(user):
            try:
                barrier.wait(timeout=5)
                execute_ticket_transition(
                    ticket_id=ticket.pk,
                    to_status_id=self.status_in_progress.pk,
                    user=user,
                )
                results.append(user.pk)
            except Exception as e:
                errors.append(e)
            finally:
                connection.close()

        t1 = threading.Thread(target=claim_worker, args=(self.agent_user,))
        t2 = threading.Thread(target=claim_worker, args=(agent_2,))
        t1.start()
        t2.start()
        t1.join()
        t2.join()

        self.assertEqual(len(results), 1, f"Expected exactly 1 claim, got {len(results)}. Errors: {errors}")
        self.assertEqual(len(errors), 1)
        ticket.refresh_from_db()
        self.assertEqual(ticket.status, self.status_in_progress)
        self.assertIn(ticket.assigned_employee_id, [self.agent_user.pk, agent_2.pk])
        self.assertEqual(
            TicketAssignment.objects.filter(ticket=ticket, unassigned_at__isnull=True).count(),
            1,
        )
        self.assertEqual(
            TicketActivityLog.objects.filter(ticket=ticket, activity_type="TICKET_START").count(),
            1,
        )

    def test_15_two_requests_resolve_the_same_ticket(self):
        """Two requests resolve the same ticket -> Only one transition succeeds"""
        barrier = threading.Barrier(2)
        results = []
        errors = []

        def resolve_worker():
            try:
                barrier.wait(timeout=5)
                execute_ticket_transition(
                    ticket_id=self.ticket.pk,
                    to_status_id=self.status_resolved.pk,
                    user=self.agent_user,
                    resolution_type=Ticket.ResolutionType.FIXED,
                    resolution_notes="Resolved concurrently",
                )
                results.append(True)
            except Exception as e:
                errors.append(e)
            finally:
                connection.close()

        t1 = threading.Thread(target=resolve_worker)
        t2 = threading.Thread(target=resolve_worker)
        t1.start()
        t2.start()
        t1.join()
        t2.join()

        self.assertEqual(len(results), 1, f"Expected exactly 1 resolve, got {len(results)}. Errors: {errors}")
        self.assertEqual(len(errors), 1)
        self.ticket.refresh_from_db()
        self.assertEqual(self.ticket.status, self.status_resolved)
        self.assertEqual(
            TicketActivityLog.objects.filter(
                ticket=self.ticket, activity_type="TICKET_RESOLVE"
            ).count(),
            1,
        )
