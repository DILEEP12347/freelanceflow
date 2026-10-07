"""Billing lives in the PUBLIC schema (shared app): Stripe webhooks arrive on the bare domain with no
tenant, and the super-admin dashboard (Week 7) needs every organization's subscription in one place.
Tenant-schema code can still read these tables, because tenant search paths include `public`."""
from django.db import models
from django.utils import timezone

FREE_PLAN_CODE = "free"


class Plan(models.Model):
    """Plans and their limits live in the database, so you can change them without redeploying.
    A limit that is missing or null means 'unlimited'."""

    code = models.SlugField(max_length=30, unique=True)  # free / pro / business
    name = models.CharField(max_length=60)
    description = models.CharField(max_length=200, blank=True)
    # Stripe Price id (price_...). Optional here: STRIPE_PRICE_PRO / STRIPE_PRICE_BUSINESS env vars also work.
    stripe_price_id = models.CharField(max_length=100, blank=True)
    price_minor = models.BigIntegerField(default=0)  # for DISPLAY only: the real price lives in Stripe
    currency = models.CharField(max_length=3, default="INR")
    interval = models.CharField(max_length=10, default="month")
    trial_days = models.PositiveSmallIntegerField(default=0)
    limits = models.JSONField(default=dict, blank=True)    # {"max_active_clients": 5, ...}
    features = models.JSONField(default=dict, blank=True)  # {"recurring_invoices": true, ...}
    is_active = models.BooleanField(default=True)
    sort_order = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ["sort_order", "id"]

    def __str__(self):
        return self.name

    @property
    def is_free(self) -> bool:
        return self.code == FREE_PLAN_CODE


class SubscriptionStatus(models.TextChoices):
    ACTIVE = "active", "Active"
    TRIALING = "trialing", "Trialing"
    PAST_DUE = "past_due", "Past due"
    CANCELED = "canceled", "Canceled"
    UNPAID = "unpaid", "Unpaid"
    INCOMPLETE = "incomplete", "Incomplete"


class Subscription(models.Model):
    """One per organization. Every organization has one: Free until it pays.
    Stripe (through webhooks) is the source of truth for everything except `plan` of free tenants."""

    tenant = models.OneToOneField("tenants.Tenant", on_delete=models.CASCADE, related_name="subscription")
    plan = models.ForeignKey(Plan, on_delete=models.PROTECT, related_name="subscriptions")
    status = models.CharField(max_length=30, default=SubscriptionStatus.ACTIVE)  # Stripe's raw status strings

    stripe_customer_id = models.CharField(max_length=100, blank=True, db_index=True)
    stripe_subscription_id = models.CharField(max_length=100, blank=True, db_index=True)
    current_period_end = models.DateTimeField(null=True, blank=True)
    trial_end = models.DateTimeField(null=True, blank=True)
    trial_used = models.BooleanField(default=False)  # one free trial per organization
    cancel_at_period_end = models.BooleanField(default=False)
    # After a failed payment the paid plan keeps working until this moment, then access drops to Free.
    grace_until = models.DateTimeField(null=True, blank=True)
    last_payment_at = models.DateTimeField(null=True, blank=True)
    # Unix time of the newest Stripe event applied: older events that arrive late are ignored.
    last_event_ts = models.BigIntegerField(default=0)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.tenant} -> {self.plan.code} ({self.status})"

    def is_entitled(self, now=None) -> bool:
        """Does this organization currently get the features of `self.plan`?"""
        if self.plan.is_free:
            return True
        now = now or timezone.now()
        if self.status in (SubscriptionStatus.ACTIVE, SubscriptionStatus.TRIALING):
            return True
        if self.status == SubscriptionStatus.PAST_DUE:
            return self.grace_until is None or self.grace_until > now
        return False  # canceled, unpaid, incomplete: back to Free


class StripeEvent(models.Model):
    """Every webhook event we have processed. The unique event_id is what makes processing idempotent:
    Stripe retries deliveries, and the same event must never be applied twice."""

    event_id = models.CharField(max_length=255, unique=True)
    type = models.CharField(max_length=100)
    customer_id = models.CharField(max_length=100, blank=True)
    created_ts = models.BigIntegerField(default=0)  # Stripe's `created` (unix time)
    processed_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-processed_at", "-id"]
