from django.urls import path

from .portal import PortalApiView, PortalPageView, PortalPdfView

urlpatterns = [
    path("portal/<str:token>/", PortalPageView.as_view()),
    path("portal/<str:token>/pdf/", PortalPdfView.as_view()),
    path("api/portal/<str:token>/", PortalApiView.as_view()),
]
