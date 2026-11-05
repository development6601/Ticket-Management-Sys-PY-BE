import threading
import uuid
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.db import connections, transaction
from django.test import TransactionTestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from rest_framework.exceptions import NotFound, PermissionDenied, ValidationError
from rest_framework.test import APIClient

from apps.accounts.models import Permission, Role, RolePermission, UserRole
from apps.customers.models import Customer, CustomerUser
from apps.customers.services.customers import CustomerCreationService
from apps.customers.services.customer_updates import CustomerUpdateService
from apps.tickets.models import Ticket, TicketCategory, TicketStatus
from apps.tickets.services.status import DEFAULT_TICKET_STATUSES, seed_customer_statuses
from apps.tickets.services.ticket_creation import TicketCreationService, allocate_ticket_number

User = get_user_model()
PW = "Password123!"


def _customer_url(customer):
    return reverse("customer-detail", kwargs={"id": customer.pk})


@override_settings(PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"])
class TicketAndCustomerKeyTests(TransactionTestCase):

    def setUp(self):
        super().setUp()
        self.admin_role, _ = Role.objects.get_or_create(
            code="ADMIN", defaults={"name": "Admin", "is_system_role": True, "is_assignable": False}
        )
        self.customer_role, _ = Role.objects.get_or_create(
            code="CUSTOMER", defaults={"name": "Customer", "is_system_role": True, "is_assignable": True}
        )
        self.p_update, _ = Permission.objects.get_or_create(
            name="customers.update", defaults={"module": "customers", "action": "update"}
        )
        self.p_view, _ = Permission.objects.get_or_create(
            name="customers.view", defaults={"module": "customers", "action": "view"}
        )
        RolePermission.objects.get_or_create(role=self.customer_role, permission=self.p_update)
        RolePermission.objects.get_or_create(role=self.customer_role, permission=self.p_view)

        # Admin user
        self.admin = User.objects.create_superuser(
            email="sysadmin@example.com",
            password=PW,
            first_name="Sys",
            last_name="Admin",
            email_verified=True,
        )

    def _auth_client(self, user):
        client = APIClient()
        client.force_authenticate(user=user)
        return client

    def _create_company(self, name, email_prefix, key=None):
        user = User.objects.create_user(
            email=f"{email_prefix}@example.com",
            password=PW,
            first_name="Contact",
            last_name=name[:10],
            phone="+12025550100",
            email_verified=True,
        )
        UserRole.objects.create(user=user, role=self.customer_role)
        kwargs = {
            "company_name": name,
            "contact_person": f"Contact {name[:10]}",
            "email": f"{email_prefix}@example.com",
            "phone": "+12025550100",
            "address": "123 Test Street",
            "state": "California",
            "country": "United States",
            "primary_user": user,
            "is_active": True,
            "account_status": Customer.AccountStatus.ACTIVE,
        }
        if key is not None:
            kwargs["ticket_key"] = key
        company = CustomerCreationService.create_customer(**kwargs)
        return company, user

    # 1. Secondary Customer Admin updates only ticket_key before tickets -> 200
    def test_01_secondary_customer_admin_updates_only_ticket_key_before_tickets(self):
        company, _ = self._create_company("Primary Co", "sec1_p")
        sec_user = User.objects.create_user(
            email="sec_admin1@example.com",
            password=PW,
            first_name="Sec",
            last_name="Admin",
            phone="+12025550111",
            email_verified=True,
        )
        UserRole.objects.create(user=sec_user, role=self.customer_role)
        # Note: No direct UserPermission assigned!
        CustomerUser.objects.create(
            customer=company,
            user=sec_user,
            is_primary_contact=False,
            role=CustomerUser.Role.ADMIN,
        )

        client = self._auth_client(sec_user)
        resp = client.patch(_customer_url(company), {"ticket_key": "SECKEY"}, format="json")
        self.assertEqual(resp.status_code, 200, resp.content)
        company.refresh_from_db()
        self.assertEqual(company.ticket_key, "SECKEY")

    # 2. Secondary Customer Admin updates contact_person -> 403 (standardized envelope)
    def test_02_secondary_customer_admin_updates_contact_person_403(self):
        company, _ = self._create_company("Primary Co 2", "sec2_p")
        sec_user = User.objects.create_user(
            email="sec_admin2@example.com",
            password=PW,
            first_name="Sec2",
            last_name="Admin2",
            phone="+12025550112",
            email_verified=True,
        )
        UserRole.objects.create(user=sec_user, role=self.customer_role)
        CustomerUser.objects.create(
            customer=company,
            user=sec_user,
            is_primary_contact=False,
            role=CustomerUser.Role.ADMIN,
        )

        client = self._auth_client(sec_user)
        resp = client.patch(
            _customer_url(company),
            {"contact_person": "Hacked Name"},
            format="json",
        )
        self.assertEqual(resp.status_code, 403, resp.content)
        data = resp.json()
        self.assertFalse(data["success"])
        self.assertEqual(data["message"], "Customer update failed.")
        self.assertIn("detail", data["data"])

    # 3. Secondary Customer Admin sends ticket_key and contact_person together -> 403
    def test_03_secondary_customer_admin_sends_ticket_key_and_contact_person_together_403(self):
        company, _ = self._create_company("Primary Co 3", "sec3_p")
        sec_user = User.objects.create_user(
            email="sec_admin3@example.com",
            password=PW,
            first_name="Sec3",
            last_name="Admin3",
            phone="+12025550113",
            email_verified=True,
        )
        UserRole.objects.create(user=sec_user, role=self.customer_role)
        CustomerUser.objects.create(
            customer=company,
            user=sec_user,
            is_primary_contact=False,
            role=CustomerUser.Role.ADMIN,
        )

        client = self._auth_client(sec_user)
        resp = client.patch(
            _customer_url(company),
            {"ticket_key": "NEWKEY", "contact_person": "Hacked Name"},
            format="json",
        )
        self.assertEqual(resp.status_code, 403, resp.content)
        data = resp.json()
        self.assertFalse(data["success"])
        self.assertEqual(data["message"], "Customer update failed.")
        self.assertIn("detail", data["data"])

    # 4. Customer Member updates ticket_key -> 403 (rejected by service)
    def test_04_customer_member_updates_ticket_key_403(self):
        company, _ = self._create_company("Member Co", "memco", key="MEMCO")
        member_user = User.objects.create_user(
            email="mem_user@example.com",
            password=PW,
            first_name="Member",
            last_name="User",
            phone="+12025550114",
            email_verified=True,
        )
        UserRole.objects.create(user=member_user, role=self.customer_role)
        CustomerUser.objects.create(
            customer=company,
            user=member_user,
            is_primary_contact=False,
            role=CustomerUser.Role.MEMBER,
        )

        client = self._auth_client(member_user)
        resp = client.patch(_customer_url(company), {"ticket_key": "MEMKEY"}, format="json")
        self.assertEqual(resp.status_code, 403, resp.content)
        data = resp.json()
        self.assertFalse(data["success"])
        self.assertEqual(data["message"], "Customer update failed.")
        self.assertIn("detail", data["data"])

    # 5. Secondary Admin updates another company's key -> 403
    def test_05_secondary_admin_updates_another_company_key_403(self):
        c1, _ = self._create_company("Company 1", "co1", key="COONE")
        c2, _ = self._create_company("Company 2", "co2", key="COTWO")

        sec_user1 = User.objects.create_user(
            email="sec_co1@example.com",
            password=PW,
            first_name="Sec",
            last_name="Co1",
            phone="+12025550115",
            email_verified=True,
        )
        UserRole.objects.create(user=sec_user1, role=self.customer_role)
        CustomerUser.objects.create(
            customer=c1,
            user=sec_user1,
            is_primary_contact=False,
            role=CustomerUser.Role.ADMIN,
        )

        client = self._auth_client(sec_user1)
        resp = client.patch(_customer_url(c2), {"ticket_key": "HACKED"}, format="json")
        self.assertEqual(resp.status_code, 403, resp.content)
        data = resp.json()
        self.assertFalse(data["success"])
        self.assertEqual(data["message"], "Customer update failed.")

    # 6. Admin updates key after first ticket -> 400
    def test_06_admin_updates_key_after_first_ticket_400(self):
        company, owner = self._create_company("Locked Co", "lock1", key="LOCKED")
        category = company.ticket_categories.first()
        status = company.ticket_statuses.filter(is_initial=True).first()
        TicketCreationService.create_ticket(
            customer_id=company.pk,
            performed_by=owner,
            subject="First ticket",
            description="Testing ticket creation",
            category=category,
            initial_status=status,
        )

        client = self._auth_client(owner)
        resp = client.patch(_customer_url(company), {"ticket_key": "CHANGED"}, format="json")
        self.assertEqual(resp.status_code, 400, resp.content)
        data = resp.json()
        self.assertFalse(data["success"])
        self.assertEqual(data["message"], "Customer update failed.")
        self.assertIn("ticket_key", data["data"])

    # 7. Duplicate key causes PostgreSQL constraint violation -> 400, not 500
    def test_07_duplicate_key_causes_postgresql_constraint_violation_400(self):
        c1, _ = self._create_company("Company A", "co_a", key="SHARED")
        c2, owner2 = self._create_company("Company B", "co_b", key="OTHER")

        client = self._auth_client(owner2)
        resp = client.patch(_customer_url(c2), {"ticket_key": "SHARED"}, format="json")
        self.assertEqual(resp.status_code, 400, resp.content)
        data = resp.json()
        self.assertFalse(data["success"])
        self.assertEqual(data["message"], "Customer update failed.")
        self.assertIn("ticket_key", data["data"])

    # 8. Outer transaction continues after handled duplicate key -> No TransactionManagementError
    def test_08_outer_transaction_continues_after_handled_duplicate_key(self):
        c1, _ = self._create_company("Company X", "co_x", key="EXISTX")
        c2, owner2 = self._create_company("Company Y", "co_y", key="EXISTY")

        with transaction.atomic():
            with self.assertRaises(ValidationError) as ctx:
                CustomerUpdateService.update_customer(
                    customer_id=c2.pk,
                    validated_data={"ticket_key": "EXISTX"},
                    performed_by=owner2,
                )
            self.assertIn("ticket_key", ctx.exception.detail)

            # Enclosing transaction must remain fully functional
            check = Customer.objects.filter(pk=c2.pk).exists()
            self.assertTrue(check)

    # 9. Permission denied by service -> Standardized 403 envelope
    def test_09_permission_denied_by_service_standardized_envelope(self):
        company, _ = self._create_company("Service Denied Co", "sdenied", key="SDENY")
        member_user = User.objects.create_user(
            email="member_sdeny@example.com",
            password=PW,
            first_name="Member",
            last_name="SDeny",
            phone="+12025550116",
            email_verified=True,
        )
        UserRole.objects.create(user=member_user, role=self.customer_role)
        CustomerUser.objects.create(
            customer=company,
            user=member_user,
            is_primary_contact=False,
            role=CustomerUser.Role.MEMBER,
        )

        client = self._auth_client(member_user)
        # Service rejects member with PermissionDenied
        resp = client.patch(_customer_url(company), {"ticket_key": "NEWKEY"}, format="json")
        self.assertEqual(resp.status_code, 403, resp.content)
        self.assertEqual(
            resp.json(),
            {
                "success": False,
                "message": "Customer update failed.",
                "data": {
                    "detail": "Only a customer administrator or an internal administrator can change the ticket key."
                },
            },
        )

    # 10. Permission denied by DRF before view execution -> Standardized 403 envelope
    def test_10_permission_denied_by_drf_before_view_execution_standardized_envelope(self):
        company, _ = self._create_company("DRF Denied Co", "drfdenied", key="DRFDENY")
        member_user = User.objects.create_user(
            email="member_drfdeny@example.com",
            password=PW,
            first_name="Member",
            last_name="DRFDeny",
            phone="+12025550117",
            email_verified=True,
        )
        UserRole.objects.create(user=member_user, role=self.customer_role)
        CustomerUser.objects.create(
            customer=company,
            user=member_user,
            is_primary_contact=False,
            role=CustomerUser.Role.MEMBER,
        )

        client = self._auth_client(member_user)
        # Non-ticket-key request is intercepted by HasPermission before patch() executes
        resp = client.patch(_customer_url(company), {"contact_person": "Jane Doe"}, format="json")
        self.assertEqual(resp.status_code, 403, resp.content)
        data = resp.json()
        self.assertFalse(data["success"])
        self.assertEqual(data["message"], "Customer update failed.")
        self.assertIn("detail", data["data"])


@override_settings(PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"])
class TicketStatusSeedingTests(TransactionTestCase):

    def setUp(self):
        super().setUp()
        self.customer = Customer.objects.create(
            company_name="Acme Corp",
            ticket_key="ACME",
            ticket_counter=0,
            contact_person="John Doe",
            email="contact@acme.example",
            phone="+12025550100",
            address="123 Road",
            state="California",
            country="United States",
            is_active=True,
            account_status=Customer.AccountStatus.ACTIVE,
        )

    # 1. Seed a new company -> exactly four statuses, ordering, and flags
    def test_seed_new_company_statuses_and_flags(self):
        seed_customer_statuses(self.customer)

        statuses = list(
            TicketStatus.objects
            .filter(customer=self.customer)
            .order_by("position", "id")
        )
        self.assertEqual(len(statuses), 4)

        names = [s.name for s in statuses]
        self.assertEqual(names, ["New", "In Progress", "Resolved", "Done"])

        positions = [s.position for s in statuses]
        self.assertEqual(positions, [1, 2, 3, 4])

        initial_statuses = [s.name for s in statuses if s.is_initial]
        self.assertEqual(initial_statuses, ["New"])

        resolved_statuses = [s.name for s in statuses if s.is_resolved]
        self.assertEqual(resolved_statuses, ["Resolved"])

        closed_statuses = [s.name for s in statuses if s.is_closed]
        self.assertEqual(closed_statuses, ["Done"])

        for s in statuses:
            self.assertTrue(s.is_system)
            self.assertTrue(s.is_active)
            self.assertIsNone(s.created_by)

    # 2. Seed same company twice -> still four statuses
    def test_seed_same_company_twice(self):
        seed_customer_statuses(self.customer)
        self.assertEqual(self.customer.ticket_statuses.count(), 4)

        # Second invocation
        seed_customer_statuses(self.customer)
        self.assertEqual(self.customer.ticket_statuses.count(), 4)

    # 3. Seed two companies -> independent statuses
    def test_seed_two_companies_have_independent_statuses(self):
        customer2 = Customer.objects.create(
            company_name="Beta Corp",
            ticket_key="BETA",
            ticket_counter=0,
            contact_person="Jane Doe",
            email="contact@beta.example",
            phone="+12025550101",
            address="456 Avenue",
            state="California",
            country="United States",
            is_active=True,
            account_status=Customer.AccountStatus.ACTIVE,
        )

        seed_customer_statuses(self.customer)
        seed_customer_statuses(customer2)

        self.assertEqual(self.customer.ticket_statuses.count(), 4)
        self.assertEqual(customer2.ticket_statuses.count(), 4)

        customer1_ids = set(self.customer.ticket_statuses.values_list("id", flat=True))
        customer2_ids = set(customer2.ticket_statuses.values_list("id", flat=True))
        self.assertTrue(customer1_ids.isdisjoint(customer2_ids))

    # 4. Rename In Progress, seed again -> custom name preserved
    def test_rename_in_progress_and_seed_again(self):
        seed_customer_statuses(self.customer)
        TicketStatus.objects.filter(customer=self.customer, name="In Progress").update(name="Working")

        # Seed again
        seed_customer_statuses(self.customer)

        names = list(
            TicketStatus.objects
            .filter(customer=self.customer)
            .order_by("position", "id")
            .values_list("name", flat=True)
        )
        self.assertEqual(names, ["New", "Working", "Resolved", "Done"])
        self.assertEqual(self.customer.ticket_statuses.count(), 4)

    # 5. Existing incomplete workflow -> ValidationError, no automatic repair
    def test_existing_incomplete_workflow_raises_validation_error(self):
        # Create incomplete workflow (missing is_resolved and is_closed)
        TicketStatus.objects.create(
            customer=self.customer,
            name="New",
            position=1,
            is_system=True,
            is_active=True,
            is_initial=True,
            is_resolved=False,
            is_closed=False,
        )

        with self.assertRaises(ValidationError) as ctx:
            seed_customer_statuses(self.customer)

        self.assertIn("statuses", ctx.exception.detail)
        error_msg = str(ctx.exception.detail["statuses"])
        self.assertIn("Customer already has statuses but no active", error_msg)
        self.assertIn("Review the existing workflow.", error_msg)

        # Verify no automatic repair was performed
        self.assertEqual(self.customer.ticket_statuses.count(), 1)

    # 6. Failure during seeding -> no partial statuses committed
    def test_failure_during_seeding_rolls_back(self):
        original_create = TicketStatus.objects.create
        call_count = 0

        def failing_create(**kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 3:
                raise RuntimeError("Simulated failure creating status")
            return original_create(**kwargs)

        with patch.object(TicketStatus.objects, "create", side_effect=failing_create):
            with self.assertRaises(RuntimeError):
                seed_customer_statuses(self.customer)

        self.assertEqual(self.customer.ticket_statuses.count(), 0)

    # 7. Create company through existing service -> categories and statuses both created
    def test_create_company_creates_categories_and_statuses(self):
        new_company = CustomerCreationService.create_customer(
            company_name="Delta Services",
            contact_person="Dave Contact",
            email="contact@delta.example",
            phone="+12025550199",
            address="789 Blvd",
            state="California",
            country="United States",
            is_active=True,
            account_status=Customer.AccountStatus.ACTIVE,
        )

        self.assertGreater(new_company.ticket_categories.count(), 0)
        self.assertEqual(new_company.ticket_statuses.count(), 4)

        names = list(
            new_company.ticket_statuses.order_by("position", "id").values_list("name", flat=True)
        )
        self.assertEqual(names, ["New", "In Progress", "Resolved", "Done"])
