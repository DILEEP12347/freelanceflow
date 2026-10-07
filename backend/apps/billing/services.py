import logging

from django.conf import settings
from django.db import transaction
from rest_framework.exceptions import ValidationError

from apps.common.urls_util import site_url

from . import gateway
from .models import Plan, SubscriptionStatus
from .plans import effective_plan_for, get_subscription, price_id_for
from .serializers import PlanSerializer
from .usage import usage_snapshot

logger = logging.getLogger(__name__)

LIVE_STATUSES = (SubscriptionStatus.ACTIVE, SubscriptionStatus.TRIALING, SubscriptionStatus.PAST_DUE)


def _plan_or_400(code, allow_free=False) -> Plan:
    plan = Plan.objects.filter(code=code, is_active=True).first() if isinstance(code, str) else None
    if plan is None:
        raise ValidationError({"plan": "Unknown plan."})
    if plan.is_free and not allow_free:
        raise ValidationError({"plan": "The Free plan needs no checkout. To downgrade, cancel in the billing portal."})
    return plan


def _urls(tenant):
    domain = tenant.get_primary_domain()
    base = site_url(domain.domain if domain else settings.BASE_DOMAIN)
    page = f"{base}{settings.BILLING_RETURN_PATH}"
    # {CHECKOUT_SESSION_ID} is a literal placeholder Stripe fills in.
    return f"{page}?checkout=success&session_id={{CHECKOUT_SESSION_ID}}", f"{page}?checkout=cancelled", page


def start_checkout(tenant, user, plan_code) -> str:
    """-> the Stripe Checkout URL to redirect the browser to. Nothing changes in our database
    except remembering the Stripe customer id; the plan only changes when Stripe's webhook says so."""
    plan = _plan_or_400(plan_code)
    price_id = price_id_for(plan)
    if not price_id:
        raise gateway.BillingNotConfigured("This plan has no Stripe price configured yet.")
    sub = get_subscription(tenant)
    if sub.stripe_subscription_id and sub.status in LIVE_STATUSES:
        raise ValidationError({"detail": "You already have a subscription. Use change-plan or the billing portal."})

    if not sub.stripe_customer_id:
        sub.stripe_customer_id = gateway.create_customer(email=user.email, name=tenant.name, tenant_id=tenant.pk)
        sub.save(update_fields=["stripe_customer_id", "updated_at"])

    success_url, cancel_url, _ = _urls(tenant)
    session = gateway.create_checkout_session(
        customer_id=sub.stripe_customer_id, price_id=price_id, tenant_id=tenant.pk, plan_code=plan.code,
        trial_days=0 if sub.trial_used else plan.trial_days, success_url=success_url, cancel_url=cancel_url,
    )
    return session["url"]


def open_portal(tenant) -> str:
    sub = get_subscription(tenant)
    if not sub.stripe_customer_id:
        raise ValidationError({"detail": "There is no billing account yet. Subscribe to a paid plan first."})
    return gateway.create_portal_session(sub.stripe_customer_id, _urls(tenant)[2])


def change_plan(tenant, plan_code) -> Plan:
    """Upgrade or downgrade between paid plans, with proration. The new plan is applied by the webhook."""
    plan = _plan_or_400(plan_code)
    sub = get_subscription(tenant)
    if not sub.stripe_subscription_id or sub.status not in LIVE_STATUSES:
        raise ValidationError({"detail": "You have no active subscription. Start one with checkout."})
    if sub.plan_id == plan.pk:
        raise ValidationError({"plan": "You are already on this plan."})
    price_id = price_id_for(plan)
    if not price_id:
        raise gateway.BillingNotConfigured("This plan has no Stripe price configured yet.")
    gateway.change_subscription_price(sub.stripe_subscription_id, price_id, plan.code)
    return plan


def subscription_summary(tenant) -> dict:
    sub = get_subscription(tenant)
    plan = effective_plan_for(sub)
    return {
        "plan": PlanSerializer(sub.plan).data,
        "effective_plan": PlanSerializer(plan).data,
        "status": sub.status,
        "entitled": sub.is_entitled(),
        "trial_end": sub.trial_end,
        "current_period_end": sub.current_period_end,
        "cancel_at_period_end": sub.cancel_at_period_end,
        "grace_until": sub.grace_until,
        "can_start_trial": not sub.trial_used,
        "has_billing_account": bool(sub.stripe_customer_id),
        "features": plan.features,
        "usage": usage_snapshot(tenant, plan),
    }
