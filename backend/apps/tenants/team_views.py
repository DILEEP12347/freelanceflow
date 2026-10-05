from rest_framework import mixins, status, viewsets
from rest_framework.exceptions import PermissionDenied
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.common.permissions import ALL_ROLES, MANAGER_ROLES, TenantRolePermission, get_membership

from .models import Invitation, Membership, Role
from .services import accept_invitation, create_invitation, send_invitation_email
from .team_serializers import InvitationSerializer, InviteCreateSerializer, MemberSerializer


class MemberViewSet(
    mixins.ListModelMixin, mixins.UpdateModelMixin, mixins.DestroyModelMixin, viewsets.GenericViewSet
):
    """List members (everyone), change roles / remove members (owner + admin)."""

    serializer_class = MemberSerializer
    permission_classes = [TenantRolePermission]
    read_roles = ALL_ROLES
    write_roles = MANAGER_ROLES
    http_method_names = ["get", "patch", "delete", "head", "options"]

    def get_queryset(self):
        return (
            Membership.objects.filter(tenant=self.request.tenant)
            .select_related("user")
            .order_by("created_at")
        )

    def _guard(self, target, new_role=None, changing_role=False):
        actor = get_membership(self.request)
        if target.role == Role.OWNER:
            raise PermissionDenied("The owner cannot be modified or removed.")
        if changing_role and target.pk == actor.pk:
            raise PermissionDenied("You cannot change your own role.")
        if actor.role != Role.OWNER and (target.role == Role.ADMIN or new_role == Role.ADMIN):
            raise PermissionDenied("Only the owner can manage admins.")

    def perform_update(self, serializer):
        target = serializer.instance
        new_role = serializer.validated_data.get("role", target.role)
        self._guard(target, new_role=new_role, changing_role=True)
        serializer.save()

    def perform_destroy(self, instance):
        self._guard(instance)
        instance.delete()


class InvitationViewSet(
    mixins.ListModelMixin, mixins.CreateModelMixin, mixins.DestroyModelMixin, viewsets.GenericViewSet
):
    """Owner + admin can list pending invites, send new ones, and revoke them."""

    serializer_class = InvitationSerializer
    permission_classes = [TenantRolePermission]
    read_roles = MANAGER_ROLES
    write_roles = MANAGER_ROLES

    def get_queryset(self):
        return Invitation.objects.filter(
            tenant=self.request.tenant, accepted_at__isnull=True
        ).order_by("-created_at")

    def create(self, request, *args, **kwargs):
        serializer = InviteCreateSerializer(data=request.data, context={"tenant": request.tenant})
        serializer.is_valid(raise_exception=True)
        actor = get_membership(request)
        role = serializer.validated_data["role"]
        if role == Role.ADMIN and actor.role != Role.OWNER:
            raise PermissionDenied("Only the owner can invite admins.")
        invitation, raw_token = create_invitation(
            tenant=request.tenant,
            email=serializer.validated_data["email"],
            role=role,
            invited_by=request.user,
        )
        send_invitation_email(invitation, raw_token)
        return Response(InvitationSerializer(invitation).data, status=status.HTTP_201_CREATED)


class AcceptInvitationView(APIView):
    """Any logged-in user holding a valid token (sent to THEIR email) can join."""

    permission_classes = [IsAuthenticated]

    def post(self, request):
        membership = accept_invitation(request.data.get("token"), request.user, request.tenant)
        return Response(
            {"organization": request.tenant.name, "role": membership.role},
            status=status.HTTP_201_CREATED,
        )
