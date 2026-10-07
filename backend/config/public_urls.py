"""URLs served on the bare domain (localhost): auth, signup, super-admin."""
from django.contrib import admin
from django.urls import include, path

from apps.billing.views import StripeWebhookView
from apps.common.views import health
from apps.tenants.views import SignupView

urlpatterns = [
    path("admin/", admin.site.urls),
    path("api/health/", health),
    path("api/auth/", include("apps.accounts.urls")),
    path("api/tenants/signup/", SignupView.as_view()),
    path("api/billing/webhook/", StripeWebhookView.as_view()),  # Stripe -> us (signed, no login)
]
