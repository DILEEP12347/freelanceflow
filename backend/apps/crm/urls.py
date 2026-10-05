from django.urls import include, path
from rest_framework.routers import DefaultRouter

from .views import ClientViewSet, ContactViewSet, LeadViewSet, NoteViewSet, StatsView, TagViewSet

router = DefaultRouter()
router.register("clients", ClientViewSet, basename="client")
router.register("tags", TagViewSet, basename="tag")
router.register("leads", LeadViewSet, basename="lead")

urlpatterns = [
    path("crm/stats/", StatsView.as_view()),
    path("clients/<int:client_id>/contacts/", ContactViewSet.as_view({"get": "list", "post": "create"})),
    path(
        "clients/<int:client_id>/contacts/<int:pk>/",
        ContactViewSet.as_view({"get": "retrieve", "patch": "partial_update", "delete": "destroy"}),
    ),
    path("clients/<int:client_id>/notes/", NoteViewSet.as_view({"get": "list", "post": "create"})),
    path("clients/<int:client_id>/notes/<int:pk>/", NoteViewSet.as_view({"delete": "destroy"})),
    path("", include(router.urls)),
]
