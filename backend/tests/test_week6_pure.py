"""No database: recurring-date maths and the reminder picker."""
from datetime import date

from django.test import SimpleTestCase

from apps.invoicing.recurrence import add_months, occurrence
from apps.invoicing.reminder_logic import pick_reminder

OFFSETS = [-3, 1, 7, 14]


class RecurrenceTests(SimpleTestCase):
    def test_month_end_anchor_never_drifts(self):
        anchor = date(2026, 1, 31)
        got = [occurrence(anchor, "monthly", 1, n) for n in range(5)]
        self.assertEqual(got, [date(2026, 1, 31), date(2026, 2, 28), date(2026, 3, 31), date(2026, 4, 30), date(2026, 5, 31)])

    def test_leap_year_and_other_frequencies(self):
        self.assertEqual(occurrence(date(2028, 1, 31), "monthly", 1, 1), date(2028, 2, 29))
        self.assertEqual(occurrence(date(2026, 10, 7), "weekly", 2, 3), date(2026, 11, 18))
        self.assertEqual(occurrence(date(2026, 11, 30), "quarterly", 1, 1), date(2027, 2, 28))
        self.assertEqual(occurrence(date(2026, 10, 7), "yearly", 1, 2), date(2028, 10, 7))
        self.assertEqual(occurrence(date(2026, 10, 7), "monthly", 3, 1), date(2027, 1, 7))
        self.assertEqual(occurrence(date(2026, 10, 7), "monthly", 1, 0), date(2026, 10, 7))

    def test_add_months_across_years_and_unknown_frequency(self):
        self.assertEqual(add_months(date(2026, 12, 15), 1), date(2027, 1, 15))
        self.assertEqual(add_months(date(2026, 1, 15), -2), date(2025, 11, 15))
        with self.assertRaises(ValueError):
            occurrence(date(2026, 1, 1), "daily", 1, 1)


class ReminderPickerTests(SimpleTestCase):
    def test_nothing_before_the_first_offset(self):
        self.assertEqual(pick_reminder(-5, OFFSETS, []), (None, []))

    def test_each_offset_fires_once(self):
        self.assertEqual(pick_reminder(-3, OFFSETS, []), (-3, []))
        self.assertEqual(pick_reminder(-1, OFFSETS, [-3]), (None, []))
        self.assertEqual(pick_reminder(0, OFFSETS, [-3]), (None, []))
        self.assertEqual(pick_reminder(1, OFFSETS, [-3]), (1, []))
        self.assertEqual(pick_reminder(8, OFFSETS, [-3, 1]), (7, []))
        self.assertEqual(pick_reminder(21, OFFSETS, [-3, 1, 7, 14]), (None, []))

    def test_a_long_overdue_invoice_gets_one_email_and_the_rest_are_skipped(self):
        self.assertEqual(pick_reminder(20, OFFSETS, []), (14, [-3, 1, 7]))

    def test_no_offsets_means_no_reminders(self):
        self.assertEqual(pick_reminder(30, [], []), (None, []))
