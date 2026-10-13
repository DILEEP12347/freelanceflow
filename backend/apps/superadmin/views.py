from django.db.models import Q
from rest_framework.exceptions import ValidationError
from rest_framework.generics import get_object_or_404
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.billing.models import StripeEvent
from apps.billing.plans import get_subscription
from apps.common.pagination import StandardPagination
from apps.tenants.models import Membership

from . import stats
from .permissions import IsPlatformAdmin


class _PlatformView(APIView):
    permission_classes = [IsPlatformAdmin]


class OverviewView(_PlatformView):
    """Organizations, plan mix, paying/trialing/past-due counts, MRR, ARR, churn and growth."""

    def get(self, request):
        return Response(stats.overview())


class OrganizationListView(_PlatformView):
    """?q=<name, domain or owner email> &plan=free|pro|business &status=active|trialing|past_due|canceled
    &ordering=-created_at|name"""

    def get(self, request):
        qs = stats.organizations()
        p = request.query_params
        if p.get("q"):
            qs = qs.filter(
                Q(name__icontains=p["q"]) | Q(domains__domain__icontains=p["q"])
                | Q(memberships__role="owner", memberships__user__email__icontains=p["q"])
            ).distinct()
        if p.get("plan"):
            if p["plan"] == "free":
                qs = qs.filter(Q(subscription__isnull=True) | Q(subscription__plan__code="free"))
            else:
                qs = qs.filter(subscription__plan__code=p["plan"])
        if p.get("status"):
            if p["status"] == "active":
                qs = qs.filter(Q(subscription__isnull=True) | Q(subscription__status="active"))
            else:
                qs = qs.filter(subscription__status=p["status"])
        ordering = p.get("ordering", "-created_at")
        if ordering not in ("created_at", "-created_at", "name", "-name"):
            raise ValidationError({"ordering": "Use created_at, -created_at, name or -name."})
        paginator = StandardPagination()
        page = paginator.paginate_queryset(qs.order_by(ordering, "id"), request, view=self)
        return paginator.get_paginated_response(stats.organization_rows(page))


class OrganizationDetailView(_PlatformView):
    def get(self, request, pk):
        tenant = get_object_or_404(stats.organizations(), pk=pk)
        row = stats.organization_rows([tenant])[0]
        sub = get_subscription(tenant)
        team = [{"email": m.user.email, "role": m.role}
                for m in Membership.objects.filter(tenant=tenant).select_related("user").order_by("created_at")]
        events = []
        if sub.stripe_customer_id:
            events = list(StripeEvent.objects.filter(customer_id=sub.stripe_customer_id).values("type", "processed_at")[:10])
        row.update({"team": team, "usage": stats.usage_for(tenant), "recent_billing_events": events,
                    "plan_limits": sub.plan.limits})
        return Response(row)


class BillingEventsView(_PlatformView):
    """The latest Stripe events we processed (all organizations)."""

    def get(self, request):
        rows = StripeEvent.objects.values("event_id", "type", "customer_id", "created_ts", "processed_at")[:100]
        return Response(list(rows))
