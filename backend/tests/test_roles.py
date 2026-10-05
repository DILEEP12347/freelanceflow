from apps.tenants.models import Role
from apps.tenants.services import create_tenant

from .helpers import TenantTestBase, client_for, make_user


class ClientAccessTests(TenantTestBase):
    def test_anonymous_gets_401(self):
        self.assertEqual(client_for(host=self.host).get("/api/clients/").status_code, 401)

    def test_user_from_another_tenant_gets_403(self):
        other_owner = make_user("owner@beta.test")
        create_tenant("Beta", "beta", owner=other_owner)
        # Beta's owner has a perfectly valid token, but no membership in Acme.
        self.assertEqual(client_for(other_owner, self.host).get("/api/clients/").status_code, 403)
        # And Acme's owner can't use their token on Beta either.
        self.assertEqual(client_for(self.owner, "beta.localhost").get("/api/clients/").status_code, 403)

    def test_role_matrix_on_clients(self):
        cases = {
            Role.VIEWER: (200, 403),
            Role.ACCOUNTANT: (200, 201),
            Role.ADMIN: (200, 201),
        }
        for role, (read_status, write_status) in cases.items():
            user = self.add_member(f"{role.value}@acme.test", role)
            c = client_for(user, self.host)
            self.assertEqual(c.get("/api/clients/").status_code, read_status, role)
            r = c.post("/api/clients/", {"name": f"by {role.value}"}, format="json")
            self.assertEqual(r.status_code, write_status, role)
        owner_client = client_for(self.owner, self.host)
        self.assertEqual(owner_client.post("/api/clients/", {"name": "by owner"}, format="json").status_code, 201)


class BusinessProfileTests(TenantTestBase):
    def test_defaults_to_tenant_name(self):
        r = client_for(self.owner, self.host).get("/api/business-profile/")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.data["business_name"], "Acme")

    def test_viewer_reads_but_cannot_edit(self):
        viewer = self.add_member("viewer@acme.test", Role.VIEWER)
        c = client_for(viewer, self.host)
        self.assertEqual(c.get("/api/business-profile/").status_code, 200)
        self.assertEqual(c.patch("/api/business-profile/", {"phone": "1"}, format="json").status_code, 403)

    def test_owner_can_edit(self):
        c = client_for(self.owner, self.host)
        r = c.patch("/api/business-profile/", {"tax_id": "29ABCDE1234F1Z5", "invoice_prefix": "acm"}, format="json")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.data["invoice_prefix"], "ACM")
        self.assertEqual(c.get("/api/business-profile/").data["tax_id"], "29ABCDE1234F1Z5")
