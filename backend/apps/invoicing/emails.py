"""Building and sending invoice and reminder emails (PDF attached)."""
import logging

from django.core.mail import EmailMessage
from django.utils import timezone

from apps.billing.limits import has_feature
from apps.crm.models import ActivityKind
from apps.crm.services import log_activity
from django.db import connection

from . import services
from .calculations import format_money
from .models import Invoice, InvoiceSettings, InvoiceStatus
from .pdf import render_invoice_pdf
from .timeutils import business_today

logger = logging.getLogger(__name__)


def recipient_for(invoice) -> str:
    """Primary contact's email, else any contact with one, else the client's own email, else ''."""
    client = invoice.client
    contacts = client.contacts.exclude(email="").order_by("-is_primary", "id")
    first = contacts.first()
    return first.email if first else (client.email or "")


def _due_phrase(invoice) -> str:
    delta = (business_today() - invoice.due_date).days if invoice.due_date else 0
    if delta < 0:
        return f"is due in {-delta} day{'s' if delta != -1 else ''}"
    if delta == 0:
        return "is due today"
    return f"is overdue by {delta} day{'s' if delta != 1 else ''}"


def compose(invoice, kind, portal_link, signature, message="", seller_name=""):
    """-> (subject, body)"""
    number = invoice.number
    balance = format_money(invoice.balance_due_minor, invoice.currency)
    due = invoice.due_date.strftime("%d %b %Y") if invoice.due_date else "-"
    to = invoice.snapshot.get("to", {}) if invoice.snapshot else {}
    greeting = f"Hi {to.get('name') or invoice.client.name},"
    if kind == "reminder":
        subject = f"Reminder: invoice {number} {_due_phrase(invoice)}"
        intro = f"This is a friendly reminder that invoice {number} {_due_phrase(invoice)}."
    else:
        subject = f"Invoice {number} from {seller_name}"
        intro = f"Please find your invoice from {seller_name} attached."
    lines = [greeting, "", intro, "", f"Invoice: {number}", f"Amount due: {balance}", f"Due date: {due}"]
    if portal_link:
        lines += ["", f"View it online: {portal_link}"]
    if message:
        lines += ["", message]
    lines += ["", "The invoice is attached as a PDF.", "", signature or seller_name]
    return subject, "\n".join(lines)


def deliver_invoice_email(invoice_id, kind="invoice", offset_days=None, message="") -> str:
    """Runs inside the organization's schema (the Celery task sets that up). Returns what happened."""
    invoice = (
        Invoice.objects.select_related("client").prefetch_related("lines", "payments").filter(pk=invoice_id).first()
    )
    if invoice is None or invoice.status not in (InvoiceStatus.SENT, InvoiceStatus.PARTIAL):
        return "skipped"  # paid, voided or deleted since it was queued: nothing to chase
    to = recipient_for(invoice)
    if not to:
        return "no_recipient"

    profile = services.get_profile()
    link = services.portal_url(invoice) if has_feature(connection.tenant, "client_portal") else ""
    subject, body = compose(
        invoice, kind, link, InvoiceSettings.load().email_signature, message, seller_name=profile.business_name
    )
    email = EmailMessage(subject, body, to=[to], reply_to=[profile.email] if profile.email else None)
    email.attach(f"{invoice.number}.pdf", render_invoice_pdf(invoice), "application/pdf")
    email.send()

    if kind == "reminder":
        log_activity(
            ActivityKind.REMINDER_SENT, f"Reminder for {invoice.number} emailed to {to}", None,
            client=invoice.client, metadata={"invoice_id": invoice.pk, "offset_days": offset_days},
        )
    else:
        Invoice.objects.filter(pk=invoice.pk).update(emailed_at=timezone.now())
        log_activity(
            ActivityKind.INVOICE_EMAILED, f"Invoice {invoice.number} emailed to {to}", None,
            client=invoice.client, metadata={"invoice_id": invoice.pk},
        )
    return "sent"
