import json

from django.conf import settings
from rest_framework.exceptions import ValidationError
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.common.permissions import ALL_ROLES, TenantRolePermission
from apps.tenants.models import Role

from . import services
from .gateway import BillingNotConfigured
from .models import Plan
from .plans import ensure_default_plans
from .serializers import PlanSerializer
from .signatures import SignatureError, verify_signature
from .webhooks import process_event

OWNER_ONLY = frozenset({Role.OWNER.value})


class _BillingView(APIView):
    """Everyone in the organization can read billing. Only the owner can change it."""

    permission_classes = [TenantRolePermission]
    read_roles = ALL_ROLES
    write_roles = OWNER_ONLY


class PlansView(_BillingView):
    def get(self, request):
        ensure_default_plans()
        return Response(PlanSerializer(Plan.objects.filter(is_active=True), many=True).data)


class SubscriptionView(_BillingView):
    def get(self, request):
        return Response(services.subscription_summary(request.tenant))


class CheckoutView(_BillingView):
    def post(self, request):
        return Response({"url": services.start_checkout(request.tenant, request.user, request.data.get("plan"))})


class PortalView(_BillingView):
    def post(self, request):
        return Response({"url": services.open_portal(request.tenant)})


class ChangePlanView(_BillingView):
    def post(self, request):
        plan = services.change_plan(request.tenant, request.data.get("plan"))
        return Response(
            {"detail": "Plan change requested. It applies as soon as Stripe confirms it, usually within seconds.",
             "plan": plan.code},
            status=202,
        )


class StripeWebhookView(APIView):
    """POST /api/billing/webhook/ on the bare domain. No login: Stripe proves who it is with a signature."""

    authentication_classes = []
    permission_classes = [AllowAny]

    def post(self, request):
        if not settings.STRIPE_WEBHOOK_SECRET:
            raise BillingNotConfigured()
        payload = request.body
        try:
            verify_signature(payload, request.META.get("HTTP_STRIPE_SIGNATURE", ""), settings.STRIPE_WEBHOOK_SECRET)
            event = json.loads(payload)
        except (SignatureError, ValueError):
            return Response({"detail": "Invalid webhook."}, status=400)
        if not isinstance(event, dict):
            return Response({"detail": "Invalid webhook."}, status=400)
        try:
            result = process_event(event)
        except ValueError:
            return Response({"detail": "Malformed event."}, status=400)
        return Response({"status": result})
