"""The plan catalog and the lookups everything else uses."""
from django.conf import settings

from .models import FREE_PLAN_CODE, Plan, Subscription

DEFAULT_PLANS = [
    {
        "code": "free", "name": "Free", "description": "For getting started", "price_minor": 0,
        "trial_days": 0, "sort_order": 0,
        "limits": {"max_active_clients": 5, "max_invoices_per_month": 10, "max_team_members": 5},
        "features": {},
    },
    {
        "code": "pro", "name": "Pro", "description": "For busy freelancers", "price_minor": 79900,
        "trial_days": 14, "sort_order": 1,
        "limits": {"max_active_clients": 50, "max_invoices_per_month": 200, "max_team_members": 15},
        "features": {"recurring_invoices": True, "client_portal": True},
    },
    {
        "code": "business", "name": "Business", "description": "For agencies and teams", "price_minor": 199900,
        "trial_days": 14, "sort_order": 2,
        "limits": {"max_active_clients": None, "max_invoices_per_month": None, "max_team_members": None},
        "features": {"recurring_invoices": True, "client_portal": True, "reports": True, "audit_log": True},
    },
]


def ensure_default_plans():
    """Create the three starter plans if they are missing. Existing rows are never overwritten,
    so edits you make (in the admin or the database) survive."""
    codes = [p["code"] for p in DEFAULT_PLANS]
    if Plan.objects.filter(code__in=codes).count() == len(codes):
        return
    for data in DEFAULT_PLANS:
        Plan.objects.get_or_create(code=data["code"], defaults={k: v for k, v in data.items() if k != "code"})


def get_free_plan() -> Plan:
    ensure_default_plans()
    return Plan.objects.get(code=FREE_PLAN_CODE)


def get_subscription(tenant) -> Subscription:
    """The organization's subscription, created on first use (as Free)."""
    ensure_default_plans()
    found = Subscription.objects.select_related("plan").filter(tenant_id=tenant.pk).first()
    if found:
        return found
    sub, _ = Subscription.objects.get_or_create(tenant_id=tenant.pk, defaults={"plan": get_free_plan()})
    return Subscription.objects.select_related("plan").get(pk=sub.pk)


def effective_plan_for(subscription: Subscription) -> Plan:
    """The plan whose limits and features apply RIGHT NOW (Free once a paid plan has lapsed)."""
    return subscription.plan if subscription.is_entitled() else get_free_plan()


def price_id_for(plan: Plan) -> str:
    """Stripe Price id: the database value wins, else the STRIPE_PRICE_<PLAN> environment variable."""
    return plan.stripe_price_id or settings.STRIPE_PRICE_IDS.get(plan.code, "")


def plan_for_price_id(price_id: str):
    if not price_id:
        return None
    plan = Plan.objects.filter(stripe_price_id=price_id).first()
    if plan:
        return plan
    for code, configured in settings.STRIPE_PRICE_IDS.items():
        if configured and configured == price_id:
            return Plan.objects.filter(code=code).first()
    return None
