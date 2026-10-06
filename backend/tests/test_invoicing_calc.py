"""Pure maths tests: no database, they run in milliseconds."""
from datetime import date
from decimal import Decimal

from django.test import SimpleTestCase

from apps.invoicing import calculations as c


class MoneyMathTests(SimpleTestCase):
    def test_line_amounts(self):
        self.assertEqual(c.line_amounts(2, 1000000, 1800), (2000000, 360000, 2360000))
        self.assertEqual(c.line_amounts("2.5", 350000, 1800), (875000, 157500, 1032500))
        self.assertEqual(c.line_amounts(Decimal("0.01"), 100, 0), (1, 0, 1))

    def test_rounding_is_half_up_per_line(self):
        self.assertEqual(c.line_amounts("1.5", 3333, 0)[0], 5000)      # 4999.5 -> 5000
        self.assertEqual(c.line_amounts(1, 5, 1250)[1], 1)             # 0.625 -> 1
        self.assertEqual(c.line_amounts(1, 10, 1250)[1], 1)            # 1.25 -> 1

    def test_totals_and_tax_summary(self):
        lines = [
            {"tax_name": "GST 18%", "tax_rate_bps": 1800, "subtotal_minor": 1000, "tax_minor": 180},
            {"tax_name": "GST 18%", "tax_rate_bps": 1800, "subtotal_minor": 500, "tax_minor": 90},
            {"tax_name": "GST 5%", "tax_rate_bps": 500, "subtotal_minor": 200, "tax_minor": 10},
            {"tax_name": "", "tax_rate_bps": 0, "subtotal_minor": 50, "tax_minor": 0},
        ]
        self.assertEqual(c.invoice_totals(lines), (1750, 280, 2030))
        summary = c.tax_summary(lines)
        self.assertEqual([r["rate_bps"] for r in summary], [0, 500, 1800])
        self.assertEqual(summary[2]["taxable_minor"], 1500)
        self.assertEqual(summary[2]["tax_minor"], 270)

    def test_gst_split_and_labels(self):
        self.assertEqual(c.split_gst(361), (180, 181))
        summary = [{"name": "GST 18%", "rate_bps": 1800, "taxable_minor": 1500, "tax_minor": 270},
                   {"name": "GST 0%", "rate_bps": 0, "taxable_minor": 50, "tax_minor": 0}]
        self.assertEqual(c.tax_display_lines(summary, "intra"), [("CGST 9%", 135), ("SGST 9%", 135)])
        self.assertEqual(c.tax_display_lines(summary, "inter"), [("IGST 18%", 270)])
        self.assertEqual(c.tax_display_lines(summary, None), [("Tax 18%", 270)])
        half_rate = [{"name": "GST 5%", "rate_bps": 500, "taxable_minor": 200, "tax_minor": 10}]
        self.assertEqual(c.tax_display_lines(half_rate, "intra")[0][0], "CGST 2.5%")

    def test_gst_mode(self):
        self.assertEqual(c.gst_mode("Karnataka", " karnataka "), "intra")
        self.assertEqual(c.gst_mode("Karnataka", "Maharashtra"), "inter")
        self.assertIsNone(c.gst_mode("Karnataka", ""))
        self.assertIsNone(c.gst_mode("Karnataka", "Karnataka", "USD"))


class StatusAndFormatTests(SimpleTestCase):
    def test_status_after_payment(self):
        self.assertEqual(c.status_after_payment(100, 0), "sent")
        self.assertEqual(c.status_after_payment(100, 40), "partial")
        self.assertEqual(c.status_after_payment(100, 100), "paid")

    def test_overdue_is_derived(self):
        today = date(2026, 10, 6)
        self.assertEqual(c.derive_status("sent", date(2026, 10, 5), today), "overdue")
        self.assertEqual(c.derive_status("partial", date(2026, 10, 5), today), "overdue")
        self.assertEqual(c.derive_status("sent", today, today), "sent")      # due today is not overdue yet
        self.assertEqual(c.derive_status("paid", date(2026, 1, 1), today), "paid")
        self.assertEqual(c.derive_status("draft", date(2026, 1, 1), today), "draft")

    def test_dates_and_numbers(self):
        self.assertEqual(c.default_due_date(date(2026, 10, 6), 30), date(2026, 11, 5))
        self.assertEqual(c.invoice_number("INV", 7), "INV-0007")
        self.assertEqual(c.invoice_number("INV", 12345), "INV-12345")

    def test_money_formatting(self):
        self.assertEqual(c.format_money(5000000), "INR 50,000.00")
        self.assertEqual(c.format_money(12345678), "INR 1,23,456.78")
        self.assertEqual(c.format_money(123456789012), "INR 1,23,45,67,890.12")
        self.assertEqual(c.format_money(99), "INR 0.99")
        self.assertEqual(c.format_money(100000000, "USD"), "USD 1,000,000.00")

    def test_quantity_formatting(self):
        self.assertEqual(c.format_quantity(Decimal("2.00")), "2")
        self.assertEqual(c.format_quantity(Decimal("1.50")), "1.5")
        self.assertEqual(c.format_quantity(100), "100")
