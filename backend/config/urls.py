"""URLs served on tenant subdomains (acme.localhost)."""
from django.urls import include, path
from rest_framework.routers import DefaultRouter

from apps.business.views import BusinessProfileView
from apps.common.views import health
from apps.crm.views import ClientViewSet

router = DefaultRouter()
router.register("clients", ClientViewSet, basename="client")

urlpatterns = [
    path("api/health/", health),
    path("api/auth/", include("apps.accounts.urls")),
    path("api/team/", include("apps.tenants.team_urls")),
    path("api/business-profile/", BusinessProfileView.as_view()),
    path("api/", include(router.urls)),
]
