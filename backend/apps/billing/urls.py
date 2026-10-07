from django.urls import path

from .views import ChangePlanView, CheckoutView, PlansView, PortalView, SubscriptionView

urlpatterns = [
    path("billing/plans/", PlansView.as_view()),
    path("billing/subscription/", SubscriptionView.as_view()),
    path("billing/checkout/", CheckoutView.as_view()),
    path("billing/portal/", PortalView.as_view()),
    path("billing/change-plan/", ChangePlanView.as_view()),
]
