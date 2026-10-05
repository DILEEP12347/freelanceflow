import re
from io import StringIO

from django.core import mail
from django.core.management import call_command
from django.db import connection
from django.test import TransactionTestCase

from apps.tenants.models import Membership, Role, Tenant

from .helpers import PASSWORD, client_for, make_user


class AuthFlowTests(TransactionTestCase):
    def setUp(self):
        connection.set_schema_to_public()
        call_command("bootstrap_public", stdout=StringIO())

    def tearDown(self):
        connection.set_schema_to_public()
        for t in Tenant.objects.exclude(schema_name="public"):
            t.delete(force_drop=True)

    def test_register_verify_login(self):
        c = client_for()
        r = c.post("/api/auth/register/",
                   {"email": "New@Example.com", "password": PASSWORD, "full_name": "New User"}, format="json")
        self.assertEqual(r.status_code, 201)

        # Not verified yet -> login refused
        creds = {"email": "new@example.com", "password": PASSWORD}
        self.assertEqual(c.post("/api/auth/login/", creds, format="json").status_code, 401)

        token = re.search(r"token=(\S+)", mail.outbox[0].body).group(1)
        self.assertEqual(c.get("/api/auth/verify-email/", {"token": token}).status_code, 200)

        r = c.post("/api/auth/login/", creds, format="json")
        self.assertEqual(r.status_code, 200)
        self.assertIn("access", r.data)
        self.assertIn("refresh", r.data)

    def test_weak_password_rejected(self):
        r = client_for().post("/api/auth/register/",
                              {"email": "weak@example.com", "password": "12345678"}, format="json")
        self.assertEqual(r.status_code, 400)
        self.assertIn("password", r.data)

    def test_bad_verification_token(self):
        r = client_for().get("/api/auth/verify-email/", {"token": "garbage"})
        self.assertEqual(r.status_code, 400)

    def test_signup_requires_login_and_makes_owner(self):
        self.assertEqual(
            client_for().post("/api/tenants/signup/", {"name": "X", "subdomain": "xco"}, format="json").status_code,
            401,
        )
        user = make_user("founder@example.com")
        r = client_for(user).post("/api/tenants/signup/", {"name": "Xco", "subdomain": "xco"}, format="json")
        self.assertEqual(r.status_code, 201)
        self.assertEqual(Membership.objects.get(user=user).role, Role.OWNER)

    def test_unverified_user_cannot_create_org(self):
        user = make_user("unverified@example.com", verified=False)
        r = client_for(user).post("/api/tenants/signup/", {"name": "Y", "subdomain": "yco"}, format="json")
        self.assertEqual(r.status_code, 403)
