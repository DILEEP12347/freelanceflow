from django.urls import include, path
from rest_framework.routers import SimpleRouter

from .team_views import AcceptInvitationView, InvitationViewSet, MemberViewSet

router = SimpleRouter()
router.register("members", MemberViewSet, basename="member")
router.register("invites", InvitationViewSet, basename="invite")

urlpatterns = [
    path("invites/accept/", AcceptInvitationView.as_view()),  # must come before the router
    path("", include(router.urls)),
]
