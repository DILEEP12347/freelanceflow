"""Plan limits and feature flags. Everything is read from the database (Plan rows), never hardcoded.

    enforce_limit(tenant, "max_active_clients", used=current_count)   # raises 402 when adding one more would exceed it
    permission_classes = [TenantRolePermission, requires_feature("recurring_invoices")]

Policy: limits stop NEW things (creating or restoring a client, sending an invoice, inviting someone).
Downgrading never deletes or hides existing data.
"""
from rest_framework.exceptions import APIException
from rest_framework.permissions import BasePermission

from .plans import effective_plan_for, get_subscription

LIMIT_LABELS = {
    "max_active_clients": "active clients",
    "max_invoices_per_month": "invoices per month",
    "max_team_members": "team members",
}


class PlanLimitExceeded(APIException):
    """HTTP 402 with machine-readable details, so the frontend can show an upgrade prompt."""

    status_code = 402
    default_code = "plan_limit_reached"

    def __init__(self, plan, key, limit, used):
        message = f"Your {plan.name} plan allows {limit} {LIMIT_LABELS.get(key, key)}. Upgrade your plan to add more."
        super().__init__(message)
        self.detail = {  # a plain dict, so the numbers stay numbers in the JSON
            "detail": message, "code": self.default_code, "limit_key": key,
            "limit": limit, "used": used, "plan": plan.code,
        }


class FeatureNotAvailable(APIException):
    status_code = 402
    default_code = "feature_not_available"

    def __init__(self, plan, feature):
        message = f"This feature is not included in your {plan.name} plan. Upgrade to use it."
        super().__init__(message)
        self.detail = {"detail": message, "code": self.default_code, "feature": feature, "plan": plan.code}


def effective_plan(tenant):
    """None when there is no real tenant (management commands): then nothing is limited."""
    if getattr(tenant, "pk", None) is None:
        return None
    return effective_plan_for(get_subscription(tenant))


def get_limit(tenant, key):
    plan = effective_plan(tenant)
    return None if plan is None else (plan.limits or {}).get(key)


def enforce_limit(tenant, key, used, adding=1):
    plan = effective_plan(tenant)
    if plan is None:
        return
    limit = (plan.limits or {}).get(key)
    if limit is not None and used + adding > limit:
        raise PlanLimitExceeded(plan, key, limit, used)


def has_feature(tenant, key) -> bool:
    plan = effective_plan(tenant)
    return bool(plan and (plan.features or {}).get(key))


def requires_feature(key):
    """Permission class factory. Use after TenantRolePermission so logged-out users still get 401/403 first."""

    class RequiresFeature(BasePermission):
        def has_permission(self, request, view):
            tenant = getattr(request, "tenant", None)
            if not has_feature(tenant, key):
                plan = effective_plan(tenant)
                if plan is None:
                    return False
                raise FeatureNotAvailable(plan, key)
            return True

    RequiresFeature.__name__ = f"RequiresFeature_{key}"
    return RequiresFeature
