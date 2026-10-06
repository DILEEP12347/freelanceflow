from datetime import timedelta

from django.db import connection
from django.utils import timezone

from apps.tenants.models import Role
from apps.tenants.services import create_tenant

from .helpers import client_for, make_user
from .test_crm_clients import CrmBase


def days_ago(n):
    return (timezone.localdate() - timedelta(days=n)).isoformat()


class InvoiceBase(CrmBase):
    def setUp(self):
        super().setUp()
        rates = self.api.get("/api/tax-rates/").data  # first call seeds the GST slabs
        self.gst18 = next(r["id"] for r in rates if r["rate_bps"] == 1800)

    def line(self, description="Design work", qty=1, price=1000000, taxed=True):
        data = {"description": description, "quantity": qty, "unit_price_minor": price}
        if taxed:
            data["tax_rate_id"] = self.gst18
        return data

    def make_invoice(self, client_id=None, lines=None, api=None, **extra):
        if client_id is None:
            client_id = self.make_client(f"Client {timezone.now().timestamp()}", payment_terms_days=30)["id"]
        body = {"client": client_id, "lines": lines if lines is not None else [self.line()], **extra}
        r = (api or self.api).post("/api/invoices/", body, format="json")
        self.assertEqual(r.status_code, 201, r.data)
        return r.data

    def send(self, invoice_id, api=None, **body):
        return (api or self.api).post(f"/api/invoices/{invoice_id}/send/", body, format="json")

    def pay(self, invoice_id, amount, api=None, **extra):
        return (api or self.api).post(
            f"/api/invoices/{invoice_id}/payments/", {"amount_minor": amount, **extra}, format="json"
        )


class TaxRateTests(InvoiceBase):
    def test_default_gst_slabs_are_seeded_with_one_default(self):
        rates = self.api.get("/api/tax-rates/").data
        self.assertEqual(sorted(r["rate_bps"] for r in rates), [0, 500, 1200, 1800, 2800])
        self.assertEqual([r["rate_bps"] for r in rates if r["is_default"]], [1800])

    def test_making_another_rate_default_unsets_the_old_one(self):
        r = self.api.post("/api/tax-rates/", {"name": "Custom 3%", "rate_bps": 300, "is_default": True}, format="json")
        self.assertEqual(r.status_code, 201, r.data)
        defaults = [x["name"] for x in self.api.get("/api/tax-rates/").data if x["is_default"]]
        self.assertEqual(defaults, ["Custom 3%"])

    def test_rate_cannot_exceed_100_percent(self):
        r = self.api.post("/api/tax-rates/", {"name": "Silly", "rate_bps": 10001}, format="json")
        self.assertEqual(r.status_code, 400)


class DraftTests(InvoiceBase):
    def test_totals_are_computed_by_the_server(self):
        inv = self.make_invoice(
            lines=[self.line(qty=2, price=1000000), self.line("Domain", price=50000, taxed=False)],
            total_minor=1,  # a client-supplied total must be ignored
        )
        self.assertEqual(inv["status"], "draft")
        self.assertIsNone(inv["number"])
        self.assertEqual((inv["subtotal_minor"], inv["tax_minor"], inv["total_minor"]), (2050000, 360000, 2410000))
        self.assertEqual(len(inv["lines"]), 2)
        self.assertEqual(inv["lines"][0]["tax_name"], "GST 18%")
        self.assertEqual(inv["balance_due_minor"], 2410000)

    def test_editing_a_draft_replaces_its_lines(self):
        inv = self.make_invoice()
        r = self.api.patch(f"/api/invoices/{inv['id']}/", {"lines": [self.line("New", price=200000, taxed=False)]}, format="json")
        self.assertEqual(r.status_code, 200, r.data)
        self.assertEqual(len(r.data["lines"]), 1)
        self.assertEqual(r.data["total_minor"], 200000)

    def test_unknown_tax_rate_and_bad_lines_are_rejected(self):
        client_id = self.make_client()["id"]
        bad = [{"description": "x", "unit_price_minor": 100, "tax_rate_id": 99999}]
        self.assertEqual(self.api.post("/api/invoices/", {"client": client_id, "lines": bad}, format="json").status_code, 400)
        neg = [{"description": "x", "unit_price_minor": -5}]
        self.assertEqual(self.api.post("/api/invoices/", {"client": client_id, "lines": neg}, format="json").status_code, 400)

    def test_archived_client_cannot_get_an_invoice(self):
        cid = self.make_client()["id"]
        self.api.post(f"/api/clients/{cid}/archive/")
        r = self.api.post("/api/invoices/", {"client": cid, "lines": [self.line()]}, format="json")
        self.assertEqual(r.status_code, 400)

    def test_foreign_currency_needs_an_exchange_rate(self):
        cid = self.make_client()["id"]
        r = self.api.post("/api/invoices/", {"client": cid, "currency": "usd", "lines": [self.line()]}, format="json")
        self.assertEqual(r.status_code, 400)
        r = self.api.post(
            "/api/invoices/", {"client": cid, "currency": "usd", "exchange_rate": "83.5", "lines": [self.line()]}, format="json"
        )
        self.assertEqual(r.status_code, 201, r.data)
        self.assertEqual(r.data["currency"], "USD")
        self.assertEqual(float(r.data["exchange_rate"]), 83.5)

    def test_draft_can_be_deleted_by_manager_only(self):
        inv = self.make_invoice()
        self.assertEqual(self.as_role(Role.ACCOUNTANT).delete(f"/api/invoices/{inv['id']}/").status_code, 403)
        self.assertEqual(self.api.delete(f"/api/invoices/{inv['id']}/").status_code, 204)
        self.assertEqual(self.api.get(f"/api/invoices/{inv['id']}/").status_code, 404)


class SendTests(InvoiceBase):
    def test_numbers_are_consecutive_and_dates_follow_payment_terms(self):
        cid = self.make_client("Terms Co", payment_terms_days=30)["id"]
        first, second = self.make_invoice(cid), self.make_invoice(cid)
        r1, r2 = self.send(first["id"]), self.send(second["id"])
        self.assertEqual(r1.status_code, 200, r1.data)
        self.assertEqual((r1.data["number"], r2.data["number"]), ("INV-0001", "INV-0002"))
        self.assertEqual(r1.data["status"], "sent")
        self.assertEqual(r1.data["issue_date"], timezone.localdate().isoformat())
        self.assertEqual(r1.data["due_date"], (timezone.localdate() + timedelta(days=30)).isoformat())

    def test_cannot_send_without_lines_or_twice(self):
        empty = self.make_invoice(lines=[])
        self.assertEqual(self.send(empty["id"]).status_code, 400)
        inv = self.make_invoice()
        self.assertEqual(self.send(inv["id"]).status_code, 200)
        self.assertEqual(self.send(inv["id"]).status_code, 400)

    def test_due_date_before_issue_date_is_rejected(self):
        inv = self.make_invoice()
        r = self.send(inv["id"], issue_date=days_ago(0), due_date=days_ago(5))
        self.assertEqual(r.status_code, 400)

    def test_sent_invoice_is_locked(self):
        inv = self.make_invoice()
        self.send(inv["id"])
        r = self.api.patch(f"/api/invoices/{inv['id']}/", {"notes": "sneaky"}, format="json")
        self.assertEqual(r.status_code, 400)
        self.assertEqual(self.api.delete(f"/api/invoices/{inv['id']}/").status_code, 400)

    def test_seller_and_buyer_details_are_frozen_when_sent(self):
        cid = self.make_client("Old Name")["id"]
        inv = self.make_invoice(cid)
        sent = self.send(inv["id"]).data
        self.api.patch(f"/api/clients/{cid}/", {"name": "New Name"}, format="json")
        again = self.api.get(f"/api/invoices/{inv['id']}/").data
        self.assertEqual(sent["snapshot"]["to"]["name"], "Old Name")
        self.assertEqual(again["snapshot"]["to"]["name"], "Old Name")
        self.assertEqual(again["client_name"], "New Name")  # live link still shows the current name

    def test_gst_mode_follows_the_states(self):
        self.api.patch("/api/business-profile/", {"state": "Karnataka"}, format="json")
        same = self.make_invoice(self.make_client("Local Co", state="Karnataka")["id"])
        other = self.make_invoice(self.make_client("Faraway Co", state="Delhi")["id"])
        unknown = self.make_invoice(self.make_client("Mystery Co")["id"])
        self.assertEqual(self.send(same["id"]).data["gst_mode"], "intra")
        self.assertEqual(self.send(other["id"]).data["gst_mode"], "inter")
        self.assertIsNone(self.send(unknown["id"]).data["gst_mode"])


class PaymentTests(InvoiceBase):
    def test_partial_then_full_payment_and_removal(self):
        inv = self.make_invoice()  # total 1,180,000
        self.send(inv["id"])
        r = self.pay(inv["id"], 500000, method="upi")
        self.assertEqual(r.status_code, 201, r.data)
        detail = self.api.get(f"/api/invoices/{inv['id']}/").data
        self.assertEqual((detail["status"], detail["balance_due_minor"]), ("partial", 680000))

        self.assertEqual(self.pay(inv["id"], 700000).status_code, 400)   # more than the balance
        self.assertEqual(self.pay(inv["id"], 0).status_code, 400)

        last = self.pay(inv["id"], 680000)
        self.assertEqual(last.status_code, 201)
        detail = self.api.get(f"/api/invoices/{inv['id']}/").data
        self.assertEqual((detail["status"], detail["balance_due_minor"]), ("paid", 0))
        self.assertIsNotNone(detail["paid_at"])
        self.assertEqual(len(detail["payments"]), 2)
        self.assertEqual(self.pay(inv["id"], 100).status_code, 400)       # already paid

        self.assertEqual(self.api.delete(f"/api/invoices/{inv['id']}/payments/{last.data['id']}/").status_code, 204)
        detail = self.api.get(f"/api/invoices/{inv['id']}/").data
        self.assertEqual((detail["status"], detail["balance_due_minor"]), ("partial", 680000))
        self.assertIsNone(detail["paid_at"])

    def test_no_payments_on_drafts(self):
        inv = self.make_invoice()
        self.assertEqual(self.pay(inv["id"], 100).status_code, 400)

    def test_payment_roles(self):
        inv = self.make_invoice()
        self.send(inv["id"])
        self.assertEqual(self.pay(inv["id"], 100, api=self.as_role(Role.VIEWER)).status_code, 403)
        made = self.pay(inv["id"], 100, api=self.as_role(Role.ACCOUNTANT))
        self.assertEqual(made.status_code, 201)
        url = f"/api/invoices/{inv['id']}/payments/{made.data['id']}/"
        self.assertEqual(self.as_role(Role.ACCOUNTANT, "acc2@acme.test").delete(url).status_code, 403)
        self.assertEqual(self.api.delete(url).status_code, 204)


class VoidTests(InvoiceBase):
    def test_void_keeps_the_number_and_it_is_never_reused(self):
        cid = self.make_client()["id"]
        first = self.make_invoice(cid)
        self.assertEqual(self.send(first["id"]).data["number"], "INV-0001")
        r = self.api.post(f"/api/invoices/{first['id']}/void/", {"reason": "Wrong client"}, format="json")
        self.assertEqual(r.status_code, 200, r.data)
        self.assertEqual((r.data["status"], r.data["number"], r.data["balance_due_minor"]), ("void", "INV-0001", 0))
        second = self.make_invoice(cid)
        self.assertEqual(self.send(second["id"]).data["number"], "INV-0002")
        self.assertEqual(self.pay(first["id"], 100).status_code, 400)     # void invoices take no payments

    def test_void_rules(self):
        draft = self.make_invoice()
        self.assertEqual(self.api.post(f"/api/invoices/{draft['id']}/void/").status_code, 400)  # delete drafts instead
        paid_some = self.make_invoice()
        self.send(paid_some["id"])
        self.pay(paid_some["id"], 100)
        self.assertEqual(self.api.post(f"/api/invoices/{paid_some['id']}/void/").status_code, 400)

    def test_only_managers_can_void(self):
        inv = self.make_invoice()
        self.send(inv["id"])
        url = f"/api/invoices/{inv['id']}/void/"
        self.assertEqual(self.as_role(Role.ACCOUNTANT).post(url).status_code, 403)
        self.assertEqual(self.as_role(Role.ADMIN).post(url).status_code, 200)


class ListAndReportTests(InvoiceBase):
    def test_overdue_is_derived_from_the_due_date(self):
        late = self.make_invoice()
        self.send(late["id"], issue_date=days_ago(10), due_date=days_ago(1))
        fine = self.make_invoice()
        self.send(fine["id"])
        draft = self.make_invoice()

        overdue = self.api.get("/api/invoices/", {"status": "overdue"}).data
        self.assertEqual([i["id"] for i in overdue["results"]], [late["id"]])
        self.assertEqual(overdue["results"][0]["display_status"], "overdue")
        self.assertEqual(overdue["results"][0]["status"], "sent")        # stored status is unchanged
        self.assertEqual(self.api.get("/api/invoices/", {"status": "open"}).data["count"], 2)
        self.assertEqual(self.api.get("/api/invoices/", {"status": "draft"}).data["count"], 1)
        self.assertEqual(self.api.get("/api/invoices/", {"status": "nonsense"}).status_code, 400)

        self.pay(late["id"], late["total_minor"])                          # paying clears the overdue flag
        self.assertEqual(self.api.get("/api/invoices/", {"status": "overdue"}).data["count"], 0)
        self.assertEqual(draft["status"], "draft")

    def test_search_and_client_filter(self):
        a = self.make_client("Alpha Studio")["id"]
        b = self.make_client("Beta Works")["id"]
        inv_a, inv_b = self.make_invoice(a), self.make_invoice(b)
        self.send(inv_a["id"])
        self.assertEqual(self.api.get("/api/invoices/", {"q": "INV-0001"}).data["count"], 1)
        self.assertEqual(self.api.get("/api/invoices/", {"q": "alpha"}).data["count"], 1)
        self.assertEqual(self.api.get("/api/invoices/", {"client": b}).data["results"][0]["id"], inv_b["id"])
        self.assertEqual(self.api.get("/api/invoices/", {"client": "abc"}).status_code, 400)
        self.assertEqual(self.api.get("/api/invoices/", {"issued_from": "garbage"}).status_code, 400)
        self.assertEqual(self.api.get("/api/invoices/", {"issued_from": days_ago(1)}).data["count"], 1)

    def test_summary_per_currency(self):
        late = self.make_invoice()                                          # 1,180,000, overdue
        self.send(late["id"], issue_date=days_ago(40), due_date=days_ago(1))
        part = self.make_invoice(lines=[self.line(taxed=False)])            # 1,000,000
        self.send(part["id"])
        self.pay(part["id"], 180000)
        self.make_invoice()                                                 # draft
        s = self.api.get("/api/invoices/summary/").data
        inr = s["currencies"]["INR"]
        self.assertEqual(s["draft_count"], 1)
        self.assertEqual(inr["outstanding_minor"], 1180000 + 820000)
        self.assertEqual((inr["overdue_minor"], inr["overdue_count"], inr["open_count"]), (1180000, 1, 2))
        self.assertEqual(inr["paid_this_month_minor"], 180000)

    def test_timeline_shows_invoice_events(self):
        cid = self.make_client()["id"]
        inv = self.make_invoice(cid)
        self.send(inv["id"])
        self.pay(inv["id"], inv["total_minor"])
        kinds = self.kinds(f"/api/clients/{cid}/timeline/")
        for expected in ("invoice_created", "invoice_sent", "payment_recorded", "invoice_paid"):
            self.assertIn(expected, kinds)

    def test_client_with_invoices_cannot_be_deleted(self):
        cid = self.make_client()["id"]
        self.make_invoice(cid)
        self.assertEqual(self.api.delete(f"/api/clients/{cid}/").status_code, 409)
        self.assertEqual(self.api.get(f"/api/clients/{cid}/").status_code, 200)


class PdfTests(InvoiceBase):
    def test_pdf_for_draft_and_sent_invoice(self):
        inv = self.make_invoice(lines=[self.line("Fish & chips <special>\nwith notes")], notes="Thanks & regards")
        for _ in range(2):
            r = self.api.get(f"/api/invoices/{inv['id']}/pdf/")
            self.assertEqual(r.status_code, 200)
            self.assertEqual(r["Content-Type"], "application/pdf")
            self.assertTrue(r.content.startswith(b"%PDF"))
            self.send(inv["id"])
        self.assertIn("INV-0001", self.api.get(f"/api/invoices/{inv['id']}/pdf/")["Content-Disposition"])


class AccessTests(InvoiceBase):
    def test_role_matrix(self):
        viewer = self.as_role(Role.VIEWER)
        self.assertEqual(viewer.get("/api/invoices/").status_code, 200)
        self.assertEqual(viewer.post("/api/invoices/", {"client": 1, "lines": []}, format="json").status_code, 403)
        cid = self.make_client()["id"]
        self.make_invoice(cid, api=self.as_role(Role.ACCOUNTANT))
        self.make_invoice(cid, api=self.as_role(Role.ADMIN))

    def test_anonymous_and_other_tenant_are_locked_out(self):
        # Tenants can only be created from the public schema, but setUp already made requests to
        # acme.localhost, which switched the connection to Acme's schema. Switch back first.
        connection.set_schema_to_public()
        other_owner = make_user("owner@beta.test")
        create_tenant("Beta", "beta", owner=other_owner)
        self.make_invoice()
        self.assertEqual(client_for(host=self.host).get("/api/invoices/").status_code, 401)
        self.assertEqual(client_for(other_owner, self.host).get("/api/invoices/").status_code, 403)
        beta = client_for(other_owner, "beta.localhost")
        self.assertEqual(beta.get("/api/invoices/").data["count"], 0)     # Acme's invoice is invisible there
