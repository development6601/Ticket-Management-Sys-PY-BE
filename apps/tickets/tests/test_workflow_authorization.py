from django.test import TestCase
from django.utils import timezone
from rest_framework.exceptions import PermissionDenied

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
    TicketCategory,
    TicketStatus,
    TicketStatusTransition,
)
from apps.tickets.services.authorization import authorize_ticket_transition
from apps.tickets.services.workflow import validate_requested_transition


class WorkflowAuthorizationTests(TestCase):
    def setUp(self):
        # 1. Permissions
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

        # 2. Roles
        self.role_agent, _ = Role.objects.get_or_create(
            code="AGENT",
            defaults={"name": "Support Agent"},
        )
        self.role_customer, _ = Role.objects.get_or_create(
            code="CUSTOMER",
            defaults={"name": "Customer Role"},
        )
        self.role_admin, _ = Role.objects.get_or_create(
            code="ADMIN",
            defaults={"name": "Admin", "is_system_role": True},
        )

        # Give AGENT role tickets.start and tickets.resolve
        RolePermission.objects.get_or_create(
            role=self.role_agent,
            permission=self.perm_start,
        )
        RolePermission.objects.get_or_create(
            role=self.role_agent,
            permission=self.perm_resolve,
        )

        # 3. Customer
        self.customer = Customer.objects.create(
            company_name="Acme Corp",
            ticket_key="ACME",
            is_active=True,
            account_status=Customer.AccountStatus.ACTIVE,
        )
        self.category = TicketCategory.objects.create(
            customer=self.customer,
            name="General Support",
            is_active=True,
        )

        # 4. Statuses
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

        # 5. Transitions
        self.trans_new_to_progress = TicketStatusTransition.objects.create(
            from_status=self.status_new,
            to_status=self.status_in_progress,
            required_permission=self.perm_start,
            is_active=True,
        )
        self.trans_progress_to_resolved = TicketStatusTransition.objects.create(
            from_status=self.status_in_progress,
            to_status=self.status_resolved,
            required_permission=self.perm_resolve,
            is_active=True,
        )
        self.trans_new_to_resolved = TicketStatusTransition.objects.create(
            from_status=self.status_new,
            to_status=self.status_resolved,
            required_permission=self.perm_resolve,
            is_active=True,
        )
        self.trans_resolved_to_done = TicketStatusTransition.objects.create(
            from_status=self.status_resolved,
            to_status=self.status_done,
            required_permission=self.perm_close,
            is_active=True,
        )
        self.trans_resolved_to_progress = TicketStatusTransition.objects.create(
            from_status=self.status_resolved,
            to_status=self.status_in_progress,
            required_permission=self.perm_reopen,
            is_active=True,
        )
        self.trans_done_to_progress = TicketStatusTransition.objects.create(
            from_status=self.status_done,
            to_status=self.status_in_progress,
            required_permission=self.perm_reopen,
            is_active=True,
        )

        # 6. Users and Support Team
        self.manager_user = User.objects.create_user(
            email="manager@example.com",
            password="Password123!",
            account_status=User.AccountStatus.ACTIVE,
        )
        self.team = SupportTeam.objects.create(
            name="Tier 1 Team",
            manager=self.manager_user,
            is_active=True,
        )
        # Give manager tickets.resolve
        UserPermission.objects.create(
            user=self.manager_user,
            permission=self.perm_resolve,
        )

        self.agent_user = User.objects.create_user(
            email="agent1@example.com",
            password="Password123!",
            account_status=User.AccountStatus.ACTIVE,
        )
        UserRole.objects.create(user=self.agent_user, role=self.role_agent)
        TeamMember.objects.create(
            team=self.team,
            user=self.agent_user,
            is_active=True,
        )

        self.creator_user = User.objects.create_user(
            email="creator@customer.com",
            password="Password123!",
            account_status=User.AccountStatus.ACTIVE,
        )
        UserRole.objects.create(user=self.creator_user, role=self.role_customer)
        CustomerUser.objects.create(
            customer=self.customer,
            user=self.creator_user,
            is_primary_contact=False,
        )

        # Default ticket in progress assigned to agent_user
        self.ticket = Ticket.objects.create(
            ticket_number="ACME-0001",
            subject="Test Ticket",
            description="Ticket for testing workflow authorization",
            customer=self.customer,
            category=self.category,
            status=self.status_new,
            created_by=self.creator_user,
            assigned_team=self.team,
            assigned_employee=self.agent_user,
        )
        self.ticket.status = self.status_in_progress
        self.ticket.save()

    def test_01_assigned_agent_with_tickets_resolve_allowed(self):
        """Assigned Agent with tickets.resolve -> Allowed"""
        transition, action = validate_requested_transition(
            user=self.agent_user,
            ticket=self.ticket,
            to_status=self.status_resolved,
        )
        self.assertEqual(action, "resolve")
        self.assertEqual(transition.id, self.trans_progress_to_resolved.id)

    def test_02_another_agent_in_same_team_denied_for_assigned_ticket(self):
        """Another Agent in same team -> Denied for assigned ticket"""
        agent_2 = User.objects.create_user(
            email="agent2@example.com",
            password="Password123!",
            account_status=User.AccountStatus.ACTIVE,
        )
        UserRole.objects.create(user=agent_2, role=self.role_agent)
        TeamMember.objects.create(
            team=self.team,
            user=agent_2,
            is_active=True,
        )

        with self.assertRaises(PermissionDenied):
            validate_requested_transition(
                user=agent_2,
                ticket=self.ticket,
                to_status=self.status_resolved,
            )

    def test_03_eligible_agent_unassigned_ticket_authorized(self):
        """Eligible Agent, unassigned ticket -> Authorized to claim and start/resolve"""
        self.ticket.assigned_employee = None
        self.ticket.save()

        # Can resolve
        transition, action = validate_requested_transition(
            user=self.agent_user,
            ticket=self.ticket,
            to_status=self.status_resolved,
        )
        self.assertEqual(action, "resolve")

        # Can start
        ticket_new = Ticket.objects.create(
            ticket_number="ACME-0002",
            subject="New Ticket",
            description="Testing start on unassigned ticket",
            customer=self.customer,
            category=self.category,
            status=self.status_new,
            created_by=self.creator_user,
            assigned_team=self.team,
            assigned_employee=None,
        )
        transition_start, action_start = validate_requested_transition(
            user=self.agent_user,
            ticket=ticket_new,
            to_status=self.status_in_progress,
        )
        self.assertEqual(action_start, "start")

    def test_04_agent_outside_assigned_team_denied(self):
        """Agent outside assigned team -> Denied"""
        other_team = SupportTeam.objects.create(
            name="Tier 2 Team",
            is_active=True,
        )
        outside_agent = User.objects.create_user(
            email="outside@example.com",
            password="Password123!",
            account_status=User.AccountStatus.ACTIVE,
        )
        UserRole.objects.create(user=outside_agent, role=self.role_agent)
        TeamMember.objects.create(
            team=other_team,
            user=outside_agent,
            is_active=True,
        )

        with self.assertRaises(PermissionDenied):
            validate_requested_transition(
                user=outside_agent,
                ticket=self.ticket,
                to_status=self.status_resolved,
            )

    def test_05_team_manager_with_permission_allowed(self):
        """Team Manager with permission -> Allowed"""
        transition, action = validate_requested_transition(
            user=self.manager_user,
            ticket=self.ticket,
            to_status=self.status_resolved,
        )
        self.assertEqual(action, "resolve")

    def test_06_system_admin_allowed(self):
        """System Admin -> Allowed"""
        admin_user = User.objects.create_superuser(
            email="admin@example.com",
            password="Password123!",
            account_status=User.AccountStatus.ACTIVE,
        )
        transition, action = validate_requested_transition(
            user=admin_user,
            ticket=self.ticket,
            to_status=self.status_resolved,
        )
        self.assertEqual(action, "resolve")

    def test_07_customer_member_tries_to_resolve_denied(self):
        """Customer Member tries to resolve -> Denied"""
        # Give customer user direct tickets.resolve
        UserPermission.objects.create(
            user=self.creator_user,
            permission=self.perm_resolve,
        )
        with self.assertRaises(PermissionDenied):
            validate_requested_transition(
                user=self.creator_user,
                ticket=self.ticket,
                to_status=self.status_resolved,
            )

    def test_08_customer_creator_with_close_permission_allowed(self):
        """Customer creator with close permission -> Allowed"""
        # Ticket is in Resolved status
        self.ticket.status = self.status_resolved
        self.ticket.save()

        UserPermission.objects.create(
            user=self.creator_user,
            permission=self.perm_close,
        )
        transition, action = validate_requested_transition(
            user=self.creator_user,
            ticket=self.ticket,
            to_status=self.status_done,
        )
        self.assertEqual(action, "close")

    def test_09_customer_creator_without_close_permission_denied(self):
        """Customer creator without close permission -> Denied"""
        self.ticket.status = self.status_resolved
        self.ticket.save()

        # self.creator_user does NOT have tickets.close
        with self.assertRaises(PermissionDenied):
            validate_requested_transition(
                user=self.creator_user,
                ticket=self.ticket,
                to_status=self.status_done,
            )

    def test_10_primary_contact_with_effective_close_permission_allowed(self):
        """Primary contact with effective close permission -> Allowed"""
        primary_user = User.objects.create_user(
            email="primary@customer.com",
            password="Password123!",
            account_status=User.AccountStatus.ACTIVE,
        )
        UserRole.objects.create(user=primary_user, role=self.role_customer)
        CustomerUser.objects.create(
            customer=self.customer,
            user=primary_user,
            is_primary_contact=True,
        )
        # Give CUSTOMER role tickets.close permission
        RolePermission.objects.get_or_create(
            role=self.role_customer,
            permission=self.perm_close,
        )

        self.ticket.status = self.status_resolved
        self.ticket.save()

        transition, action = validate_requested_transition(
            user=primary_user,
            ticket=self.ticket,
            to_status=self.status_done,
        )
        self.assertEqual(action, "close")

    def test_11_customer_member_with_direct_close_permission_allowed(self):
        """Customer Member with direct close permission -> Allowed, subject to visibility"""
        other_member = User.objects.create_user(
            email="member2@customer.com",
            password="Password123!",
            account_status=User.AccountStatus.ACTIVE,
        )
        CustomerUser.objects.create(
            customer=self.customer,
            user=other_member,
            is_primary_contact=False,
        )
        # Grant direct tickets.close permission
        UserPermission.objects.create(
            user=other_member,
            permission=self.perm_close,
        )

        self.ticket.status = self.status_resolved
        self.ticket.save()

        transition, action = validate_requested_transition(
            user=other_member,
            ticket=self.ticket,
            to_status=self.status_done,
        )
        self.assertEqual(action, "close")

    def test_12_customer_from_another_company_denied(self):
        """Customer from another company -> Denied"""
        other_customer = Customer.objects.create(
            company_name="Globex Corp",
            ticket_key="GLBX",
            is_active=True,
            account_status=Customer.AccountStatus.ACTIVE,
        )
        other_customer_user = User.objects.create_user(
            email="user@globex.com",
            password="Password123!",
            account_status=User.AccountStatus.ACTIVE,
        )
        CustomerUser.objects.create(
            customer=other_customer,
            user=other_customer_user,
            is_primary_contact=True,
        )
        UserPermission.objects.create(
            user=other_customer_user,
            permission=self.perm_close,
        )

        self.ticket.status = self.status_resolved
        self.ticket.save()

        with self.assertRaises(PermissionDenied):
            validate_requested_transition(
                user=other_customer_user,
                ticket=self.ticket,
                to_status=self.status_done,
            )

    def test_13_inactive_user_denied(self):
        """Inactive user -> Denied"""
        self.agent_user.is_active = False
        self.agent_user.save()

        with self.assertRaises(PermissionDenied):
            validate_requested_transition(
                user=self.agent_user,
                ticket=self.ticket,
                to_status=self.status_resolved,
            )

    def test_14_inactive_customer_denied(self):
        """Inactive customer -> Denied"""
        UserPermission.objects.create(
            user=self.creator_user,
            permission=self.perm_close,
        )
        self.ticket.status = self.status_resolved
        self.ticket.save()

        self.customer.is_active = False
        self.customer.save()

        with self.assertRaises(PermissionDenied):
            validate_requested_transition(
                user=self.creator_user,
                ticket=self.ticket,
                to_status=self.status_done,
            )

    def test_15_permission_exists_but_not_assigned_denied(self):
        """Permission exists but not assigned -> Denied"""
        unauthorized_agent = User.objects.create_user(
            email="unauth_agent@example.com",
            password="Password123!",
            account_status=User.AccountStatus.ACTIVE,
        )
        # In team, but has no role or permissions assigned
        TeamMember.objects.create(
            team=self.team,
            user=unauthorized_agent,
            is_active=True,
        )
        self.ticket.assigned_employee = unauthorized_agent
        self.ticket.save()

        with self.assertRaises(PermissionDenied):
            validate_requested_transition(
                user=unauthorized_agent,
                ticket=self.ticket,
                to_status=self.status_resolved,
            )

    def test_16_resolved_to_done_using_wrong_permission_denied(self):
        """Resolved -> Done using wrong permission -> Denied"""
        self.ticket.status = self.status_resolved
        self.ticket.save()

        bad_transition = TicketStatusTransition(
            from_status=self.status_resolved,
            to_status=self.status_done,
            required_permission=self.perm_resolve,  # Wrong! Must be tickets.close
            is_active=True,
        )
        admin_user = User.objects.create_superuser(
            email="admin_wrong_perm@example.com",
            password="Password123!",
            account_status=User.AccountStatus.ACTIVE,
        )

        with self.assertRaises(PermissionDenied) as ctx:
            authorize_ticket_transition(
                user=admin_user,
                ticket=self.ticket,
                transition=bad_transition,
            )
        self.assertIn("This action requires tickets.close", str(ctx.exception))

    def test_17_done_to_in_progress_using_wrong_permission_denied(self):
        """Done -> In Progress using wrong permission -> Denied"""
        self.ticket.status = self.status_done
        self.ticket.save()

        bad_transition = TicketStatusTransition(
            from_status=self.status_done,
            to_status=self.status_in_progress,
            required_permission=self.perm_resolve,  # Wrong! Must be tickets.reopen
            is_active=True,
        )
        admin_user = User.objects.create_superuser(
            email="admin_wrong_perm2@example.com",
            password="Password123!",
            account_status=User.AccountStatus.ACTIVE,
        )

        with self.assertRaises(PermissionDenied) as ctx:
            authorize_ticket_transition(
                user=admin_user,
                ticket=self.ticket,
                transition=bad_transition,
            )
        self.assertIn("This action requires tickets.reopen", str(ctx.exception))
