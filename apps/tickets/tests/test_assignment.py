import threading
from unittest.mock import patch
import uuid

from django.contrib.auth import get_user_model
from django.db import connection, close_old_connections, transaction, IntegrityError
from django.test import TestCase, TransactionTestCase
from django.utils import timezone
from rest_framework.exceptions import PermissionDenied, ValidationError

from apps.accounts.models import (
    Permission,
    UserPermission,
)
from apps.support.models import SupportTeam, TeamMember
from apps.tickets.models import (
    Ticket,
    TicketActivityLog,
    TicketAssignment,
)
from apps.tickets.services.assignment import (
    assign_ticket,
    change_ticket_assignment,
    claim_unassigned_ticket,
    execute_ticket_assignment,
    get_active_assignment,
    reassign_ticket,
    transfer_ticket,
    unassign_ticket,
    validate_assignment_reason,
)


class TicketAssignmentTests(TestCase):
    def setUp(self):
        from apps.tickets.tests.test_workflow_authorization import WorkflowAuthorizationTests
        WorkflowAuthorizationTests.setUp(self)

        User = get_user_model()
        self.agent_user.email_verified = True
        self.agent_user.is_active = True
        self.agent_user.save()

        self.manager_user.email_verified = True
        self.manager_user.is_active = True
        self.manager_user.save()

        # Seed permissions
        self.perm_assign, _ = Permission.objects.get_or_create(
            module="tickets",
            action="assign",
            defaults={"name": "Ticket Assign"},
        )
        self.perm_reassign, _ = Permission.objects.get_or_create(
            module="tickets",
            action="reassign",
            defaults={"name": "Ticket Reassign"},
        )
        self.perm_unassign, _ = Permission.objects.get_or_create(
            module="tickets",
            action="unassign",
            defaults={"name": "Ticket Unassign"},
        )
        self.perm_transfer, _ = Permission.objects.get_or_create(
            module="tickets",
            action="transfer",
            defaults={"name": "Ticket Transfer"},
        )

        for perm in [
            self.perm_assign,
            self.perm_reassign,
            self.perm_unassign,
            self.perm_transfer,
        ]:
            UserPermission.objects.get_or_create(
                user=self.manager_user,
                permission=perm,
            )

        # Second agent on team 1
        self.agent_2 = User.objects.create_user(
            email="agent2@example.com",
            password="Password123!",
            account_status=User.AccountStatus.ACTIVE,
            email_verified=True,
            is_active=True,
        )
        TeamMember.objects.create(
            team=self.team,
            user=self.agent_2,
            is_active=True,
        )

        # Team 2 (also managed by self.manager_user)
        self.team_2 = SupportTeam.objects.create(
            name="Tier 2 Team",
            manager=self.manager_user,
            is_active=True,
        )
        self.agent_team_2 = User.objects.create_user(
            email="agent_t2@example.com",
            password="Password123!",
            account_status=User.AccountStatus.ACTIVE,
            email_verified=True,
            is_active=True,
        )
        TeamMember.objects.create(
            team=self.team_2,
            user=self.agent_team_2,
            is_active=True,
        )

        # Ensure initial ticket assignment
        TicketAssignment.objects.filter(ticket=self.ticket).delete()
        self.ticket.status = self.status_in_progress
        self.ticket.assigned_employee = self.agent_user
        self.ticket.assigned_team = self.team
        self.ticket.save()
        TicketAssignment.objects.create(
            ticket=self.ticket,
            team=self.team,
            employee=self.agent_user,
            assigned_by=self.manager_user,
            reason="Initial assignment",
        )

    def change(self, ticket=None, user=None, **kwargs):
        return change_ticket_assignment(
            ticket_id=(ticket or self.ticket).pk,
            user=user or self.manager_user,
            **kwargs,
        )

    def assert_active_assignment(self, ticket, employee):
        assignment = TicketAssignment.objects.filter(
            ticket=ticket,
            unassigned_at__isnull=True,
            employee__isnull=False,
        ).first()
        if employee is None:
            self.assertIsNone(assignment)
        else:
            self.assertIsNotNone(assignment)
            self.assertEqual(assignment.employee_id, employee.pk)

    def test_01_assign_employee(self):
        ticket = Ticket.objects.create(
            ticket_number=f"ACME-{uuid.uuid4().hex[:6].upper()}",
            subject="New Ticket for Assignment",
            description="Testing assign operation",
            customer=self.customer,
            category=self.category,
            status=self.status_new,
            created_by=self.creator_user,
            assigned_team=self.team,
            assigned_employee=None,
        )
        self.change(
            ticket=ticket,
            action="assign",
            employee_id=self.agent_user.pk,
        )
        ticket.refresh_from_db()
        self.assertEqual(
            ticket.assigned_employee_id,
            self.agent_user.pk,
        )
        self.assertEqual(ticket.assigned_team_id, self.team.pk)
        self.assertEqual(ticket.status_id, self.status_new.pk)
        self.assert_active_assignment(ticket, self.agent_user)

    def test_02_reassign_employee(self):
        self.change(
            action="reassign",
            employee_id=self.agent_2.pk,
            reason="John is unavailable",
        )
        self.ticket.refresh_from_db()
        self.assertEqual(
            self.ticket.assigned_employee_id,
            self.agent_2.pk,
        )
        self.assertEqual(
            self.ticket.status_id,
            self.status_in_progress.pk,
        )
        old_record = TicketAssignment.objects.get(
            ticket=self.ticket,
            employee=self.agent_user,
        )
        self.assertIsNotNone(old_record.unassigned_at)
        self.assert_active_assignment(
            self.ticket, self.agent_2
        )
        self.assertEqual(
            TicketActivityLog.objects.filter(
                ticket=self.ticket,
                activity_type="TICKET_REASSIGN",
            ).count(),
            1,
        )

    def test_03_unassign_employee(self):
        self.change(
            action="unassign",
            reason="Waiting for availability",
        )
        self.ticket.refresh_from_db()
        self.assertIsNone(self.ticket.assigned_employee_id)
        self.assertEqual(self.ticket.assigned_team_id, self.team.pk)
        self.assert_active_assignment(self.ticket, None)

    def test_04_transfer_without_employee(self):
        self.change(
            action="transfer",
            team_id=self.team_2.pk,
            reason="Tier escalation",
        )
        self.ticket.refresh_from_db()
        self.assertEqual(self.ticket.assigned_team_id, self.team_2.pk)
        self.assertIsNone(self.ticket.assigned_employee_id)
        self.assert_active_assignment(self.ticket, None)

    def test_05_transfer_with_employee(self):
        self.change(
            action="transfer",
            team_id=self.team_2.pk,
            employee_id=self.agent_team_2.pk,
            reason="Tier escalation with specialist",
        )
        self.ticket.refresh_from_db()
        self.assertEqual(self.ticket.assigned_team_id, self.team_2.pk)
        self.assertEqual(
            self.ticket.assigned_employee_id,
            self.agent_team_2.pk,
        )
        self.assert_active_assignment(self.ticket, self.agent_team_2)

    def test_06_reassign_without_reason(self):
        with self.assertRaises(ValidationError) as cm:
            self.change(
                action="reassign",
                employee_id=self.agent_2.pk,
                reason="",
            )
        self.assertIn("reason", cm.exception.detail)
        self.ticket.refresh_from_db()
        self.assertEqual(
            self.ticket.assigned_employee_id,
            self.agent_user.pk,
        )

    def test_07_transfer_without_reason(self):
        with self.assertRaises(ValidationError) as cm:
            self.change(
                action="transfer",
                team_id=self.team_2.pk,
                reason="",
            )
        self.assertIn("reason", cm.exception.detail)
        self.ticket.refresh_from_db()
        self.assertEqual(self.ticket.assigned_team_id, self.team.pk)

    def test_08_assign_inactive_employee(self):
        User = get_user_model()
        inactive_user = User.objects.create_user(
            email="inactive@example.com",
            password="Password123!",
            account_status=User.AccountStatus.DEACTIVATED,
            email_verified=True,
            is_active=False,
        )
        TeamMember.objects.create(
            team=self.team,
            user=inactive_user,
            is_active=True,
        )
        with self.assertRaises(PermissionDenied):
            self.change(
                action="reassign",
                employee_id=inactive_user.pk,
                reason="Reassign to inactive",
            )

    def test_09_assign_employee_from_another_team(self):
        with self.assertRaises(PermissionDenied):
            self.change(
                action="reassign",
                employee_id=self.agent_team_2.pk,
                reason="Reassign across teams without transfer",
            )

    def test_10_assign_customer_user(self):
        with self.assertRaises(PermissionDenied):
            self.change(
                action="reassign",
                employee_id=self.creator_user.pk,
                reason="Reassign to customer",
            )

    def test_11_assignment_in_resolved(self):
        self.ticket.status = self.status_resolved
        self.ticket.save()
        with self.assertRaises(PermissionDenied):
            self.change(
                action="reassign",
                employee_id=self.agent_2.pk,
                reason="Attempt in resolved",
            )

    def test_12_assignment_in_done(self):
        self.ticket.status = self.status_done
        self.ticket.save()
        with self.assertRaises(PermissionDenied):
            self.change(
                action="reassign",
                employee_id=self.agent_2.pk,
                reason="Attempt in done",
            )

    def test_13_agent_without_permission(self):
        with self.assertRaises(PermissionDenied):
            self.change(
                user=self.agent_user,
                action="reassign",
                employee_id=self.agent_2.pk,
                reason="Agent trying to reassign",
            )

    def test_14_manager_outside_team_scope(self):
        User = get_user_model()
        other_manager = User.objects.create_user(
            email="other_mgr@example.com",
            password="Password123!",
            account_status=User.AccountStatus.ACTIVE,
            email_verified=True,
            is_active=True,
        )
        SupportTeam.objects.create(
            name="External Team",
            manager=other_manager,
            is_active=True,
        )
        UserPermission.objects.create(
            user=other_manager,
            permission=self.perm_reassign,
        )
        with self.assertRaises(PermissionDenied):
            self.change(
                user=other_manager,
                action="reassign",
                employee_id=self.agent_2.pk,
                reason="External manager trying to reassign",
            )

    def test_15_activity_logging_failure(self):
        with patch(
            "apps.tickets.services.assignment.TicketActivityLog.objects.create",
            side_effect=RuntimeError("Audit log write failed"),
        ):
            with self.assertRaises(RuntimeError):
                self.change(
                    action="reassign",
                    employee_id=self.agent_2.pk,
                    reason="Reassign with failed log",
                )
        self.ticket.refresh_from_db()
        self.assertEqual(
            self.ticket.assigned_employee_id,
            self.agent_user.pk,
        )
        self.assert_active_assignment(self.ticket, self.agent_user)

    def test_16_assign_already_assigned_ticket_rejected(self):
        with self.assertRaises(ValidationError):
            self.change(
                action="assign",
                employee_id=self.agent_2.pk,
            )

    def test_17_reassign_to_same_employee_rejected(self):
        with self.assertRaises(ValidationError):
            self.change(
                action="reassign",
                employee_id=self.agent_user.pk,
                reason="Same agent reassignment",
            )

    def test_18_unassign_already_unassigned_rejected(self):
        self.change(action="unassign")
        with self.assertRaises(ValidationError):
            self.change(action="unassign")

    def test_19_transfer_to_same_team_rejected(self):
        with self.assertRaises(PermissionDenied):
            self.change(
                action="transfer",
                team_id=self.team.pk,
                reason="Transfer to same team",
            )

    def test_20_transfer_to_inactive_team_rejected(self):
        self.team_2.is_active = False
        self.team_2.save()
        with self.assertRaises(PermissionDenied):
            self.change(
                action="transfer",
                team_id=self.team_2.pk,
                reason="Transfer to inactive team",
            )

    def test_21_invalid_action_rejected(self):
        with self.assertRaises(PermissionDenied):
            self.change(action="invalid_action")

    def test_22_non_text_reason_rejected(self):
        with self.assertRaises(ValidationError):
            self.change(
                action="reassign",
                employee_id=self.agent_2.pk,
                reason=12345,
            )

    def test_23_permitted_team_member_can_reassign(self):
        UserPermission.objects.get_or_create(
            user=self.agent_user,
            permission=self.perm_reassign,
        )
        self.change(
            user=self.agent_user,
            action="reassign",
            employee_id=self.agent_2.pk,
            reason="Delegating to teammate",
        )
        self.ticket.refresh_from_db()
        self.assertEqual(
            self.ticket.assigned_employee_id,
            self.agent_2.pk,
        )

    def test_24_manager_cannot_transfer_to_unmanaged_team(self):
        User = get_user_model()
        external_manager = User.objects.create_user(
            email="extmgr@example.com",
            password="Password123!",
            account_status=User.AccountStatus.ACTIVE,
            email_verified=True,
            is_active=True,
        )
        unmanaged_team = SupportTeam.objects.create(
            name="Unmanaged Team",
            manager=external_manager,
            is_active=True,
        )
        with self.assertRaises(PermissionDenied):
            self.change(
                action="transfer",
                team_id=unmanaged_team.pk,
                reason="Transferring to team not managed by user",
            )

    def test_26_database_prevents_duplicate_active_assignment(self):
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                TicketAssignment.objects.create(
                    ticket=self.ticket,
                    team=self.team,
                    employee=self.agent_2,
                    assigned_by=self.manager_user,
                    reason="Duplicate active assignment",
                )
        self.assert_active_assignment(
            self.ticket,
            self.agent_user,
        )

    def test_27_transfer_rejects_employee_from_old_team(self):
        with self.assertRaises(PermissionDenied):
            self.change(
                action="transfer",
                team_id=self.team_2.pk,
                employee_id=self.agent_2.pk,
                reason="Transfer with invalid employee",
            )
        self.ticket.refresh_from_db()
        self.assertEqual(self.ticket.assigned_team_id, self.team.pk)
        self.assertEqual(
            self.ticket.assigned_employee_id,
            self.agent_user.pk,
        )

    def test_28_unassign_preserves_ticket_status(self):
        original_status = self.ticket.status_id
        self.change(
            action="unassign",
            reason="Agent unavailable",
        )
        self.ticket.refresh_from_db()
        self.assertEqual(self.ticket.status_id, original_status)
        self.assertIsNone(self.ticket.assigned_employee_id)

    def test_29_resolved_ticket_cannot_transfer(self):
        self.ticket.status = self.status_resolved
        self.ticket.save()
        with self.assertRaises(PermissionDenied):
            self.change(
                action="transfer",
                team_id=self.team_2.pk,
                reason="Attempt after resolution",
            )

    def test_30_done_ticket_cannot_unassign(self):
        self.ticket.status = self.status_done
        self.ticket.save()
        with self.assertRaises(PermissionDenied):
            self.change(
                action="unassign",
                reason="Attempt after closure",
            )


class TicketAssignmentConcurrencyTests(TransactionTestCase):
    def setUp(self):
        from apps.tickets.tests.test_workflow_authorization import WorkflowAuthorizationTests
        WorkflowAuthorizationTests.setUp(self)

        User = get_user_model()
        self.agent_user.email_verified = True
        self.agent_user.save(update_fields=["email_verified"])

        self.agent_2 = User.objects.create_user(
            email=f"concurrent-{uuid.uuid4().hex[:8]}@example.com",
            password="Password123!",
            account_status=User.AccountStatus.ACTIVE,
            email_verified=True,
        )
        TeamMember.objects.create(
            team=self.team,
            user=self.agent_2,
            is_active=True,
        )

        permission, _ = Permission.objects.get_or_create(
            module="tickets",
            action="assign",
            defaults={"name": "Ticket Assign"},
        )
        UserPermission.objects.get_or_create(
            user=self.manager_user,
            permission=permission,
        )

        self.ticket.assigned_employee = None
        self.ticket.status = self.status_new
        self.ticket.save()
        TicketAssignment.objects.filter(ticket=self.ticket).delete()

    def test_25_two_requests_assign_same_ticket(self):
        """
        Two requests attempt assignment simultaneously.
        Exactly one should succeed.
        """
        barrier = threading.Barrier(2)
        successes = []
        failures = []

        def worker(employee_id):
            close_old_connections()
            try:
                barrier.wait(timeout=10)
                change_ticket_assignment(
                    ticket_id=self.ticket.pk,
                    user=self.manager_user,
                    action="assign",
                    employee_id=employee_id,
                )
                successes.append(employee_id)
            except Exception as e:
                failures.append(e)
            finally:
                connection.close()

        t1 = threading.Thread(target=worker, args=(self.agent_user.pk,))
        t2 = threading.Thread(target=worker, args=(self.agent_2.pk,))

        t1.start()
        t2.start()

        t1.join()
        t2.join()

        self.assertEqual(len(successes), 1, f"Expected 1 winner, got {len(successes)}. Failures: {failures}")
        self.assertEqual(len(failures), 1)

        active_count = TicketAssignment.objects.filter(
            ticket=self.ticket,
            unassigned_at__isnull=True,
            employee__isnull=False,
        ).count()
        self.assertEqual(active_count, 1)
