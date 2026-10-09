from datetime import date
from decimal import Decimal

from django.db import models
from django.utils import timezone

from .calculations import OPEN_STATUSES, derive_status
from .timeutils import business_today, clear_cache


class TaxRate(models.Model):
    """A preset the invoice editor offers (GST 18% ...). Lines COPY the name and rate when they are
    saved, so editing or deleting a TaxRate never changes an invoice that already exists."""

    name = models.CharField(max_length=50, unique=True)
    rate_bps = models.PositiveIntegerField()  # basis points: 1800 = 18.00%
    is_default = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["rate_bps", "name"]

    def __str__(self):
        return self.name


class InvoiceStatus(models.TextChoices):
    DRAFT = "draft", "Draft"
    SENT = "sent", "Sent"
    PARTIAL = "partial", "Partially paid"
    PAID = "paid", "Paid"
    VOID = "void", "Void"
    # "overdue" is NOT stored. It is derived: a sent/partial invoice whose due date has passed.


class InvoiceSequence(models.Model):
    """Gap-free counter, one row per invoice prefix. Locked with SELECT ... FOR UPDATE when a
    number is handed out, so two invoices sent at the same moment can never share a number."""

    prefix = models.CharField(max_length=10, unique=True)
    last_number = models.PositiveIntegerField(default=0)


class Invoice(models.Model):
    client = models.ForeignKey("crm.Client", on_delete=models.PROTECT, related_name="invoices")
    # Drafts have no number. It is assigned when the invoice is sent, so numbers stay consecutive
    # (tax authorities expect that) even if you delete drafts.
    number = models.CharField(max_length=30, null=True, blank=True, unique=True)
    status = models.CharField(max_length=20, choices=InvoiceStatus.choices, default=InvoiceStatus.DRAFT)

    issue_date = models.DateField(null=True, blank=True)
    due_date = models.DateField(null=True, blank=True)

    currency = models.CharField(max_length=3, default="INR")
    # How many units of the business's base currency one unit of `currency` was worth. 1 when equal.
    exchange_rate = models.DecimalField(max_digits=18, decimal_places=6, default=Decimal("1"))

    subtotal_minor = models.BigIntegerField(default=0)
    tax_minor = models.BigIntegerField(default=0)
    total_minor = models.BigIntegerField(default=0)
    amount_paid_minor = models.BigIntegerField(default=0)

    notes = models.TextField(blank=True)
    terms = models.TextField(blank=True)
    void_reason = models.CharField(max_length=255, blank=True)
    # Seller and buyer details frozen at the moment the invoice is sent.
    snapshot = models.JSONField(default=dict, blank=True)

    # Client portal: the secret in the public link /portal/<token>/. Set when the invoice is sent.
    portal_token = models.CharField(max_length=64, null=True, blank=True, unique=True)
    viewed_at = models.DateTimeField(null=True, blank=True)   # first time the client opened the portal link
    emailed_at = models.DateTimeField(null=True, blank=True)  # last time the invoice was emailed
    recurring = models.ForeignKey(
        "invoicing.RecurringInvoice", null=True, blank=True, on_delete=models.SET_NULL, related_name="invoices"
    )

    created_by_id = models.BigIntegerField(null=True, blank=True)
    created_by_email = models.EmailField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    sent_at = models.DateTimeField(null=True, blank=True)
    paid_at = models.DateTimeField(null=True, blank=True)
    voided_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        indexes = [models.Index(fields=["status", "due_date"])]

    def __str__(self):
        return self.number or f"Draft #{self.pk}"

    @property
    def balance_due_minor(self) -> int:
        if self.status == InvoiceStatus.VOID:
            return 0
        return self.total_minor - self.amount_paid_minor

    @property
    def display_status(self) -> str:
        return derive_status(self.status, self.due_date, business_today())

    @property
    def is_overdue(self) -> bool:
        return self.display_status == "overdue"

    @property
    def is_open(self) -> bool:
        return self.status in OPEN_STATUSES


class InvoiceLine(models.Model):
    invoice = models.ForeignKey(Invoice, on_delete=models.CASCADE, related_name="lines")
    position = models.PositiveIntegerField(default=0)
    description = models.CharField(max_length=500)
    quantity = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal("1"))
    unit_price_minor = models.BigIntegerField()
    tax_name = models.CharField(max_length=50, blank=True)
    tax_rate_bps = models.PositiveIntegerField(default=0)
    subtotal_minor = models.BigIntegerField(default=0)
    tax_minor = models.BigIntegerField(default=0)
    total_minor = models.BigIntegerField(default=0)

    class Meta:
        ordering = ["position", "id"]


class PaymentMethod(models.TextChoices):
    BANK_TRANSFER = "bank_transfer", "Bank transfer"
    UPI = "upi", "UPI"
    CARD = "card", "Card"
    CASH = "cash", "Cash"
    CHEQUE = "cheque", "Cheque"
    OTHER = "other", "Other"


class Payment(models.Model):
    """A payment recorded against an invoice (manually for now; Week 5+ can add Stripe)."""

    invoice = models.ForeignKey(Invoice, on_delete=models.CASCADE, related_name="payments")
    amount_minor = models.BigIntegerField()
    paid_on = models.DateField(default=date.today)
    method = models.CharField(max_length=20, choices=PaymentMethod.choices, default=PaymentMethod.BANK_TRANSFER)
    reference = models.CharField(max_length=100, blank=True)
    notes = models.CharField(max_length=255, blank=True)
    recorded_by_id = models.BigIntegerField(null=True, blank=True)
    recorded_by_email = models.EmailField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-paid_on", "-id"]


def default_reminder_offsets():
    return [-3, 1, 7, 14]


class InvoiceSettings(models.Model):
    """One row per organization (always pk=1): how its invoicing behaves."""

    reminders_enabled = models.BooleanField(default=True)
    # Days relative to the due date: -3 = three days before, 1 = one day after, 14 = two weeks after.
    reminder_offsets = models.JSONField(default=default_reminder_offsets)
    timezone = models.CharField(max_length=64, default="UTC")  # e.g. "Asia/Kolkata"
    email_signature = models.CharField(max_length=500, blank=True)
    last_daily_run_on = models.DateField(null=True, blank=True)  # the daily jobs ran for this local date

    @classmethod
    def load(cls):
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        clear_cache()


class InvoiceReminder(models.Model):
    """A reminder that was claimed for an invoice. The unique (invoice, offset) pair is what guarantees the
    same reminder is never sent twice, even if the daily job runs twice."""

    invoice = models.ForeignKey(Invoice, on_delete=models.CASCADE, related_name="reminders")
    offset_days = models.IntegerField()
    skipped = models.BooleanField(default=False)  # too old to be useful: recorded but not emailed
    to_email = models.EmailField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["invoice", "offset_days"], name="uniq_reminder_per_offset")]


class RecurringFrequency(models.TextChoices):
    WEEKLY = "weekly", "Weekly"
    MONTHLY = "monthly", "Monthly"
    QUARTERLY = "quarterly", "Quarterly"
    YEARLY = "yearly", "Yearly"


class RecurringStatus(models.TextChoices):
    ACTIVE = "active", "Active"
    PAUSED = "paused", "Paused"
    ENDED = "ended", "Ended"


class RecurringInvoice(models.Model):
    """A template that creates a new invoice on a schedule (Pro plan feature)."""

    name = models.CharField(max_length=120)
    client = models.ForeignKey("crm.Client", on_delete=models.PROTECT, related_name="recurring_invoices")
    frequency = models.CharField(max_length=20, choices=RecurringFrequency.choices, default=RecurringFrequency.MONTHLY)
    interval = models.PositiveSmallIntegerField(default=1)  # every N weeks/months/...
    start_date = models.DateField()
    # Occurrences are counted from the anchor. It equals start_date, and moves to the resume date when a long
    # pause would otherwise make the schedule "catch up" with a pile of back-dated invoices.
    anchor_date = models.DateField()
    anchor_runs = models.PositiveIntegerField(default=0)
    next_run_date = models.DateField(db_index=True)
    end_date = models.DateField(null=True, blank=True)
    max_runs = models.PositiveIntegerField(null=True, blank=True)
    runs_count = models.PositiveIntegerField(default=0)
    status = models.CharField(max_length=10, choices=RecurringStatus.choices, default=RecurringStatus.ACTIVE)
    auto_send = models.BooleanField(default=False)  # send (and email) each invoice instead of leaving a draft

    currency = models.CharField(max_length=3, default="INR")
    exchange_rate = models.DecimalField(max_digits=18, decimal_places=6, default=Decimal("1"))
    # [{"description", "quantity" (string), "unit_price_minor", "tax_name", "tax_rate_bps"}]: tax is frozen here,
    # so changing a TaxRate later never changes what a running schedule bills.
    lines = models.JSONField(default=list)
    notes = models.TextField(blank=True)
    terms = models.TextField(blank=True)

    last_run_at = models.DateTimeField(null=True, blank=True)
    last_error = models.CharField(max_length=255, blank=True)
    created_by_email = models.EmailField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["next_run_date", "id"]
        indexes = [models.Index(fields=["status", "next_run_date"])]

    def __str__(self):
        return self.name
