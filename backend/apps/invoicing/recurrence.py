"""Pure date maths for recurring invoices (no Django): easy to test, hard to get wrong.

Every occurrence is computed from the ANCHOR date, never from the previous occurrence, so a monthly invoice
anchored on Jan 31 goes Feb 28 -> Mar 31 -> Apr 30 (it never drifts to the 28th for good).
"""
import calendar
from datetime import date, timedelta

FREQUENCIES = {"weekly": None, "monthly": 1, "quarterly": 3, "yearly": 12}


def add_months(day: date, months: int) -> date:
    year, month0 = divmod(day.year * 12 + (day.month - 1) + months, 12)
    month = month0 + 1
    return date(year, month, min(day.day, calendar.monthrange(year, month)[1]))


def occurrence(anchor: date, frequency: str, interval: int, n: int) -> date:
    """The n-th run (0 = the anchor itself)."""
    if frequency not in FREQUENCIES:
        raise ValueError(f"Unknown frequency {frequency!r}")
    if frequency == "weekly":
        return anchor + timedelta(weeks=interval * n)
    return add_months(anchor, FREQUENCIES[frequency] * interval * n)
