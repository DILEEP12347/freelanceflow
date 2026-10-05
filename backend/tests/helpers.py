from io import StringIO

from django.core.management import call_command
from django.db import connection
from django.test import TransactionTestCase
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import RefreshToken

from apps.accounts.models import User
from apps.tenants.models import Membership, Tenant
from apps.tenants.services import create_tenant

PASSWORD = "Str0ng-Pass-123!"


def make_user(email, verified=True):
    return User.objects.create_user(email=email, password=PASSWORD, is_email_verified=verified)


def client_for(user=None, host="localhost"):
    """An API client that talks to `host` (this picks the tenant) as `user` (None = anonymous)."""
    client = APIClient()
    client.defaults["HTTP_HOST"] = host
    if user is not None:
        token = RefreshToken.for_user(user).access_token
        client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")
    return client


class TenantTestBase(TransactionTestCase):
    """Creates the public tenant plus one organization 'acme' owned by owner@acme.test."""

    def setUp(self):
        connection.set_schema_to_public()
        call_command("bootstrap_public", stdout=StringIO())
        self.owner = make_user("owner@acme.test")
        self.tenant = create_tenant("Acme", "acme", owner=self.owner)
        self.host = "acme.localhost"

    def tearDown(self):
        connection.set_schema_to_public()
        for t in Tenant.objects.exclude(schema_name="public"):
            t.delete(force_drop=True)

    def add_member(self, email, role):
        user = make_user(email)
        Membership.objects.create(user=user, tenant=self.tenant, role=role)
        return user
