from django.db import connection

from apps.audit.models import AuditLog
from apps.tenants.models import Role
from apps.tenants.services import create_tenant

from .helpers import client_for, make_user
from .test_week7_work import Week7Base


class AuditBase(Week7Base):
    def setUp(self):
        super().setUp()
        self.set_plan("business")       # recording happens on every plan; reading the log needs Business

    def entries(self, **params):
        r = self.api.get("/api/audit/", params)
        self.assertEqual(r.status_code, 200, r.data)
        return r.data["results"]

    def one(self, action, **params):
        found = [e for e in self.entries(action=action, **params)]
        self.assertTrue(found, f"no audit entry for {action}")
        return found[0]


class AuditRecordingTests(AuditBase):
    def test_a_create_is_recorded_with_who_what_and_when(self):
        r = self.api.post("/api/clients/", {"name": "Audited Co"}, format="json")
        entry = self.one("client.created")
        self.assertEqual((entry["actor_email"], entry["outcome"], entry["status_code"], entry["method"], entry["path"]),
                         (self.owner.email, "ok", 201, "POST", "/api/clients/"))
        self.assertEqual((entry["object_type"], entry["object_id"], entry["object_repr"]), ("client", str(r.data["id"]), "Audited Co"))
        self.assertEqual(entry["changes"]["name"], "Audited Co")
        self.assertIsNotNone(entry["ip_address"])
        self.assertTrue(entry["created_at"])

    def test_invoice_payment_and_void_actions(self):
        inv = self.make_invoice()
        sent = self.send(inv["id"]).data
        self.pay(inv["id"], 100000)
        self.api.post(f"/api/invoices/{inv['id']}/void/", {}, format="json")          # refused: it has a payment
        self.assertEqual(self.one("invoice.sent")["object_repr"], sent["number"])
        payment = self.one("payment.recorded")
        self.assertEqual((payment["parent_id"], payment["changes"]["amount_minor"]), (str(inv["id"]), 100000))
        self.assertEqual(self.entries(action="invoice.voided"), [])                    # a 400 is not an event
        inv2 = self.make_invoice()
        self.send(inv2["id"])
        self.api.post(f"/api/invoices/{inv2['id']}/void/", {"reason": "Wrong client"}, format="json")
        self.assertEqual(self.one("invoice.voided", object_id=inv2["id"])["changes"]["reason"], "Wrong client")

    def test_role_changes_are_recorded(self):
        self.as_role(Role.VIEWER)
        data = self.api.get("/api/team/members/").data
        members = data["results"] if isinstance(data, dict) and "results" in data else data
        viewer = next(m for m in members if m["email"] == "viewer@acme.test")
        r = self.api.patch(f"/api/team/members/{viewer['id']}/", {"role": "accountant"}, format="json")
        self.assertEqual(r.status_code, 200, r.data)
        entry = self.one("member.role_changed")
        self.assertEqual((entry["object_id"], entry["changes"]["role"]), (str(viewer["id"]), "accountant"))

    def test_refused_attempts_are_recorded_too(self):
        viewer = self.as_role(Role.VIEWER)
        self.assertEqual(viewer.post("/api/clients/", {"name": "Sneaky"}, format="json").status_code, 403)
        denied = self.entries(outcome="denied")
        self.assertEqual([(e["action"], e["actor_email"], e["status_code"]) for e in denied], [("client.created", "viewer@acme.test", 403)])

    def test_validation_errors_and_reads_are_not_recorded(self):
        self.api.post("/api/clients/", {}, format="json")                              # 400
        self.api.get("/api/clients/")
        self.api.get("/api/invoices/")
        self.assertEqual(self.entries(action="client."), [])
        self.assertEqual(self.entries(), [])                                           # nothing at all yet

    def test_a_failed_login_is_recorded_and_the_password_is_not(self):
        r = client_for(host=self.host).post("/api/auth/login/", {"email": self.owner.email, "password": "definitely-wrong"}, format="json")
        self.assertEqual(r.status_code, 401, r.data)
        entry = self.one("auth.login")
        self.assertEqual((entry["outcome"], entry["actor_email"], entry["changes"]["password"]), ("denied", self.owner.email, "[redacted]"))
        self.assertNotIn("definitely-wrong", str(entry))

    def test_it_is_recorded_on_every_plan_and_visible_after_upgrading(self):
        self.set_plan("free")
        self.api.post("/api/clients/", {"name": "Early Co"}, format="json")
        self.assertEqual(self.api.get("/api/audit/").status_code, 402)
        self.set_plan("business")
        self.assertEqual(len(self.entries(action="client.created")), 1)

    def test_stripe_driven_plan_changes_are_recorded(self):
        import time

        self.link_customer("cus_1")
        now = int(time.time())
        self.deliver(self.event("customer.subscription.created", self.sub_obj(status="active", price="price_pro"), created=now - 100))
        self.deliver(self.event("invoice.payment_failed", {"id": "in_1", "customer": "cus_1"}, created=now - 50))
        self.deliver(self.event("customer.subscription.deleted", self.sub_obj(status="canceled"), created=now - 10))
        with self.in_tenant():
            rows = list(AuditLog.objects.filter(actor_email="stripe").order_by("id").values("action", "changes"))
        self.assertEqual([r["action"] for r in rows], ["billing.subscription_updated", "billing.payment_failed", "billing.subscription_canceled"])
        self.assertEqual((rows[0]["changes"]["previous_plan"], rows[0]["changes"]["plan"]), ("business", "pro"))


class AuditViewerTests(AuditBase):
    def test_filters(self):
        cid = self.make_client("Alpha Audit")["id"]
        inv = self.make_invoice(cid)
        self.send(inv["id"])
        self.assertEqual({e["action"] for e in self.entries(action="invoice.")}, {"invoice.created", "invoice.sent"})
        self.assertEqual(len(self.entries(actor="owner@")), len(self.entries()))
        self.assertEqual(self.entries(actor="nobody@"), [])
        self.assertEqual({e["action"] for e in self.entries(object_type="client", object_id=cid)}, {"client.created"})
        self.assertEqual([e["action"] for e in self.entries(q="Alpha Audit")], ["client.created"])
        self.assertEqual(len(self.entries(**{"from": "2000-01-01", "to": "2099-12-31"})), len(self.entries()))
        self.assertEqual(self.entries(**{"from": "2099-01-01"}), [])
        self.assertEqual(self.api.get("/api/audit/", {"outcome": "bogus"}).status_code, 400)
        self.assertEqual(self.api.get("/api/audit/", {"from": "garbage"}).status_code, 400)
        names = self.api.get("/api/audit/actions/").data
        self.assertIn("client.created", names)
        self.assertEqual(names, sorted(names))

    def test_newest_first_and_detail(self):
        self.make_client("First")
        self.make_client("Second")
        rows = self.entries(action="client.created")
        self.assertEqual([r["object_repr"] for r in rows], ["Second", "First"])
        self.assertEqual(self.api.get(f"/api/audit/{rows[0]['id']}/").status_code, 200)

    def test_who_may_read_it(self):
        self.assertEqual(self.api.get("/api/audit/").status_code, 200)
        self.assertEqual(self.as_role(Role.ADMIN).get("/api/audit/").status_code, 200)
        self.assertEqual(self.as_role(Role.ACCOUNTANT).get("/api/audit/").status_code, 403)
        self.assertEqual(self.as_role(Role.VIEWER).get("/api/audit/").status_code, 403)
        self.assertEqual(client_for(host=self.host).get("/api/audit/").status_code, 401)
        for plan in ("free", "pro"):
            self.set_plan(plan)
            r = self.api.get("/api/audit/")
            self.assertEqual((r.status_code, r.data["code"]), (402, "feature_not_available"), plan)

    def test_the_log_cannot_be_changed_through_the_api_or_the_model(self):
        self.make_client("Immutable")
        entry = self.entries()[0]
        self.assertEqual(self.api.post("/api/audit/", {"action": "x"}, format="json").status_code, 405)
        self.assertEqual(self.api.patch(f"/api/audit/{entry['id']}/", {"action": "x"}, format="json").status_code, 405)
        self.assertEqual(self.api.delete(f"/api/audit/{entry['id']}/").status_code, 405)
        with self.in_tenant():
            row = AuditLog.objects.get(pk=entry["id"])
            row.action = "tampered"
            with self.assertRaises(ValueError):
                row.save()

    def test_each_organization_sees_only_its_own_log(self):
        connection.set_schema_to_public()
        other_owner = make_user("owner@other.test")
        create_tenant("Other", "other", owner=other_owner)
        client_for(other_owner, "other.localhost").post("/api/clients/", {"name": "Other secret"}, format="json")
        self.make_client("Our client")
        self.assertEqual(self.entries(q="Other secret"), [])
        self.assertEqual([e["object_repr"] for e in self.entries(action="client.created")], ["Our client"])
