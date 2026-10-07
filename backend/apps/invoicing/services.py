"""All invoice business rules live here, so views stay thin and Week 6's Celery jobs reuse them."""
from datetime import timedelta
from decimal import Decimal

from django.db import connection, transaction
from django.db.models import Count, F, Q, Sum
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from apps.billing.limits import enforce_limit
from apps.business.models import BusinessProfile
from apps.crm.models import ActivityKind, ClientStatus
from apps.crm.services import log_activity

from .calculations import (
    default_due_date,
    format_money,
    gst_mode,
    invoice_number,
    invoice_totals,
    line_amounts,
    status_after_payment,
)
from .models import Invoice, InvoiceLine, InvoiceSequence, InvoiceStatus, Payment, TaxRate

DEFAULT_TAX_RATES = [("GST 0%", 0, False), ("GST 5%", 500, False), ("GST 12%", 1200, False),
                     ("GST 18%", 1800, True), ("GST 28%", 2800, False)]


# ---------------- helpers ----------------
def get_profile() -> BusinessProfile:
    tenant_name = getattr(connection.tenant, "name", "") or "My business"
    profile, _ = BusinessProfile.objects.get_or_create(pk=1, defaults={"business_name": tenant_name})
    return profile


def ensure_default_tax_rates():
    """Give a new organization the standard Indian GST slabs the first time it asks for tax rates."""
    if TaxRate.objects.exists():
        return
    for name, bps, is_default in DEFAULT_TAX_RATES:
        TaxRate.objects.create(name=name, rate_bps=bps, is_default=is_default)


def set_default_tax_rate(rate: TaxRate):
    if rate.is_default:
        TaxRate.objects.exclude(pk=rate.pk).filter(is_default=True).update(is_default=False)


def _lock(invoice: Invoice) -> Invoice:
    """Re-read the invoice with a row lock (must be inside transaction.atomic)."""
    return Invoice.objects.select_for_update().get(pk=invoice.pk)


def _resolve_fx(currency, provided_rate, base_currency):
    """Exchange rate rules: same currency as the business -> 1. Otherwise the rate must be given."""
    if currency == base_currency:
        return Decimal("1")
    if provided_rate is None or Decimal(provided_rate) <= 0:
        raise ValidationError(
            {"exchange_rate": f"Required when the invoice currency differs from your business currency ({base_currency})."}
        )
    return Decimal(provided_rate)


def save_lines(invoice: Invoice, lines_data):
    """Replace the invoice's lines and recompute its totals (does not save the invoice itself)."""
    invoice.lines.all().delete()
    objs = []
    for position, data in enumerate(lines_data):
        subtotal, tax, total = line_amounts(data["quantity"], data["unit_price_minor"], data["tax_rate_bps"])
        objs.append(
            InvoiceLine(
                invoice=invoice, position=position, description=data["description"],
                quantity=data["quantity"], unit_price_minor=data["unit_price_minor"],
                tax_name=data["tax_name"], tax_rate_bps=data["tax_rate_bps"],
                subtotal_minor=subtotal, tax_minor=tax, total_minor=total,
            )
        )
    InvoiceLine.objects.bulk_create(objs)
    invoice.subtotal_minor, invoice.tax_minor, invoice.total_minor = invoice_totals(objs)


def address_block(obj) -> dict:
    return {
        "line1": obj.address_line1, "line2": obj.address_line2, "city": obj.city,
        "state": obj.state, "postal_code": obj.postal_code, "country": obj.country,
    }


def build_snapshot(client, profile, currency="INR") -> dict:
    return {
        "from": {
            "name": profile.business_name, "legal_name": profile.legal_name, "email": profile.email,
            "phone": profile.phone, "tax_id": profile.tax_id, **address_block(profile),
        },
        "to": {
            "name": client.name, "company_name": client.company_name, "email": client.email,
            "phone": client.phone, "tax_id": client.tax_id, **address_block(client),
        },
        "gst_mode": gst_mode(profile.state, client.state, currency),
    }


def invoices_sent_this_month() -> int:
    """Invoices that went out since the 1st of this month (UTC). Voided ones still count: they used a number."""
    start = timezone.now().replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    return Invoice.objects.filter(sent_at__gte=start).count()


# ---------------- numbering ----------------
def next_invoice_number(prefix: str) -> str:
    """Call inside transaction.atomic(). Locks the counter row so numbers are unique and consecutive."""
    seq, _ = InvoiceSequence.objects.get_or_create(prefix=prefix)
    seq = InvoiceSequence.objects.select_for_update().get(pk=seq.pk)
    seq.last_number += 1
    seq.save(update_fields=["last_number"])
    return invoice_number(prefix, seq.last_number)


# ---------------- create / edit ----------------
def _require_active_client(client):
    if client.status == ClientStatus.ARCHIVED:
        raise ValidationError({"client": "This client is archived. Restore the client first."})


@transaction.atomic
def create_invoice(actor, client, lines=None, **fields) -> Invoice:
    _require_active_client(client)
    profile = get_profile()
    currency = fields.pop("currency", None) or profile.default_currency
    rate = _resolve_fx(currency, fields.pop("exchange_rate", None), profile.default_currency)
    invoice = Invoice(
        client=client, currency=currency, exchange_rate=rate,
        created_by_id=getattr(actor, "pk", None), created_by_email=getattr(actor, "email", "") or "",
        **fields,
    )
    invoice.save()
    save_lines(invoice, lines or [])
    invoice.save()
    log_activity(
        ActivityKind.INVOICE_CREATED, f"Draft invoice created for {client.name}", actor,
        client=client, metadata={"invoice_id": invoice.pk},
    )
    return invoice


@transaction.atomic
def update_draft(invoice: Invoice, actor, lines=None, **fields) -> Invoice:
    invoice = _lock(invoice)
    if invoice.status != InvoiceStatus.DRAFT:
        raise ValidationError({"detail": "Only draft invoices can be edited. Void it and create a new one instead."})
    client = fields.pop("client", invoice.client)
    if client.pk != invoice.client_id:
        _require_active_client(client)
        invoice.client = client
    profile = get_profile()
    currency = fields.pop("currency", invoice.currency)
    provided_rate = fields.pop("exchange_rate", None if currency != invoice.currency else invoice.exchange_rate)
    invoice.currency = currency
    invoice.exchange_rate = _resolve_fx(currency, provided_rate, profile.default_currency)
    for name, value in fields.items():
        setattr(invoice, name, value)
    if lines is not None:
        save_lines(invoice, lines)
    invoice.save()
    return invoice


# ---------------- status flow ----------------
@transaction.atomic
def send_invoice(invoice: Invoice, actor, issue_date=None, due_date=None) -> Invoice:
    """draft -> sent: assigns the number, fixes the dates and freezes seller/buyer details."""
    invoice = _lock(invoice)
    if invoice.status != InvoiceStatus.DRAFT:
        raise ValidationError({"detail": "Only draft invoices can be sent."})
    if not invoice.lines.exists():
        raise ValidationError({"detail": "Add at least one line item before sending."})
    if invoice.total_minor <= 0:
        raise ValidationError({"detail": "The invoice total must be greater than zero."})
    client = invoice.client
    _require_active_client(client)
    enforce_limit(connection.tenant, "max_invoices_per_month", invoices_sent_this_month())

    issue = issue_date or invoice.issue_date or timezone.localdate()
    due = due_date or invoice.due_date or default_due_date(issue, client.payment_terms_days)
    if due < issue:
        raise ValidationError({"due_date": "The due date cannot be before the issue date."})

    profile = get_profile()
    invoice.number = next_invoice_number(profile.invoice_prefix)
    invoice.issue_date, invoice.due_date = issue, due
    invoice.snapshot = build_snapshot(client, profile, invoice.currency)
    invoice.status = InvoiceStatus.SENT
    invoice.sent_at = timezone.now()
    invoice.save()
    # Week 6: email the PDF to the client here (Celery task).
    log_activity(
        ActivityKind.INVOICE_SENT,
        f"Invoice {invoice.number} sent ({format_money(invoice.total_minor, invoice.currency)}, due {due.isoformat()})",
        actor, client=client, metadata={"invoice_id": invoice.pk, "number": invoice.number},
    )
    return invoice


@transaction.atomic
def void_invoice(invoice: Invoice, actor, reason="") -> Invoice:
    """Cancel a sent invoice that has no payments. The number is kept (never reused)."""
    invoice = _lock(invoice)
    if invoice.status == InvoiceStatus.DRAFT:
        raise ValidationError({"detail": "Drafts have no number yet. Delete the draft instead."})
    if invoice.status == InvoiceStatus.VOID:
        raise ValidationError({"detail": "This invoice is already void."})
    if invoice.amount_paid_minor > 0:
        raise ValidationError({"detail": "This invoice has payments. Delete them first, or record a credit note later."})
    invoice.status = InvoiceStatus.VOID
    invoice.voided_at = timezone.now()
    invoice.void_reason = (reason or "")[:255]
    invoice.save()
    log_activity(
        ActivityKind.INVOICE_VOIDED, f"Invoice {invoice.number} voided", actor,
        client=invoice.client, metadata={"invoice_id": invoice.pk, "reason": invoice.void_reason},
    )
    return invoice


@transaction.atomic
def record_payment(invoice: Invoice, actor, amount_minor, paid_on=None, method="bank_transfer",
                   reference="", notes="") -> Payment:
    invoice = _lock(invoice)
    if invoice.status not in (InvoiceStatus.SENT, InvoiceStatus.PARTIAL):
        raise ValidationError({"detail": "Payments can only be recorded on sent invoices that are not fully paid."})
    balance = invoice.total_minor - invoice.amount_paid_minor
    if amount_minor <= 0:
        raise ValidationError({"amount_minor": "The amount must be greater than zero."})
    if amount_minor > balance:
        raise ValidationError({"amount_minor": f"The amount exceeds the balance due ({format_money(balance, invoice.currency)})."})
    payment = Payment.objects.create(
        invoice=invoice, amount_minor=amount_minor, paid_on=paid_on or timezone.localdate(),
        method=method, reference=reference, notes=notes,
        recorded_by_id=getattr(actor, "pk", None), recorded_by_email=getattr(actor, "email", "") or "",
    )
    invoice.amount_paid_minor += amount_minor
    invoice.status = status_after_payment(invoice.total_minor, invoice.amount_paid_minor)
    if invoice.status == InvoiceStatus.PAID:
        invoice.paid_at = timezone.now()
    invoice.save()
    log_activity(
        ActivityKind.PAYMENT_RECORDED,
        f"Payment of {format_money(amount_minor, invoice.currency)} recorded on {invoice.number}",
        actor, client=invoice.client, metadata={"invoice_id": invoice.pk, "payment_id": payment.pk},
    )
    if invoice.status == InvoiceStatus.PAID:
        log_activity(
            ActivityKind.INVOICE_PAID, f"Invoice {invoice.number} is fully paid", actor,
            client=invoice.client, metadata={"invoice_id": invoice.pk},
        )
    return payment


@transaction.atomic
def delete_payment(payment: Payment, actor) -> Invoice:
    invoice = _lock(payment.invoice)
    amount = payment.amount_minor
    payment.delete()
    invoice.amount_paid_minor -= amount
    invoice.status = status_after_payment(invoice.total_minor, invoice.amount_paid_minor)
    invoice.paid_at = None
    invoice.save()
    log_activity(
        ActivityKind.PAYMENT_DELETED,
        f"Payment of {format_money(amount, invoice.currency)} removed from {invoice.number}",
        actor, client=invoice.client, metadata={"invoice_id": invoice.pk},
    )
    return invoice


# ---------------- dashboard numbers ----------------
def invoice_summary(today=None) -> dict:
    """Per-currency totals (never add INR to USD). Week 5 and the dashboard read this."""
    today = today or timezone.localdate()
    open_invoices = Invoice.objects.filter(status__in=(InvoiceStatus.SENT, InvoiceStatus.PARTIAL))
    overdue = Q(due_date__lt=today)
    balance = F("total_minor") - F("amount_paid_minor")
    currencies = {}

    def bucket(code):
        return currencies.setdefault(
            code,
            {"outstanding_minor": 0, "overdue_minor": 0, "overdue_count": 0, "open_count": 0,
             "paid_this_month_minor": 0},
        )

    for row in open_invoices.values("currency").annotate(
        outstanding=Sum(balance), overdue_total=Sum(balance, filter=overdue),
        overdue_count=Count("id", filter=overdue), open_count=Count("id"),
    ):
        b = bucket(row["currency"])
        b["outstanding_minor"] = row["outstanding"] or 0
        b["overdue_minor"] = row["overdue_total"] or 0
        b["overdue_count"] = row["overdue_count"]
        b["open_count"] = row["open_count"]

    month_start = today.replace(day=1)
    for row in Payment.objects.filter(paid_on__gte=month_start, paid_on__lte=today).values(
        "invoice__currency"
    ).annotate(total=Sum("amount_minor")):
        bucket(row["invoice__currency"])["paid_this_month_minor"] = row["total"] or 0

    return {
        "draft_count": Invoice.objects.filter(status=InvoiceStatus.DRAFT).count(),
        "currencies": currencies,
    }
