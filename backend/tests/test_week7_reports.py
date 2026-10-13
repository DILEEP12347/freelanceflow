from datetime import date

from django.utils import timezone

from apps.tenants.models import Role

from .helpers import client_for
from .test_week7_work import Week7Base

PERIOD = {"from": "2026-07-01", "to": "2026-10-31"}
REPORTS = ["revenue", "receivables", "profit-loss", "clients", "time"]


class ReportAccessTests(Week7Base):
    def test_reports_are_a_business_plan_feature(self):
        for plan in ("free", "pro"):
            self.set_plan(plan)
            for name in REPORTS:
                r = self.api.get(f"/api/reports/{name}/")
                self.assertEqual((r.status_code, r.data["code"]), (402, "feature_not_available"), (plan, name))

    def test_who_may_read_them(self):
        self.set_plan("business")
        for name in REPORTS:
            self.assertEqual(self.api.get(f"/api/reports/{name}/").status_code, 200, name)
        self.assertEqual(self.as_role(Role.ACCOUNTANT).get("/api/reports/revenue/").status_code, 200)
        self.assertEqual(self.as_role(Role.VIEWER).get("/api/reports/revenue/").status_code, 403)
        self.assertEqual(client_for(host=self.host).get("/api/reports/revenue/").status_code, 401)


class ReportBase(Week7Base):
    def setUp(self):
        super().setUp()
        self.set_plan("business")
        self.client_a = self.make_client("Client A")["id"]
        self.client_b = self.make_client("Client B")["id"]

    def issue(self, client_id, issue, due, price=1000000, taxed=False, send=True, **extra):
        lines = [self.line(price=price, taxed=taxed)]
        data = self.make_invoice(client_id, lines=lines, **extra)
        if send:
            r = self.send(data["id"], issue_date=issue, due_date=due)
            self.assertEqual(r.status_code, 200, r.data)
            return r.data
        return data

    def seed_revenue(self):
        """A: Aug 1,000,000 fully paid. B: Sep 2,000,000 + 18% tax, 500,000 paid. Plus a void and a draft (ignored)."""
        a = self.issue(self.client_a, "2026-08-10", "2026-09-10")
        self.pay(a["id"], 1000000, paid_on="2026-08-20")
        b = self.issue(self.client_b, "2026-09-05", "2026-10-05", price=2000000, taxed=True)
        self.pay(b["id"], 500000, paid_on="2026-09-30")
        void = self.issue(self.client_a, "2026-09-06", "2026-10-06", price=700000)
        self.assertEqual(self.api.post(f"/api/invoices/{void['id']}/void/", {}, format="json").status_code, 200)
        self.issue(self.client_a, None, None, price=900000, send=False)           # a draft
        return a, b


class RevenueReportTests(ReportBase):
    def test_invoiced_and_collected_per_month(self):
        self.seed_revenue()
        data = self.api.get("/api/reports/revenue/", PERIOD).data
        self.assertEqual([c["currency"] for c in data["currencies"]], ["INR"])
        inr = data["currencies"][0]
        by_month = {m["month"]: m for m in inr["months"]}
        self.assertEqual(list(by_month), ["2026-07", "2026-08", "2026-09", "2026-10"])         # empty months are included
        self.assertEqual(by_month["2026-07"]["invoiced_minor"], 0)
        self.assertEqual((by_month["2026-08"]["invoiced_minor"], by_month["2026-08"]["collected_minor"], by_month["2026-08"]["invoice_count"]), (1000000, 1000000, 1))
        self.assertEqual((by_month["2026-09"]["invoiced_minor"], by_month["2026-09"]["tax_minor"], by_month["2026-09"]["collected_minor"]), (2360000, 360000, 500000))
        self.assertEqual(inr["totals"], {"invoiced_minor": 3360000, "tax_minor": 360000, "invoice_count": 2, "collected_minor": 1500000})

    def test_currencies_are_never_mixed(self):
        self.seed_revenue()
        self.issue(self.client_a, "2026-09-12", "2026-10-12", price=500000, currency="USD", exchange_rate="83")
        data = self.api.get("/api/reports/revenue/", PERIOD).data
        totals = {c["currency"]: c["totals"]["invoiced_minor"] for c in data["currencies"]}
        self.assertEqual(totals, {"INR": 3360000, "USD": 500000})

    def test_default_period_is_the_last_twelve_months(self):
        data = self.api.get("/api/reports/revenue/").data
        self.assertEqual(str(data["to"]), timezone.localdate().isoformat())
        self.issue(self.client_a, timezone.localdate().isoformat(), None, send=False)
        self.assertEqual(self.api.get("/api/reports/revenue/").status_code, 200)

    def test_period_validation(self):
        for params in ({"from": "2026-10-01", "to": "2026-01-01"}, {"from": "garbage"}, {"from": "2010-01-01", "to": "2026-01-01"}):
            self.assertEqual(self.api.get("/api/reports/revenue/", params).status_code, 400, params)

    def test_csv_export(self):
        self.seed_revenue()
        r = self.api.get("/api/reports/revenue/", {**PERIOD, "export": "csv"})
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r["Content-Type"].startswith("text/csv"))
        self.assertIn("revenue.csv", r["Content-Disposition"])
        lines = r.content.decode().splitlines()
        self.assertEqual(lines[0], "currency,month,invoiced_minor,tax_minor,invoice_count,collected_minor")
        self.assertIn("INR,2026-08,1000000,0,1,1000000", lines)


class ReceivablesReportTests(ReportBase):
    def seed(self):
        self.issue(self.client_a, "2026-09-01", "2026-12-01")                                  # not due yet
        late = self.issue(self.client_a, "2026-09-01", "2026-10-01")                           # 8 days late
        self.pay(late["id"], 400000, paid_on="2026-09-20")
        self.issue(self.client_b, "2026-07-21", "2026-08-20")                                  # 50 days late
        self.issue(self.client_b, "2026-05-02", "2026-06-01")                                  # 130 days late
        paid = self.issue(self.client_b, "2026-08-01", "2026-08-31")
        self.pay(paid["id"], 1000000, paid_on="2026-09-01")                                    # settled: excluded

    def test_aging_buckets_and_per_client_rows(self):
        self.seed()
        data = self.api.get("/api/reports/receivables/", {"as_of": "2026-10-09"}).data
        inr = data["currencies"][0]
        self.assertEqual(inr["buckets"], {"not_due": 1000000, "d1_30": 600000, "d31_60": 1000000, "d61_90": 0, "d90_plus": 1000000})
        self.assertEqual(inr["total_minor"], 3600000)
        self.assertEqual([(c["client_name"], c["total_minor"]) for c in inr["clients"]], [("Client B", 2000000), ("Client A", 1600000)])
        self.assertEqual(inr["clients"][0]["d90_plus"], 1000000)

    def test_drafts_and_voids_do_not_count(self):
        self.issue(self.client_a, None, None, send=False)
        self.assertEqual(self.api.get("/api/reports/receivables/").data["currencies"], [])

    def test_csv_cannot_run_formulas_in_a_spreadsheet(self):
        evil = self.make_client("=EVIL()")["id"]
        self.issue(evil, "2026-09-01", "2026-10-01")
        text = self.api.get("/api/reports/receivables/", {"as_of": "2026-10-09", "export": "csv"}).content.decode()
        self.assertIn("'=EVIL()", text)


class ProfitLossReportTests(ReportBase):
    def test_income_expenses_profit_and_gst(self):
        self.seed_revenue()
        self.make_expense(description="Hosting", category="Software", amount_minor=118000, tax_minor=18000, incurred_on="2026-09-02")
        self.make_expense(description="Train", category="Travel", amount_minor=50000, tax_minor=0, incurred_on="2026-08-15")
        data = self.api.get("/api/reports/profit-loss/", PERIOD).data
        self.assertIn("accrual", data["basis"])
        inr = data["currencies"][0]
        self.assertEqual(inr["totals"], {"income_minor": 3000000, "expenses_minor": 150000, "profit_minor": 2850000,
                                         "gst_collected_minor": 360000, "gst_paid_minor": 18000, "net_gst_payable_minor": 342000})
        months = {m["month"]: m for m in inr["months"]}
        self.assertEqual((months["2026-08"]["income_minor"], months["2026-08"]["expenses_minor"], months["2026-08"]["profit_minor"]), (1000000, 50000, 950000))
        self.assertEqual((months["2026-09"]["income_minor"], months["2026-09"]["expenses_minor"], months["2026-09"]["profit_minor"]), (2000000, 100000, 1900000))
        self.assertEqual(inr["expenses_by_category"], [{"category": "Software", "amount_minor": 100000}, {"category": "Travel", "amount_minor": 50000}])

    def test_expenses_with_no_income_still_show(self):
        self.make_expense(amount_minor=50000, tax_minor=0, incurred_on="2026-09-02")
        inr = self.api.get("/api/reports/profit-loss/", PERIOD).data["currencies"][0]
        self.assertEqual((inr["totals"]["income_minor"], inr["totals"]["profit_minor"]), (0, -50000))


class ClientsReportTests(ReportBase):
    def test_ranking_collected_and_outstanding(self):
        self.seed_revenue()
        rows = self.api.get("/api/reports/clients/", PERIOD).data["currencies"][0]["clients"]
        self.assertEqual([r["client_name"] for r in rows], ["Client B", "Client A"])
        b, a = rows
        self.assertEqual((b["invoiced_minor"], b["collected_minor"], b["outstanding_minor"], b["invoice_count"]), (2360000, 500000, 1860000, 1))
        self.assertEqual((a["invoiced_minor"], a["collected_minor"], a["outstanding_minor"]), (1000000, 1000000, 0))

    def test_limit(self):
        self.seed_revenue()
        self.assertEqual(len(self.api.get("/api/reports/clients/", {**PERIOD, "limit": 1}).data["currencies"][0]["clients"]), 1)
        for bad in ("0", "101", "abc"):
            self.assertEqual(self.api.get("/api/reports/clients/", {"limit": bad}).status_code, 400, bad)


class TimeReportTests(ReportBase):
    def test_hours_by_project_and_person_and_unbilled_value(self):
        p1 = self.make_project(self.client_a, rate=150000, name="Site")
        p2 = self.make_project(self.client_b, rate=100000, name="App", billable=False)
        e1 = self.add_time(p1["id"], 60, hours_ago=6)
        self.add_time(p1["id"], 30, hours_ago=4)
        self.add_time(p2["id"], 150, hours_ago=3)
        self.add_time(p1["id"], 30, hours_ago=2, api=self.as_role(Role.ACCOUNTANT))
        self.assertEqual(self.work_invoice(self.client_a, [e1["id"]]).status_code, 201)
        data = self.api.get("/api/reports/time/").data
        site = next(p for p in data["projects"] if p["project"] == "Site")
        app = next(p for p in data["projects"] if p["project"] == "App")
        self.assertEqual(data["projects"][0]["project"], "App")                              # most hours first (9000 s)
        self.assertEqual((site["total_seconds"], site["billed_seconds"], site["unbilled_seconds"], site["unbilled_value_minor"]), (7200, 3600, 3600, 150000))
        self.assertEqual((app["total_seconds"], app["billable_seconds"], app["unbilled_value_minor"]), (9000, 0, 0))
        self.assertEqual(data["unbilled_value_by_currency"], [{"currency": "INR", "amount_minor": 150000}])
        people = {p["user_email"]: p["total_seconds"] for p in data["people"]}
        self.assertEqual(people, {self.owner.email: 14400, "accountant@acme.test": 1800})
