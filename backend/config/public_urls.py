"""URLs served on the bare domain (localhost): marketing/signup/super-admin."""
from django.contrib import admin
from django.urls import path

from apps.common.views import health
from apps.tenants.views import SignupView

urlpatterns = [
    path("admin/", admin.site.urls),
    path("api/health/", health),
    path("api/tenants/signup/", SignupView.as_view()),
]
