import logging

from celery import shared_task
from django.core.mail import send_mail

logger = logging.getLogger(__name__)


def _date(value):
    return value.strftime("%d %b %Y") if value else "soon"


@shared_task(name="billing.send_billing_notice", autoretry_for=(OSError,), retry_backoff=True, max_retries=3)
def send_billing_notice(tenant_id, kind):
    """Email the organization's owner(s) about their subscription. kind: 'payment_failed' | 'trial_ending'."""
    from apps.tenants.models import Membership, Role, Tenant

    from .plans import get_subscription

    tenant = Tenant.objects.filter(pk=tenant_id).first()
    if tenant is None:
        return "skipped"
    owners = list(
        Membership.objects.filter(tenant=tenant, role=Role.OWNER.value).values_list("user__email", flat=True)
    )
    owners = [email for email in owners if email]
    if not owners:
        return "no_recipient"
    sub = get_subscription(tenant)
    plan = sub.plan.name

    if kind == "payment_failed":
        subject = f"Your {plan} payment for {tenant.name} did not go through"
        body = (
            f"Hi,\n\nWe could not collect your latest payment for the {plan} plan on {tenant.name}.\n\n"
            f"Your {plan} features stay on until {_date(sub.grace_until)}. After that the account moves to the "
            "Free plan. Nothing is deleted.\n\n"
            "To fix it, open Settings > Billing and update your payment method. Your card is retried automatically.\n"
        )
    elif kind == "trial_ending":
        subject = f"Your {plan} trial for {tenant.name} ends soon"
        body = (
            f"Hi,\n\nYour free trial of the {plan} plan on {tenant.name} ends on {_date(sub.trial_end)}.\n\n"
            "Your card will be charged then, unless you cancel before that in Settings > Billing.\n"
        )
    else:
        logger.warning("Unknown billing notice kind %r", kind)
        return "unknown_kind"
    send_mail(subject, body, None, owners)
    return "sent"
