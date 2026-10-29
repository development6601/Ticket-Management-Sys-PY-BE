"""
Automated tests for Account Status workflows: User and Company
activation/deactivation, primary contact transfer, permission matrix, and atomicity.

Run:
    python manage.py test apps.accounts.tests.test_account_status_workflow -v 2
"""

import hashlib
from datetime import timedelta
from unittest import mock

from django.contrib.auth import get_user_model
from django.db import IntegrityError
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APITestCase

from apps.accounts.models import (
    EmailVerificationRequest,
    PasswordResetRequest,
    Permission,
    Role,
    RolePermission,
    UserRole,
)
from apps.customers.models import Customer, CustomerUser
from apps.notifications.models import EmailJob

User = get_user_model()

PW = "Zq7!Lm2#Vx9pKd"
PW2 = "Rt4$Nb8@Hw3jQf"


# =====================================================================
# Base class for Account Status Workflow tests
# =====================================================================
@override_settings(PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"])
class BaseStatusWorkflowTest(APITestCase):
    @classmethod
    def setUpTestData(cls):
        # ---- URLs -------------------------------------------------
        cls.login_url = reverse("auth-login")
        cls.verify_url = reverse("auth-verify-email")
        cls.me_url = reverse("auth-me")
        cls.users_url = reverse("user-list")

        # ---- Roles ------------------------------------------------
        cls.admin_role = Role.objects.create(
            code="ADMIN", name="Admin", is_system_role=True, is_assignable=False
        )
        cls.customer_role = Role.objects.create(
            code="CUSTOMER", name="Customer", is_system_role=True, is_assignable=True
        )
        cls.agent_role = Role.objects.create(
            code="SUPPORT_AGENT", name="Support Agent", is_system_role=True, is_assignable=True
        )
        cls.manager_role = Role.objects.create(
            code="TEAM_MANAGER", name="Team Manager", is_system_role=True, is_assignable=True
        )
        cls.delegate_role = Role.objects.create(
            code="CUSTOMER_MANAGER", name="Customer Manager", is_system_role=True, is_assignable=True
        )

        # ---- Permissions ------------------------------------------
        def perm(name):
            module, action = name.split(".")
            return Permission.objects.create(name=name, module=module, action=action)

        cls.p_create_internal = perm("users.create_internal")
        cls.p_create_customer = perm("users.create_customer")
        cls.p_view = perm("users.view")
        cls.p_update = perm("users.update")
        cls.p_activate = perm("users.activate")
        cls.p_deactivate = perm("users.deactivate")
        cls.p_customers_view = perm("customers.view")

        def grant(role, *perms):
            for p in perms:
                RolePermission.objects.create(role=role, permission=p)

        grant(
            cls.customer_role,
            cls.p_create_customer,
            cls.p_view,
            cls.p_update,
            cls.p_customers_view,
        )
        grant(cls.delegate_role, cls.p_view, cls.p_update)

        # ---- Users & Companies ------------------------------------
        cls.admin = cls._make_user("admin@example.com", "System", "Admin", cls.admin_role)

        # Company ALPHA: owner (primary), member (plain), delegate (allowed)
        cls.alpha = cls._make_company("Alpha Technologies", "+919876543210", "alpha.owner@example.com")
        cls.owner = cls._make_user(
            "alpha.owner@example.com", "Alpha", "Owner", cls.customer_role, company=cls.alpha, primary=True
        )
        cls.member = cls._make_user(
            "alpha.member@example.com", "Alpha", "Member", cls.customer_role, company=cls.alpha
        )
        cls.delegate = cls._make_user(
            "alpha.delegate@example.com", "Alpha", "Delegate", cls.delegate_role, company=cls.alpha
        )

        # Company BETA
        cls.beta = cls._make_company("Beta Industries", "+919876543211", "beta.owner@example.com")
        cls.beta_owner = cls._make_user(
            "beta.owner@example.com", "Beta", "Owner", cls.customer_role, company=cls.beta, primary=True
        )
        cls.beta_member = cls._make_user(
            "beta.member@example.com", "Beta", "Member", cls.customer_role, company=cls.beta
        )

        # Internal employees
        cls.mgr = cls._make_user("manager@example.com", "Mina", "Manager", cls.manager_role)
        cls.agent = cls._make_user("agent@example.com", "Aman", "Agent", cls.agent_role)

    @classmethod
    def _make_user(cls, email, first, last, role, company=None, primary=False):
        user = User.objects.create_user(
            email=email,
            password=PW,
            first_name=first,
            last_name=last,
            email_verified=True,
            is_active=True,
        )
        UserRole.objects.create(user=user, role=role)
        if company:
            CustomerUser.objects.create(customer=company, user=user, is_primary_contact=primary)
        return user

    @classmethod
    def _make_company(cls, name, phone, email):
        return Customer.objects.create(
            company_name=name,
            contact_person="Contact",
            email=email,
            phone=phone,
            address="Street 1",
            state="Gujarat",
            country="India",
            is_active=True,
        )

    def setUp(self):
        self.client.raise_request_exception = False

    def as_user(self, user):
        self.client.credentials()
        self.client.force_authenticate(user=user)

    def anonymous(self):
        self.client.credentials()
        self.client.force_authenticate(user=None)

    def login(self, email, password):
        self.anonymous()
        return self.client.post(self.login_url, {"email": email, "password": password}, format="json")

    def jwt_as(self, email, password):
        r = self.login(email, password)
        self.assertEqual(r.status_code, 200, r.content)
        access = r.json()["data"]["access"]
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {access}")
        return r.json()["data"]

    def user_status(self, user, active, actor=None):
        self.as_user(actor or self.admin)
        return self.client.patch(
            reverse("user-status", kwargs={"id": user.pk}),
            {"is_active": active},
            format="json",
        )

    def company_status(self, company, active, actor=None):
        self.as_user(actor or self.admin)
        return self.client.patch(
            reverse("customer-status", kwargs={"id": company.pk}),
            {"is_active": active},
            format="json",
        )

    def transfer_primary(self, company, new_user, actor=None):
        self.as_user(actor or self.admin)
        return self.client.patch(
            reverse("customer-detail", kwargs={"id": company.pk}),
            {"primary_contact_user_id": str(new_user.pk)},
            format="json",
        )

    def assert_user_state(self, user, active, state):
        user.refresh_from_db()
        self.assertIs(user.is_active, active)
        self.assertEqual(user.account_status, state)

    def assert_company_state(self, company, active, state):
        company.refresh_from_db()
        self.assertIs(company.is_active, active)
        self.assertEqual(company.account_status, state)

    def job(self, user, kind):
        qs = EmailJob.objects.filter(user=user)
        if kind == "verify":
            qs = qs.filter(template="email_verification")
        elif kind == "setup":
            qs = qs.filter(template__icontains="password").exclude(template="password_reset")
        elif kind == "reset":
            qs = qs.filter(template="password_reset")
        return qs.order_by("-created_at").first()

    def verify_token(self, user):
        j = self.job(user, "verify")
        self.assertIsNotNone(j, f"No email-verification email queued for {user.email}.")
        return j.payload["token"]

    def do_verify(self, token):
        self.anonymous()
        return self.client.get(self.verify_url, {"token": token})


# =====================================================================
# 1. Internal User Status Tests
# =====================================================================
class InternalUserStatusTests(BaseStatusWorkflowTest):
    def test_internal_agent_deactivate_and_immediate_reactivate(self):
        self.assertEqual(self.user_status(self.agent, False).status_code, 200)
        self.assert_user_state(self.agent, False, User.AccountStatus.DEACTIVATED)
        self.assertEqual(self.login(self.agent.email, PW).status_code, 401)

        r = self.user_status(self.agent, True)
        self.assertEqual(r.status_code, 200, r.content)
        self.assert_user_state(self.agent, True, User.AccountStatus.ACTIVE)
        self.assertEqual(self.login(self.agent.email, PW).status_code, 200)
        self.assertFalse(EmailVerificationRequest.objects.filter(user=self.agent, used_at__isnull=True).exists())

    def test_internal_manager_deactivate_and_reactivate(self):
        self.assertEqual(self.user_status(self.mgr, False).status_code, 200)
        self.assert_user_state(self.mgr, False, User.AccountStatus.DEACTIVATED)
        self.assertEqual(self.user_status(self.mgr, True).status_code, 200)
        self.assert_user_state(self.mgr, True, User.AccountStatus.ACTIVE)

    def test_internal_self_deactivation_rejected(self):
        r = self.user_status(self.admin, False, self.admin)
        self.assertEqual(r.status_code, 400, r.content)
        self.assert_user_state(self.admin, True, User.AccountStatus.ACTIVE)

    def test_last_active_admin_cannot_be_deactivated(self):
        # A second admin exists to deactivate first admin
        second = self._make_user("second.admin@example.com", "Second", "Admin", self.admin_role)
        r = self.user_status(self.admin, False, second)
        self.assertEqual(r.status_code, 200, r.content)
        self.assert_user_state(self.admin, False, User.AccountStatus.DEACTIVATED)

        # Remaining active admin cannot be deactivated by another user (if any tried)
        # Attempting self-deactivation fails
        r = self.user_status(second, False, second)
        self.assertEqual(r.status_code, 400, r.content)
        self.assert_user_state(second, True, User.AccountStatus.ACTIVE)

    def test_deactivated_internal_user_jwt_rejected(self):
        # Agent logs in with real JWT
        data = self.jwt_as(self.agent.email, PW)
        access = data["access"]
        me_r = self.client.get(self.me_url)
        self.assertEqual(me_r.status_code, 200)

        # Admin deactivates agent
        self.user_status(self.agent, False)

        # The existing JWT must now be rejected
        self.anonymous()
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {access}")
        r = self.client.get(self.me_url)
        self.assertEqual(r.status_code, 401, r.content)

    def test_cannot_deactivate_already_deactivated_user(self):
        self.assertEqual(self.user_status(self.agent, False).status_code, 200)
        r = self.user_status(self.agent, False)
        self.assertEqual(r.status_code, 400, r.content)

    def test_cannot_reactivate_already_active_user(self):
        r = self.user_status(self.agent, True)
        self.assertEqual(r.status_code, 400, r.content)


# =====================================================================
# 2. Customer User Status Tests
# =====================================================================
class CustomerUserStatusTests(BaseStatusWorkflowTest):
    def test_customer_deactivation_is_immediate(self):
        r = self.user_status(self.member, False)
        self.assertEqual(r.status_code, 200, r.content)
        self.assert_user_state(self.member, False, User.AccountStatus.DEACTIVATED)
        self.assertEqual(self.login(self.member.email, PW).status_code, 401)

    def test_deactivated_customer_user_jwt_rejected(self):
        data = self.jwt_as(self.member.email, PW)
        access = data["access"]
        self.assertEqual(self.client.get(self.me_url).status_code, 200)

        self.user_status(self.member, False)

        self.anonymous()
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {access}")
        r = self.client.get(self.me_url)
        self.assertEqual(r.status_code, 401, r.content)

    def test_customer_reactivation_waits_for_email_confirmation(self):
        self.assertEqual(self.user_status(self.member, False).status_code, 200)
        r = self.user_status(self.member, True)
        self.assertEqual(r.status_code, 202, r.content)
        self.assert_user_state(self.member, False, User.AccountStatus.DEACTIVATED)
        self.assertEqual(self.login(self.member.email, PW).status_code, 401)

        token = self.verify_token(self.member)
        req = EmailVerificationRequest.objects.get(
            token_hash=hashlib.sha256(token.encode()).hexdigest()
        )
        self.assertEqual(req.purpose, EmailVerificationRequest.Purpose.USER_REACTIVATION)
        self.assertNotEqual(req.token_hash, token)

        # Confirm token
        self.assertEqual(self.do_verify(token).status_code, 200)
        self.assert_user_state(self.member, True, User.AccountStatus.ACTIVE)
        self.assertEqual(self.login(self.member.email, PW).status_code, 200)

        # Reused token is rejected
        self.assertEqual(self.do_verify(token).status_code, 400)

    def test_customer_reactivation_new_request_invalidates_old_link(self):
        self.assertEqual(self.user_status(self.member, False).status_code, 200)
        self.assertEqual(self.user_status(self.member, True).status_code, 202)
        old_token = self.verify_token(self.member)

        self.assertEqual(self.user_status(self.member, True).status_code, 202)
        new_token = self.verify_token(self.member)
        self.assertNotEqual(old_token, new_token)

        self.assertEqual(self.do_verify(old_token).status_code, 400)
        self.assert_user_state(self.member, False, User.AccountStatus.DEACTIVATED)
        self.assertEqual(self.do_verify(new_token).status_code, 200)

    def test_expired_customer_reactivation_token_rejected(self):
        self.assertEqual(self.user_status(self.member, False).status_code, 200)
        self.assertEqual(self.user_status(self.member, True).status_code, 202)
        token = self.verify_token(self.member)

        EmailVerificationRequest.objects.filter(
            token_hash=hashlib.sha256(token.encode()).hexdigest()
        ).update(expires_at=timezone.now() - timedelta(seconds=1))

        self.assertEqual(self.do_verify(token).status_code, 400)
        self.assert_user_state(self.member, False, User.AccountStatus.DEACTIVATED)

    def test_cannot_change_status_of_pending_verification_user(self):
        pending_user = self._make_user("pending@example.com", "Pen", "Ding", self.customer_role, company=self.alpha)
        pending_user.account_status = User.AccountStatus.PENDING_VERIFICATION
        pending_user.is_active = False
        pending_user.save()

        r = self.user_status(pending_user, False)
        self.assertEqual(r.status_code, 400, r.content)

        r = self.user_status(pending_user, True)
        self.assertEqual(r.status_code, 400, r.content)


# =====================================================================
# 3. Primary Contact Transfer Tests
# =====================================================================
class PrimaryContactTransferTests(BaseStatusWorkflowTest):
    def test_primary_contact_cannot_be_deactivated_before_transfer(self):
        r = self.user_status(self.owner, False)
        self.assertEqual(r.status_code, 400, r.content)
        self.assert_user_state(self.owner, True, User.AccountStatus.ACTIVE)

    def test_primary_contact_transfer_then_old_primary_can_be_deactivated(self):
        User.objects.filter(pk=self.member.pk).update(phone="+919876543219")
        r = self.transfer_primary(self.alpha, self.member)
        self.assertEqual(r.status_code, 200, r.content)
        self.assertFalse(CustomerUser.objects.get(user=self.owner).is_primary_contact)
        self.assertTrue(CustomerUser.objects.get(user=self.member).is_primary_contact)

        r = self.user_status(self.owner, False)
        self.assertEqual(r.status_code, 200, r.content)
        self.assert_user_state(self.owner, False, User.AccountStatus.DEACTIVATED)

    def test_primary_transfer_rejects_other_company_member(self):
        r = self.transfer_primary(self.alpha, self.beta_member)
        self.assertEqual(r.status_code, 400, r.content)
        self.assertTrue(CustomerUser.objects.get(user=self.owner).is_primary_contact)

    def test_primary_transfer_rejects_customer_actor(self):
        r = self.transfer_primary(self.alpha, self.member, self.member)
        self.assertEqual(r.status_code, 403, r.content)
        self.assertTrue(CustomerUser.objects.get(user=self.owner).is_primary_contact)

    def test_primary_transfer_rejects_inactive_or_unverified_target(self):
        unverified = self._make_user("unverified@example.com", "Un", "Ver", self.customer_role, company=self.alpha)
        unverified.email_verified = False
        unverified.save()

        r = self.transfer_primary(self.alpha, unverified)
        self.assertEqual(r.status_code, 400, r.content)

    def test_primary_transfer_rejects_same_user_already_primary(self):
        r = self.transfer_primary(self.alpha, self.owner)
        self.assertEqual(r.status_code, 400, r.content)


# =====================================================================
# 4. Customer Company Status Tests
# =====================================================================
class CustomerCompanyStatusTests(BaseStatusWorkflowTest):
    def test_company_deactivation_blocks_logins_without_changing_member_status(self):
        r = self.company_status(self.alpha, False)
        self.assertEqual(r.status_code, 200, r.content)
        self.assert_company_state(self.alpha, False, Customer.AccountStatus.DEACTIVATED)

        for u in (self.owner, self.member, self.delegate):
            self.assert_user_state(u, True, User.AccountStatus.ACTIVE)
            self.assertEqual(self.login(u.email, PW).status_code, 401)

        self.assertEqual(self.login(self.beta_owner.email, PW).status_code, 200)

    def test_company_deactivation_blocks_existing_jwt_tokens(self):
        # Owner acquires JWT while active
        data = self.jwt_as(self.owner.email, PW)
        access = data["access"]
        self.assertEqual(self.client.get(self.me_url).status_code, 200)

        # Company deactivated
        self.company_status(self.alpha, False)

        # Existing JWT is now blocked
        self.anonymous()
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {access}")
        r = self.client.get(self.me_url)
        self.assertEqual(r.status_code, 401, r.content)

    def test_company_reactivation_requires_primary_email_confirmation(self):
        self.assertEqual(self.company_status(self.alpha, False).status_code, 200)
        r = self.company_status(self.alpha, True)
        self.assertEqual(r.status_code, 202, r.content)
        self.assert_company_state(self.alpha, False, Customer.AccountStatus.DEACTIVATED)
        self.assertEqual(self.login(self.owner.email, PW).status_code, 401)

        token = self.verify_token(self.owner)
        req = EmailVerificationRequest.objects.get(
            token_hash=hashlib.sha256(token.encode()).hexdigest()
        )
        self.assertEqual(req.purpose, EmailVerificationRequest.Purpose.COMPANY_REACTIVATION)
        self.assertEqual(req.customer_id, self.alpha.pk)

        self.assertEqual(self.do_verify(token).status_code, 200)
        self.assert_company_state(self.alpha, True, Customer.AccountStatus.ACTIVE)
        self.assertEqual(self.login(self.owner.email, PW).status_code, 200)
        self.assertEqual(self.do_verify(token).status_code, 400)

    def test_company_reactivation_preserves_individually_deactivated_user(self):
        self.assertEqual(self.user_status(self.member, False).status_code, 200)
        self.assertEqual(self.company_status(self.alpha, False).status_code, 200)
        self.assertEqual(self.company_status(self.alpha, True).status_code, 202)

        self.assertEqual(self.do_verify(self.verify_token(self.owner)).status_code, 200)
        self.assert_user_state(self.member, False, User.AccountStatus.DEACTIVATED)
        self.assertEqual(self.login(self.member.email, PW).status_code, 401)
        self.assertEqual(self.login(self.owner.email, PW).status_code, 200)

    def test_company_reactivation_supersedes_previous_token(self):
        self.assertEqual(self.company_status(self.alpha, False).status_code, 200)
        self.assertEqual(self.company_status(self.alpha, True).status_code, 202)
        old_token = self.verify_token(self.owner)

        self.assertEqual(self.company_status(self.alpha, True).status_code, 202)
        new_token = self.verify_token(self.owner)
        self.assertNotEqual(old_token, new_token)

        self.assertEqual(self.do_verify(old_token).status_code, 400)
        self.assert_company_state(self.alpha, False, Customer.AccountStatus.DEACTIVATED)
        self.assertEqual(self.do_verify(new_token).status_code, 200)

    def test_company_reactivation_expired_token_rejected(self):
        self.assertEqual(self.company_status(self.alpha, False).status_code, 200)
        self.assertEqual(self.company_status(self.alpha, True).status_code, 202)
        token = self.verify_token(self.owner)

        EmailVerificationRequest.objects.filter(
            token_hash=hashlib.sha256(token.encode()).hexdigest()
        ).update(expires_at=timezone.now() - timedelta(seconds=1))

        self.assertEqual(self.do_verify(token).status_code, 400)
        self.assert_company_state(self.alpha, False, Customer.AccountStatus.DEACTIVATED)

    def test_customer_cannot_deactivate_company(self):
        r = self.company_status(self.alpha, False, self.owner)
        self.assertEqual(r.status_code, 403, r.content)
        self.assert_company_state(self.alpha, True, Customer.AccountStatus.ACTIVE)

    def test_non_admin_internal_cannot_deactivate_company(self):
        r = self.company_status(self.alpha, False, self.mgr)
        self.assertEqual(r.status_code, 403, r.content)
        self.assert_company_state(self.alpha, True, Customer.AccountStatus.ACTIVE)

    def test_company_status_rejects_non_boolean(self):
        self.as_user(self.admin)
        url = reverse("customer-status", kwargs={"id": self.alpha.pk})
        for payload in ({}, {"is_active": "true"}, {"is_active": 1}, {"is_active": None}):
            r = self.client.patch(url, payload, format="json")
            self.assertEqual(r.status_code, 400, r.content)
        self.assert_company_state(self.alpha, True, Customer.AccountStatus.ACTIVE)

    def test_company_cannot_deactivate_twice(self):
        self.assertEqual(self.company_status(self.alpha, False).status_code, 200)
        self.assertEqual(self.company_status(self.alpha, False).status_code, 400)

    def test_company_a_deactivation_does_not_affect_company_b(self):
        self.assertEqual(self.company_status(self.alpha, False).status_code, 200)
        self.assert_company_state(self.beta, True, Customer.AccountStatus.ACTIVE)
        self.assertEqual(self.login(self.beta_member.email, PW).status_code, 200)

    def test_company_reactivation_does_not_change_user_email_verified(self):
        verified_before = User.objects.get(pk=self.owner.pk).email_verified
        self.assertEqual(self.company_status(self.alpha, False).status_code, 200)
        self.assertEqual(self.company_status(self.alpha, True).status_code, 202)
        self.assertEqual(self.do_verify(self.verify_token(self.owner)).status_code, 200)
        self.owner.refresh_from_db()
        self.assertEqual(self.owner.email_verified, verified_before)


# =====================================================================
# 5. Status Permission Tests
# =====================================================================
class StatusPermissionTests(BaseStatusWorkflowTest):
    def test_internal_user_with_permission_can_deactivate_other_internal_user(self):
        RolePermission.objects.get_or_create(role=self.manager_role, permission=self.p_deactivate)
        r = self.user_status(self.agent, False, self.mgr)
        self.assertEqual(r.status_code, 200, r.content)
        self.assert_user_state(self.agent, False, User.AccountStatus.DEACTIVATED)

    def test_internal_user_without_permission_cannot_deactivate(self):
        r = self.user_status(self.mgr, False, self.agent)
        self.assertEqual(r.status_code, 403, r.content)
        self.assert_user_state(self.mgr, True, User.AccountStatus.ACTIVE)

    def test_internal_user_with_activate_permission_can_reactivate_internal(self):
        self.assertEqual(self.user_status(self.agent, False).status_code, 200)
        RolePermission.objects.get_or_create(role=self.manager_role, permission=self.p_activate)
        r = self.user_status(self.agent, True, self.mgr)
        self.assertEqual(r.status_code, 200, r.content)
        self.assert_user_state(self.agent, True, User.AccountStatus.ACTIVE)

    def test_non_admin_cannot_deactivate_admin_even_with_permission(self):
        RolePermission.objects.get_or_create(role=self.manager_role, permission=self.p_deactivate)
        r = self.user_status(self.admin, False, self.mgr)
        self.assertEqual(r.status_code, 403, r.content)
        self.assert_user_state(self.admin, True, User.AccountStatus.ACTIVE)

    def test_customer_with_status_permission_can_manage_own_member(self):
        RolePermission.objects.get_or_create(role=self.customer_role, permission=self.p_deactivate)
        RolePermission.objects.get_or_create(role=self.customer_role, permission=self.p_activate)

        # Non-primary customer cannot inherit CUSTOMER role status permissions
        r_non_primary = self.user_status(self.member, False, self.delegate)
        self.assertEqual(r_non_primary.status_code, 403, r_non_primary.content)

        # Primary contact inherits status permissions from CUSTOMER role
        r = self.user_status(self.member, False, self.owner)
        self.assertEqual(r.status_code, 200, r.content)

        r = self.user_status(self.member, True, self.owner)
        self.assertEqual(r.status_code, 202, r.content)
        self.assert_user_state(self.member, False, User.AccountStatus.DEACTIVATED)
        self.assertEqual(self.do_verify(self.verify_token(self.member)).status_code, 200)

    def test_customer_cannot_manage_other_company_even_with_permission(self):
        RolePermission.objects.get_or_create(role=self.customer_role, permission=self.p_deactivate)
        r = self.user_status(self.beta_member, False, self.owner)
        self.assertEqual(r.status_code, 403, r.content)
        self.assert_user_state(self.beta_member, True, User.AccountStatus.ACTIVE)

    def test_customer_cannot_change_primary_contact_status(self):
        RolePermission.objects.get_or_create(role=self.customer_role, permission=self.p_deactivate)
        r = self.user_status(self.owner, False, self.delegate)
        self.assertEqual(r.status_code, 403, r.content)
        self.assert_user_state(self.owner, True, User.AccountStatus.ACTIVE)

    def test_customer_cannot_manage_internal_user_status(self):
        RolePermission.objects.get_or_create(role=self.customer_role, permission=self.p_deactivate)
        r = self.user_status(self.agent, False, self.owner)
        self.assertEqual(r.status_code, 403, r.content)
        self.assert_user_state(self.agent, True, User.AccountStatus.ACTIVE)


# =====================================================================
# 6. Status Atomicity Tests
# =====================================================================
class StatusAtomicityTests(BaseStatusWorkflowTest):
    def test_deactivation_invalidates_pending_password_reset_and_verification(self):
        # Create pending requests
        EmailVerificationRequest.objects.create(
            user=self.member,
            token_hash="fakehash1",
            purpose=EmailVerificationRequest.Purpose.INITIAL_VERIFICATION,
            expires_at=timezone.now() + timedelta(hours=24),
        )
        PasswordResetRequest.objects.create(
            user=self.member,
            token_hash="fakehash2",
            expires_at=timezone.now() + timedelta(hours=24),
        )

        self.assertEqual(self.user_status(self.member, False).status_code, 200)
        self.assertFalse(PasswordResetRequest.objects.filter(user=self.member, used_at__isnull=True).exists())
        self.assertFalse(EmailVerificationRequest.objects.filter(user=self.member, used_at__isnull=True).exists())

    def test_company_deactivation_invalidates_pending_tokens_for_all_members(self):
        EmailVerificationRequest.objects.create(
            user=self.member,
            token_hash="fakehashmember",
            purpose=EmailVerificationRequest.Purpose.INITIAL_VERIFICATION,
            expires_at=timezone.now() + timedelta(hours=24),
        )
        PasswordResetRequest.objects.create(
            user=self.owner,
            token_hash="fakehashowner",
            expires_at=timezone.now() + timedelta(hours=24),
        )

        self.assertEqual(self.company_status(self.alpha, False).status_code, 200)
        self.assertFalse(PasswordResetRequest.objects.filter(user=self.owner, used_at__isnull=True).exists())
        self.assertFalse(EmailVerificationRequest.objects.filter(user=self.member, used_at__isnull=True).exists())

    def test_customer_reactivation_rolls_back_if_email_job_fails(self):
        self.assertEqual(self.user_status(self.member, False).status_code, 200)

        with mock.patch("apps.notifications.models.EmailJob.objects.create", side_effect=IntegrityError("Email queue down")):
            r = self.user_status(self.member, True)
            self.assertEqual(r.status_code, 500)

        # No stray unconfirmed activation request left
        self.assertFalse(
            EmailVerificationRequest.objects.filter(
                user=self.member,
                purpose=EmailVerificationRequest.Purpose.USER_REACTIVATION,
                used_at__isnull=True,
            ).exists()
        )
