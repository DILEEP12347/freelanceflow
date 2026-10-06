"""Pure money and tax maths: no Django, no database, so it is easy to test and to trust.

Rules used everywhere:
- Money is an integer in minor units (paise/cents). Never floats.
- Quantities are Decimals (2.5 hours). Tax rates are integer basis points: 1800 = 18.00%.
- Rounding is half-up, applied once per invoice line. Invoice totals are the sum of the lines,
  so the numbers printed on the PDF always add up exactly.
"""
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal

OPEN_STATUSES = ("sent", "partial")


def round_minor(value) -> int:
    """Round a Decimal amount of minor units to an int, half-up."""
    return int(Decimal(value).quantize(Decimal(1), rounding=ROUND_HALF_UP))


def line_amounts(quantity, unit_price_minor: int, tax_rate_bps: int):
    """-> (subtotal_minor, tax_minor, total_minor) for one invoice line."""
    subtotal = round_minor(Decimal(str(quantity)) * int(unit_price_minor))
    tax = round_minor(Decimal(subtotal) * int(tax_rate_bps) / Decimal(10000))
    return subtotal, tax, subtotal + tax


def invoice_totals(lines):
    """lines: iterable of dicts/objects with subtotal_minor and tax_minor -> (subtotal, tax, total)."""
    subtotal = sum(_get(line, "subtotal_minor") for line in lines)
    tax = sum(_get(line, "tax_minor") for line in lines)
    return subtotal, tax, subtotal + tax


def _get(obj, name):
    return obj[name] if isinstance(obj, dict) else getattr(obj, name)


def tax_summary(lines):
    """Group lines by tax rate: [{name, rate_bps, taxable_minor, tax_minor}], lowest rate first."""
    groups = {}
    for line in lines:
        key = (_get(line, "tax_name"), _get(line, "tax_rate_bps"))
        row = groups.setdefault(
            key, {"name": key[0], "rate_bps": key[1], "taxable_minor": 0, "tax_minor": 0}
        )
        row["taxable_minor"] += _get(line, "subtotal_minor")
        row["tax_minor"] += _get(line, "tax_minor")
    return sorted(groups.values(), key=lambda r: (r["rate_bps"], r["name"]))


def percent_label(bps) -> str:
    """1800 -> '18', 250 -> '2.5'"""
    return format((Decimal(int(bps)) / Decimal(100)).normalize(), "f")


def split_gst(tax_minor: int):
    """Intra-state GST is paid half as CGST, half as SGST. An odd paisa goes to SGST."""
    cgst = tax_minor // 2
    return cgst, tax_minor - cgst


def gst_mode(seller_state, buyer_state, currency="INR"):
    """'intra' (CGST + SGST) when both states are known and equal, 'inter' (IGST) when both are
    known and differ, None when we can't tell or the invoice isn't in INR."""
    if (currency or "").upper() != "INR":
        return None
    seller = (seller_state or "").strip().lower()
    buyer = (buyer_state or "").strip().lower()
    if not seller or not buyer:
        return None
    return "intra" if seller == buyer else "inter"


def tax_display_lines(summary, mode):
    """-> [(label, amount_minor)] for the totals box. Zero-rated groups are skipped."""
    out = []
    for row in summary:
        if row["rate_bps"] == 0 and row["tax_minor"] == 0:
            continue
        if mode == "intra":
            cgst, sgst = split_gst(row["tax_minor"])
            half = percent_label(Decimal(row["rate_bps"]) / 2)
            out.append((f"CGST {half}%", cgst))
            out.append((f"SGST {half}%", sgst))
        elif mode == "inter":
            out.append((f"IGST {percent_label(row['rate_bps'])}%", row["tax_minor"]))
        else:
            out.append((f"Tax {percent_label(row['rate_bps'])}%", row["tax_minor"]))
    return out


def status_after_payment(total_minor: int, paid_minor: int) -> str:
    if paid_minor >= total_minor:
        return "paid"
    return "partial" if paid_minor > 0 else "sent"


def derive_status(status: str, due_date, today: date) -> str:
    """'overdue' is not stored: it is a sent/partial invoice whose due date has passed."""
    if status in OPEN_STATUSES and due_date is not None and due_date < today:
        return "overdue"
    return status


def default_due_date(issue_date: date, payment_terms_days: int) -> date:
    return issue_date + timedelta(days=int(payment_terms_days))


def invoice_number(prefix: str, n: int) -> str:
    return f"{prefix}-{n:04d}"


def _group_indian(n: int) -> str:
    s = str(n)
    if len(s) <= 3:
        return s
    head, tail = s[:-3], s[-3:]
    parts = []
    while len(head) > 2:
        parts.insert(0, head[-2:])
        head = head[:-2]
    if head:
        parts.insert(0, head)
    return ",".join(parts + [tail])


def format_money(minor: int, currency: str = "INR") -> str:
    """5000000 -> 'INR 50,000.00'; 12345678 INR -> 'INR 1,23,456.78' (Indian digit grouping).
    Assumes 2 decimal places, which is right for INR, USD, EUR, GBP (not JPY)."""
    sign = "-" if minor < 0 else ""
    major, cents = divmod(abs(int(minor)), 100)
    grouped = _group_indian(major) if (currency or "").upper() == "INR" else f"{major:,}"
    return f"{sign}{currency} {grouped}.{cents:02d}"


def format_quantity(quantity) -> str:
    s = format(Decimal(str(quantity)), "f")
    if "." in s:
        s = s.rstrip("0").rstrip(".")
    return s or "0"
