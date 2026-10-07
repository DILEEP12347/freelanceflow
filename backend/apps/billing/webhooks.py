"""Stripe webhook processing: the ONLY code path that changes a subscription's plan or status.

Guarantees:
- Authentic: the signature is verified before anything is read (see signatures.py).
- Idempotent: each event id is stored in the same transaction as its effects. A retry of an event we
  already processed is acknowledged and ignored. If processing fails, the whole transaction rolls back
  (including the stored id) and we answer 500, so Stripe retries later.
- Order-safe: Stripe does not promise delivery order, so an event older than the newest one already
  applied is ignored (Subscription.last_event_ts).
"""
import logging
from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from .models import StripeEvent, Subscription, SubscriptionStatus
from .plans import get_free_plan, plan_for_price_id
from .stripe_payloads import extract_period_end, extract_price_id, extract_tenant_id, id_of, ts_to_dt

logger = logging.getLogger(__name__)

ENDED = (SubscriptionStatus.CANCELED, "incomplete_expired")


def _find_subscription(obj):
    """Which organization is this about? By Stripe customer id first (we created and stored it), then by
    the tenant id we put in metadata / client_reference_id."""
    qs = Subscription.objects.select_for_update(of=("self",)).select_related("plan")
    customer = id_of(obj.get("customer"))
    sub = qs.filter(stripe_customer_id=customer).first() if customer else None
    if sub is None:
        tenant_id = extract_tenant_id(obj)
        sub = qs.filter(tenant_id=tenant_id).first() if tenant_id else None
    return sub


def _grace_deadline(ts):
    start = ts_to_dt(ts) or timezone.now()
    return start + timedelta(days=settings.BILLING_GRACE_DAYS)


def _stale(sub, ts) -> bool:
    return ts < sub.last_event_ts


def _on_checkout_completed(event, obj):
    """The customer finished Checkout. Just link the ids: plan and status arrive with the subscription events."""
    if obj.get("mode") not in (None, "subscription"):
        return
    sub = _find_subscription(obj)
    if sub is None:
        logger.warning("checkout.session.completed %s: no matching organization", event["id"])
        return
    sub.stripe_customer_id = sub.stripe_customer_id or id_of(obj.get("customer"))
    sub.stripe_subscription_id = id_of(obj.get("subscription")) or sub.stripe_subscription_id
    sub.save()


def _on_subscription(event, obj):
    sub = _find_subscription(obj)
    if sub is None:
        logger.warning("%s %s: no matching organization", event["type"], event["id"])
        return
    ts = int(event.get("created") or 0)
    if _stale(sub, ts):
        return
    status = obj.get("status") or sub.status
    ended = event["type"] == "customer.subscription.deleted" or status in ENDED

    sub.stripe_customer_id = sub.stripe_customer_id or id_of(obj.get("customer"))
    if ended:
        sub.status = SubscriptionStatus.CANCELED
        sub.plan = get_free_plan()
        sub.stripe_subscription_id = ""
        sub.cancel_at_period_end = False
        sub.grace_until = sub.current_period_end = sub.trial_end = None
    else:
        sub.status = status
        sub.stripe_subscription_id = obj.get("id") or sub.stripe_subscription_id
        price_id = extract_price_id(obj)
        plan = plan_for_price_id(price_id)
        if plan:
            sub.plan = plan
        elif price_id:
            logger.warning("Unknown Stripe price %s: plan left unchanged for tenant %s", price_id, sub.tenant_id)
        sub.current_period_end = extract_period_end(obj)
        sub.trial_end = ts_to_dt(obj.get("trial_end"))
        # One free trial per organization: any real subscription (trialing OR paying) uses it up, so cancelling
        # and re-subscribing never gives a second trial. 'incomplete' means the first payment has not gone through yet.
        if sub.trial_end or status != SubscriptionStatus.INCOMPLETE:
            sub.trial_used = True
        sub.cancel_at_period_end = bool(obj.get("cancel_at_period_end"))
        if status in (SubscriptionStatus.ACTIVE, SubscriptionStatus.TRIALING):
            sub.grace_until = None
        elif status == SubscriptionStatus.PAST_DUE and sub.grace_until is None:
            sub.grace_until = _grace_deadline(ts)
    sub.last_event_ts = ts
    sub.save()


def _on_invoice_paid(event, obj):
    sub = _find_subscription(obj)
    ts = int(event.get("created") or 0)
    if sub is None or _stale(sub, ts):
        return
    sub.last_payment_at = ts_to_dt(ts)
    sub.grace_until = None
    if sub.status == SubscriptionStatus.PAST_DUE:
        sub.status = SubscriptionStatus.ACTIVE
    sub.last_event_ts = ts
    sub.save()


def _on_invoice_payment_failed(event, obj):
    """Start the grace period: the paid plan keeps working for BILLING_GRACE_DAYS while Stripe retries the card."""
    sub = _find_subscription(obj)
    ts = int(event.get("created") or 0)
    if sub is None or _stale(sub, ts):
        return
    if not sub.plan.is_free and sub.status in (
        SubscriptionStatus.ACTIVE, SubscriptionStatus.TRIALING, SubscriptionStatus.PAST_DUE
    ):
        sub.status = SubscriptionStatus.PAST_DUE
        if sub.grace_until is None:
            sub.grace_until = _grace_deadline(ts)
        # Week 6: email the owner "your payment failed" from here (Celery task).
    sub.last_event_ts = ts
    sub.save()


HANDLERS = {
    "checkout.session.completed": _on_checkout_completed,
    "customer.subscription.created": _on_subscription,
    "customer.subscription.updated": _on_subscription,
    "customer.subscription.deleted": _on_subscription,
    "invoice.paid": _on_invoice_paid,
    "invoice.payment_failed": _on_invoice_payment_failed,
}


def process_event(event: dict) -> str:
    """-> 'processed' | 'duplicate' | 'ignored'. Raises ValueError for a malformed event."""
    event_id, event_type = event.get("id"), event.get("type")
    if not isinstance(event_id, str) or not isinstance(event_type, str):
        raise ValueError("Malformed event.")
    handler = HANDLERS.get(event_type)
    if handler is None:
        return "ignored"
    obj = (event.get("data") or {}).get("object")
    if not isinstance(obj, dict):
        raise ValueError("Malformed event object.")
    with transaction.atomic():
        _, created = StripeEvent.objects.get_or_create(
            event_id=event_id,
            defaults={"type": event_type, "customer_id": id_of(obj.get("customer")), "created_ts": int(event.get("created") or 0)},
        )
        if not created:
            return "duplicate"
        handler(event, obj)
    return "processed"
