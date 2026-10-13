"""Date helpers and aging buckets for the reports (pure)."""
import csv
import io
from datetime import date, timedelta

BUCKETS = ["not_due", "d1_30", "d31_60", "d61_90", "d90_plus"]
MAX_DAYS = 1830  # about five years


def default_period(today: date):
    """The last 12 months: from the 1st of the month eleven months ago, to today."""
    total = today.year * 12 + (today.month - 1) - 11
    return date(total // 12, total % 12 + 1, 1), today


def month_keys(start: date, end: date):
    """['2026-01', '2026-02', ...] from start's month through end's month."""
    keys, year, month = [], start.year, start.month
    while (year, month) <= (end.year, end.month):
        keys.append(f"{year:04d}-{month:02d}")
        year, month = (year + 1, 1) if month == 12 else (year, month + 1)
    return keys


def month_key(day: date) -> str:
    return f"{day.year:04d}-{day.month:02d}"


def bucket_for(days_overdue: int) -> str:
    if days_overdue <= 0:
        return "not_due"
    if days_overdue <= 30:
        return "d1_30"
    if days_overdue <= 60:
        return "d31_60"
    if days_overdue <= 90:
        return "d61_90"
    return "d90_plus"


def validate_period(start: date, end: date):
    """-> an error message, or None."""
    if end < start:
        return "'from' must not be after 'to'."
    if (end - start).days > MAX_DAYS:
        return "The period can be at most 5 years."
    return None


def csv_safe(value):
    """Spreadsheet formula injection: a text cell starting with = + - @ would be run as a formula by Excel."""
    if isinstance(value, str) and value[:1] in ("=", "+", "-", "@", "\t", "\r"):
        return "'" + value
    return value


def to_csv(columns, rows) -> str:
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(columns)
    for row in rows:
        writer.writerow([csv_safe(cell) for cell in row])
    return buffer.getvalue()
