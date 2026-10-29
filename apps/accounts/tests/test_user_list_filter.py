import uuid

from django.contrib.auth import get_user_model
from django.test import override_settings
from django.urls import reverse
from rest_framework.test import APITestCase

from apps.accounts.models import Permission, Role, RolePermission, UserRole
from apps.customers.models import Customer, CustomerUser

User = get_user_model()
PW = "Zq7!Lm2#Vx9pKd"


@override_settings(
    PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"]
)
class UserListCustomerFilterTests(APITestCase):
    @classmethod
    def setUpTestData(cls):
        cls.users_url = reverse("user-list")

        cls.admin_role = Role.objects.create(
            code="ADMIN",
            name="Admin",
            is_system_role=True,
            is_assignable=False,
        )
        cls.customer_role = Role.objects.create(
            code="CUSTOMER",
            name="Customer",
            is_system_role=True,
            is_assignable=True,
        )
        cls.agent_role = Role.objects.create(
            code="SUPPORT_AGENT",
            name="Support Agent",
            is_system_role=True,
            is_assignable=True,
        )
        cls.viewer_role = Role.objects.create(
            code="INTERNAL_VIEWER",
            name="Internal Viewer",
            is_system_role=True,
            is_assignable=True,
        )
        cls.delegate_role = Role.objects.create(
            code="CUSTOMER_MANAGER",
            name="Customer Manager",
            is_system_role=True,
            is_assignable=True,
        )

        cls.p_view = Permission.objects.create(
            name="users.view",
            module="users",
            action="view",
        )

        RolePermission.objects.create(
            role=cls.viewer_role,
            permission=cls.p_view,
        )
        RolePermission.objects.create(
            role=cls.customer_role,
            permission=cls.p_view,
        )
        RolePermission.objects.create(
            role=cls.delegate_role,
            permission=cls.p_view,
        )

        cls.company_a = Customer.objects.create(
            company_name="Company A",
            contact_person="Owner A",
            email="owner.a@example.com",
            phone="+919876543210",
            address="Address A",
            state="Gujarat",
            country="India",
            is_active=True,
            account_status=Customer.AccountStatus.ACTIVE,
        )
        cls.company_b = Customer.objects.create(
            company_name="Company B",
            contact_person="Owner B",
            email="owner.b@example.com",
            phone="+919876543211",
            address="Address B",
            state="Gujarat",
            country="India",
            is_active=True,
            account_status=Customer.AccountStatus.ACTIVE,
        )

        cls.admin = cls._make_user(
            "admin@example.com",
            "System",
            "Admin",
            cls.admin_role,
        )
        cls.internal_viewer = cls._make_user(
            "viewer@example.com",
            "Internal",
            "Viewer",
            cls.viewer_role,
        )
        cls.internal_no_perm = cls._make_user(
            "agent@example.com",
            "Support",
            "Agent",
            cls.agent_role,
        )

        cls.primary_a = cls._make_user(
            "owner.a@example.com",
            "Owner",
            "A",
            cls.customer_role,
            company=cls.company_a,
            primary=True,
        )
        cls.delegate_a = cls._make_user(
            "delegate.a@example.com",
            "Delegate",
            "A",
            cls.delegate_role,
            company=cls.company_a,
        )
        cls.member_a = cls._make_user(
            "member.a@example.com",
            "Member",
            "A",
            cls.customer_role,
            company=cls.company_a,
        )

        cls.primary_b = cls._make_user(
            "owner.b@example.com",
            "Owner",
            "B",
            cls.customer_role,
            company=cls.company_b,
            primary=True,
        )
        cls.member_b = cls._make_user(
            "member.b@example.com",
            "Member",
            "B",
            cls.customer_role,
            company=cls.company_b,
        )

    @classmethod
    def _make_user(
        cls,
        email,
        first_name,
        last_name,
        role,
        company=None,
        primary=False,
    ):
        user = User.objects.create_user(
            email=email,
            password=PW,
            first_name=first_name,
            last_name=last_name,
            email_verified=True,
            is_active=True,
            account_status=User.AccountStatus.ACTIVE,
        )
        UserRole.objects.create(user=user, role=role)
        if company is not None:
            CustomerUser.objects.create(
                customer=company,
                user=user,
                is_primary_contact=primary,
            )
        return user

    def as_user(self, user):
        self.client.force_authenticate(user=user)

    def _result_emails(self, response):
        return {u["email"] for u in response.json()["data"]["results"]}

    def test_admin_unfiltered_retains_existing_list_behavior(self):
        self.as_user(self.admin)
        response = self.client.get(self.users_url, {"page_size": 100})
        self.assertEqual(response.status_code, 200, response.content)
        emails = self._result_emails(response)
        self.assertTrue(
            {
                self.admin.email,
                self.internal_viewer.email,
                self.primary_a.email,
                self.primary_b.email,
            }
            <= emails
        )

    def test_admin_filters_by_company_a(self):
        self.as_user(self.admin)
        response = self.client.get(
            self.users_url,
            {"customer_id": str(self.company_a.id), "page_size": 100},
        )
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(
            self._result_emails(response),
            {
                self.primary_a.email,
                self.delegate_a.email,
                self.member_a.email,
            },
        )
        self.assertEqual(response.json()["data"]["count"], 3)

    def test_internal_user_with_users_view_filters_by_company_a(self):
        self.as_user(self.internal_viewer)
        response = self.client.get(
            self.users_url,
            {"customer_id": str(self.company_a.id), "page_size": 100},
        )
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(
            self._result_emails(response),
            {
                self.primary_a.email,
                self.delegate_a.email,
                self.member_a.email,
            },
        )

    def test_customer_primary_contact_unfiltered_returns_own_company_users(self):
        self.as_user(self.primary_a)
        response = self.client.get(self.users_url, {"page_size": 100})
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(
            self._result_emails(response),
            {
                self.primary_a.email,
                self.delegate_a.email,
                self.member_a.email,
            },
        )

    def test_customer_primary_contact_with_own_customer_id_returns_own_company_users(self):
        self.as_user(self.primary_a)
        response = self.client.get(
            self.users_url,
            {"customer_id": str(self.company_a.id), "page_size": 100},
        )
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(
            self._result_emails(response),
            {
                self.primary_a.email,
                self.delegate_a.email,
                self.member_a.email,
            },
        )

    def test_customer_with_users_view_and_own_customer_id_returns_own_company_users(self):
        # Non-primary customer cannot inherit users.view from role
        self.as_user(self.delegate_a)
        r_non_primary = self.client.get(
            self.users_url,
            {"customer_id": str(self.company_a.id), "page_size": 100},
        )
        self.assertEqual(r_non_primary.status_code, 403, r_non_primary.content)

        # Primary contact inherits users.view from CUSTOMER role
        self.as_user(self.primary_a)
        response = self.client.get(
            self.users_url,
            {"customer_id": str(self.company_a.id), "page_size": 100},
        )
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(
            self._result_emails(response),
            {
                self.primary_a.email,
                self.delegate_a.email,
                self.member_a.email,
            },
        )

    def test_company_a_user_requesting_company_b_returns_403(self):
        for user in (self.primary_a, self.delegate_a):
            with self.subTest(user=user.email):
                self.as_user(user)
                response = self.client.get(
                    self.users_url,
                    {"customer_id": str(self.company_b.id)},
                )
                self.assertEqual(response.status_code, 403, response.content)

    def test_user_without_list_permission_with_customer_id_returns_403(self):
        for user in (self.member_a, self.internal_no_perm):
            with self.subTest(user=user.email):
                self.as_user(user)
                response = self.client.get(
                    self.users_url,
                    {"customer_id": str(self.company_a.id)},
                )
                self.assertEqual(response.status_code, 403, response.content)

    def test_authorized_user_with_invalid_customer_id_returns_400(self):
        self.as_user(self.admin)
        for bad_value in ("invalid", "12345", ""):
            with self.subTest(customer_id=bad_value):
                response = self.client.get(
                    self.users_url,
                    {"customer_id": bad_value},
                )
                self.assertEqual(response.status_code, 400, response.content)
                self.assertIn("customer_id", response.json())

    def test_internal_user_with_nonexistent_customer_id_returns_404(self):
        for user in (self.admin, self.internal_viewer):
            with self.subTest(user=user.email):
                self.as_user(user)
                response = self.client.get(
                    self.users_url,
                    {"customer_id": str(uuid.uuid4())},
                )
                self.assertEqual(response.status_code, 404, response.content)

    def test_customer_member_with_admin_role_is_still_scoped_to_own_company(self):
        customer_admin = self._make_user(
            "cust.admin@example.com",
            "Cust",
            "Admin",
            self.admin_role,
            company=self.company_a,
        )
        self.as_user(customer_admin)

        response = self.client.get(self.users_url, {"page_size": 100})
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(
            self._result_emails(response),
            {
                self.primary_a.email,
                self.delegate_a.email,
                self.member_a.email,
                customer_admin.email,
            },
        )

        other_response = self.client.get(
            self.users_url,
            {"customer_id": str(self.company_b.id)},
        )
        self.assertEqual(other_response.status_code, 403, other_response.content)
