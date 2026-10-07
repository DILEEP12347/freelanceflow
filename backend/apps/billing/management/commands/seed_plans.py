from django.conf import settings
from django.core.management.base import BaseCommand

from apps.billing.models import Plan
from apps.billing.plans import ensure_default_plans, price_id_for


class Command(BaseCommand):
    help = "Create the Free, Pro and Business plans if missing, and show their Stripe price configuration."

    def handle(self, *args, **opts):
        ensure_default_plans()
        for plan in Plan.objects.all():
            price = price_id_for(plan) or ("-" if plan.is_free else "NOT SET")
            self.stdout.write(f"{plan.code:<9} {plan.name:<9} limits={plan.limits}  stripe price: {price}")
        self.stdout.write("Stripe secret key: " + ("set" if settings.STRIPE_SECRET_KEY else "NOT SET"))
        self.stdout.write("Stripe webhook secret: " + ("set" if settings.STRIPE_WEBHOOK_SECRET else "NOT SET"))
