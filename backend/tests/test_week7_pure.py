"""No database: audit rules, redaction, hours maths and report helpers."""
import json
from datetime import date
from decimal import Decimal

from django.test import SimpleTestCase

from apps.audit.redact import sanitize_body
from apps.audit.rules import classify
from apps.reports import periods
from apps.work import hours


class AuditRuleTests(SimpleTestCase):
    def test_known_actions(self):
        self.assertEqual(classify("POST", "/api/invoices/12/void/"),
                         {"action": "invoice.voided", "object_type": "invoice", "object_id": "12", "parent_id": ""})
        self.assertEqual(classify("DELETE", "/api/invoices/12/payments/5/"),
                         {"action": "payment.deleted", "object_type": "payment", "object_id": "5", "parent_id": "12"})
        self.assertEqual(classify("PATCH", "/api/team/members/3/")["action"], "member.role_changed")
        self.assertEqual(classify("POST", "/api/team/invites/")["action"], "member.invited")
        self.assertEqual(classify("POST", "/api/team/invites/accept/")["action"], "member.joined")  # not an invite
        self.assertEqual(classify("POST", "/api/auth/login/")["action"], "auth.login")
        self.assertEqual(classify("POST", "/api/clients/")["action"], "client.created")
        self.assertEqual(classify("DELETE", "/api/clients/7/")["object_id"], "7")
        self.assertEqual(classify("POST", "/api/time/start/")["action"], "time.started")
        self.assertEqual(classify("POST", "/api/time/9/stop/")["action"], "time.stopped")
        self.assertEqual(classify("POST", "/api/time/")["action"], "time_entry.created")
        self.assertEqual(classify("POST", "/api/work/invoice/")["action"], "work.invoiced")

    def test_unknown_calls_are_still_named(self):
        self.assertEqual(classify("POST", "/api/invoices/3/mystery/")["action"], "post:invoices/<id>/mystery")
        self.assertEqual(classify("DELETE", "/api/widgets/")["action"], "delete:widgets")


class RedactionTests(SimpleTestCase):
    def test_secrets_are_removed_and_size_is_bounded(self):
        body = json.dumps({"email": "a@b.test", "password": "hunter2", "API_KEY": "x", "nested": {"refresh_token": "t", "ok": 1},
                           "lines": [{"d": 1}] * 30, "note": "x" * 500}).encode()
        out = sanitize_body(body)
        self.assertEqual((out["password"], out["API_KEY"], out["nested"]["refresh_token"]), ("[redacted]",) * 3)
        self.assertEqual((out["email"], out["nested"]["ok"], len(out["lines"]), len(out["note"])), ("a@b.test", 1, 20, 201))

    def test_bad_bodies_give_nothing(self):
        for raw in (b"", b"not json", b"[1, 2]", b"\xff\xfe"):
            self.assertEqual(sanitize_body(raw), {})

    def test_huge_bodies_keep_only_their_keys(self):
        out = sanitize_body(json.dumps({f"k{i}": "y" * 190 for i in range(30)}).encode())
        self.assertTrue(out["_truncated"])


class HoursTests(SimpleTestCase):
    def test_value(self):
        self.assertEqual(hours.value_minor(3600, 150000), 150000)
        self.assertEqual(hours.value_minor(5400, 150000), 225000)
        self.assertEqual(hours.value_minor(1, 1800), 1)           # 0.5 rounds half up
        self.assertEqual(hours.value_minor(100, 100000), 2778)    # 2777.78
        self.assertEqual(hours.value_from_product(5400 * 150000), 225000)

    def test_quantity_and_labels(self):
        self.assertEqual(hours.hours_quantity(5400), Decimal("1.50"))
        self.assertEqual(hours.hours_quantity(0), Decimal("0.01"))      # a line always has a quantity
        self.assertEqual(hours.hours_label(5400), "1.50")
        self.assertEqual(hours.hours_label(0), "0.00")


class ReportPeriodTests(SimpleTestCase):
    def test_default_period_is_twelve_months(self):
        self.assertEqual(periods.default_period(date(2026, 10, 9)), (date(2025, 11, 1), date(2026, 10, 9)))
        self.assertEqual(periods.default_period(date(2026, 12, 1)), (date(2026, 1, 1), date(2026, 12, 1)))
        self.assertEqual(len(periods.month_keys(*periods.default_period(date(2026, 10, 9)))), 12)

    def test_month_keys(self):
        self.assertEqual(periods.month_keys(date(2025, 11, 20), date(2026, 2, 3)), ["2025-11", "2025-12", "2026-01", "2026-02"])

    def test_aging_buckets(self):
        got = [periods.bucket_for(d) for d in (-5, 0, 1, 30, 31, 60, 61, 90, 91)]
        self.assertEqual(got, ["not_due", "not_due", "d1_30", "d1_30", "d31_60", "d31_60", "d61_90", "d61_90", "d90_plus"])

    def test_period_validation(self):
        self.assertIsNotNone(periods.validate_period(date(2026, 2, 1), date(2026, 1, 1)))
        self.assertIsNotNone(periods.validate_period(date(2020, 1, 1), date(2026, 1, 1)))
        self.assertIsNone(periods.validate_period(date(2026, 1, 1), date(2026, 1, 1)))

    def test_csv_blocks_formula_injection_but_keeps_numbers(self):
        out = periods.to_csv(["name", "amount"], [["=SUM(A1)", 5], ["+1", -250], ["Acme, Inc", 3], ["ok", 0]]).splitlines()
        self.assertEqual(out[1:], ["'=SUM(A1),5", "'+1,-250", '"Acme, Inc",3', "ok,0"])
