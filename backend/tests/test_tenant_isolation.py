from django.test import TransactionTestCase
from django_tenants.utils import schema_context
from rest_framework.exceptions import ValidationError

from apps.crm.models import Client
from apps.tenants.models import Tenant
from apps.tenants.services import create_tenant


class TenantIsolationTests(TransactionTestCase):
    def setUp(self):
        self.a = create_tenant("Acme", "acme")
        self.b = create_tenant("Beta Co", "betaco")

    def tearDown(self):
        for t in Tenant.objects.exclude(schema_name="public"):
            t.delete(force_drop=True)

    def test_tenant_a_cannot_see_tenant_b_data(self):
        with schema_context(self.a.schema_name):
            Client.objects.create(name="Acme's client")
        with schema_context(self.b.schema_name):
            self.assertEqual(Client.objects.count(), 0)
        with schema_context(self.a.schema_name):
            self.assertEqual(Client.objects.count(), 1)

    def test_subdomain_rules(self):
        with self.assertRaises(ValidationError):
            create_tenant("Dup", "acme")  # already taken
        with self.assertRaises(ValidationError):
            create_tenant("Admin", "admin")  # reserved
