"""URLs served on tenant subdomains (acme.localhost)."""
from django.urls import include, path

from apps.business.views import BusinessProfileView
from apps.common.views import health

urlpatterns = [
    path("api/health/", health),
    path("api/auth/", include("apps.accounts.urls")),
    path("api/team/", include("apps.tenants.team_urls")),
    path("api/business-profile/", BusinessProfileView.as_view()),
    path("api/", include("apps.crm.urls")),
    path("api/", include("apps.invoicing.urls")),
]
