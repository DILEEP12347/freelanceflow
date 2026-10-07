"""The ONLY place that talks to Stripe. Everything else calls these functions, which makes the rest of
the code easy to test (tests replace these functions) and keeps SDK details in one file.

Uses the resource-style calls (stripe.Customer.create ...) with a per-request api_key, so there is no
global Stripe state. State changes (plan, status) are NEVER written from these calls: they arrive by webhook.
"""
import logging
from contextlib import contextmanager

from django.conf import settings
from rest_framework.exceptions import APIException

logger = logging.getLogger(__name__)


class BillingNotConfigured(APIException):
    status_code = 503
    default_code = "billing_not_configured"
    default_detail = "Billing is not configured on this server yet."


class BillingProviderError(APIException):
    status_code = 502
    default_code = "billing_provider_error"
    default_detail = "The payment provider could not complete the request. Please try again."


def _key() -> str:
    key = settings.STRIPE_SECRET_KEY
    if not key:
        raise BillingNotConfigured()
    return key


@contextmanager
def _translate_errors():
    import stripe

    stripe_error = getattr(stripe, "StripeError", None) or stripe.error.StripeError
    try:
        yield
    except stripe_error as exc:
        logger.exception("Stripe API error: %s", exc)
        raise BillingProviderError() from exc


def create_customer(email: str, name: str, tenant_id: int) -> str:
    import stripe

    key = _key()
    with _translate_errors():
        customer = stripe.Customer.create(
            email=email, name=name, metadata={"tenant_id": str(tenant_id)},
            api_key=key, idempotency_key=f"customer-tenant-{tenant_id}",
        )
    return customer.id


def create_checkout_session(customer_id, price_id, tenant_id, plan_code, trial_days, success_url, cancel_url) -> dict:
    import stripe

    key = _key()
    subscription_data = {"metadata": {"tenant_id": str(tenant_id), "plan": plan_code}}
    if trial_days:
        subscription_data["trial_period_days"] = int(trial_days)
    with _translate_errors():
        session = stripe.checkout.Session.create(
            mode="subscription",
            customer=customer_id,
            client_reference_id=str(tenant_id),
            line_items=[{"price": price_id, "quantity": 1}],
            subscription_data=subscription_data,
            allow_promotion_codes=True,
            success_url=success_url,
            cancel_url=cancel_url,
            api_key=key,
        )
    return {"id": session.id, "url": session.url}


def create_portal_session(customer_id: str, return_url: str) -> str:
    import stripe

    key = _key()
    with _translate_errors():
        session = stripe.billing_portal.Session.create(customer=customer_id, return_url=return_url, api_key=key)
    return session.url


def change_subscription_price(subscription_id: str, price_id: str, plan_code: str) -> None:
    """Switch the subscription's price. Stripe prorates: the customer is credited for the unused part of
    the old plan and charged for the new one on the next invoice."""
    import stripe

    key = _key()
    with _translate_errors():
        current = stripe.Subscription.retrieve(subscription_id, api_key=key)
        item_id = current["items"]["data"][0]["id"]  # ["items"], because .items is the dict method
        stripe.Subscription.modify(
            subscription_id,
            items=[{"id": item_id, "price": price_id}],
            proration_behavior="create_prorations",
            metadata={"plan": plan_code},
            api_key=key,
        )
