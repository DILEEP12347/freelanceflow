from django.urls import path

from .views import BillingEventsView, OrganizationDetailView, OrganizationListView, OverviewView

urlpatterns = [
    path("platform/overview/", OverviewView.as_view()),
    path("platform/organizations/", OrganizationListView.as_view()),
    path("platform/organizations/<int:pk>/", OrganizationDetailView.as_view()),
    path("platform/billing-events/", BillingEventsView.as_view()),
]
