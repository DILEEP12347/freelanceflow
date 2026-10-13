"""Platform-wide numbers. Revenue figures come from the Plan's listed price (Plan.price_minor) for subscriptions that
are really paying, so keep those prices in step with Stripe. Discounts, coupons and proration are not reflected."""
import logging
from collections import defaultdict
from datetime import timedelta

from django.db.models import Count
from django.db.models.functions import TruncMonth
from django.utils import timezone
from django_tenants.utils import get_public_schema_name, tenant_context

from apps.billing.models import Plan, StripeEvent, Subscription, SubscriptionStatus
from apps.billing.plans import ensure_default_plans
from apps.tenants.models import Membership, Role, Tenant

logger = logging.getLogger(__name__)


def organizations():
    return Tenant.objects.exclude(schema_name=get_public_schema_name())


def classify_subscription(sub, free_plan, now):
    """-> (effective_plan, is_paying, is_trialing, is_past_due) for one subscription (or none = Free)."""
    if sub is None:
        return free_plan, False, False, False
    effective = sub.plan if sub.is_entitled(now) else free_plan
    if sub.plan.is_free:
        return effective, False, False, False
    paying = sub.status == SubscriptionStatus.ACTIVE or (sub.status == SubscriptionStatus.PAST_DUE and sub.is_entitled(now))
    return effective, paying, sub.status == SubscriptionStatus.TRIALING, sub.status == SubscriptionStatus.PAST_DUE


def overview(now=None):
    now = now or timezone.now()
    ensure_default_plans()
    free = Plan.objects.get(code="free")
    org_ids = list(organizations().values_list("pk", flat=True))
    subs = {s.tenant_id: s for s in Subscription.objects.select_related("plan").filter(tenant_id__in=org_ids)}

    by_plan = defaultdict(int)
    paying = trialing = past_due = 0
    mrr = defaultdict(int)
    for tenant_id in org_ids:
        sub = subs.get(tenant_id)
        effective, is_paying, is_trialing, is_past_due = classify_subscription(sub, free, now)
        by_plan[effective.code] += 1
        trialing += is_trialing
        past_due += is_past_due
        if is_paying:
            paying += 1
            mrr[sub.plan.currency] += sub.plan.price_minor

    since = int((now - timedelta(days=30)).timestamp())
    cancellations = StripeEvent.objects.filter(type="customer.subscription.deleted", created_ts__gte=since).count()
    new_subs = StripeEvent.objects.filter(type="customer.subscription.created", created_ts__gte=since).count()
    denominator = paying + cancellations
    growth = (organizations().filter(created_at__gte=now - timedelta(days=185)).annotate(m=TruncMonth("created_at"))
              .values("m").order_by("m").annotate(n=Count("id")))

    return {
        "generated_at": now,
        "organizations": len(org_ids),
        "new_organizations_30d": organizations().filter(created_at__gte=now - timedelta(days=30)).count(),
        "plans": [{"code": p.code, "name": p.name, "organizations": by_plan.get(p.code, 0)} for p in Plan.objects.all()],
        "subscriptions": {"paying": paying, "trialing": trialing, "past_due": past_due},
        "mrr": [{"currency": c, "amount_minor": v} for c, v in sorted(mrr.items())],
        "arr": [{"currency": c, "amount_minor": v * 12} for c, v in sorted(mrr.items())],
        # An approximation: cancellations in the last 30 days / (paying customers now + those cancellations).
        "churn_30d": {"cancellations": cancellations, "new_subscriptions": new_subs,
                      "rate": round(cancellations / denominator, 4) if denominator else 0.0},
        "growth": [{"month": g["m"].strftime("%Y-%m"), "organizations": g["n"]} for g in growth],
    }


def organization_rows(tenants, now=None):
    """Rows for one page of organizations, with a handful of queries in total (not per row)."""
    from apps.tenants.models import Domain

    now = now or timezone.now()
    ensure_default_plans()
    free = Plan.objects.get(code="free")
    ids = [t.pk for t in tenants]
    subs = {s.tenant_id: s for s in Subscription.objects.select_related("plan").filter(tenant_id__in=ids)}
    owners = {m.tenant_id: m.user.email for m in Membership.objects.filter(tenant_id__in=ids, role=Role.OWNER.value).select_related("user")}
    members = dict(Membership.objects.filter(tenant_id__in=ids).values_list("tenant_id").annotate(n=Count("id")))
    domains = {}
    for d in Domain.objects.filter(tenant_id__in=ids).order_by("is_primary", "id"):
        domains[d.tenant_id] = d.domain  # a primary domain, if any, is written last and wins
    rows = []
    for t in tenants:
        sub = subs.get(t.pk)
        effective, *_ = classify_subscription(sub, free, now)
        rows.append({
            "id": t.pk, "name": t.name, "schema_name": t.schema_name, "domain": domains.get(t.pk, ""),
            "owner_email": owners.get(t.pk, ""), "members": members.get(t.pk, 0),
            "plan": sub.plan.code if sub else "free", "effective_plan": effective.code,
            "status": sub.status if sub else "active",
            "trial_end": sub.trial_end if sub else None, "grace_until": sub.grace_until if sub else None,
            "current_period_end": sub.current_period_end if sub else None,
            "cancel_at_period_end": bool(sub and sub.cancel_at_period_end),
            "has_stripe_customer": bool(sub and sub.stripe_customer_id), "created_at": t.created_at,
        })
    return rows


def usage_for(tenant):
    """Counts from inside the organization's own schema. None if that schema cannot be read."""
    try:
        from apps.audit.models import AuditLog
        from apps.crm.models import Client, ClientStatus
        from apps.invoicing.models import Invoice, InvoiceStatus

        with tenant_context(tenant):
            last = AuditLog.objects.order_by("-created_at").values_list("created_at", flat=True).first()
            return {
                "active_clients": Client.objects.filter(status=ClientStatus.ACTIVE).count(),
                "invoices_sent": Invoice.objects.exclude(status=InvoiceStatus.DRAFT).count(),
                "last_activity_at": last,
            }
    except Exception:
        logger.exception("Could not read usage for tenant %s", tenant.pk)
        return None
