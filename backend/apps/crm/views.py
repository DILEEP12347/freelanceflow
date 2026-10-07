from django.db.models import ProtectedError, Q
from django.utils import timezone
from django.shortcuts import get_object_or_404
from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.filters import OrderingFilter
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.billing.limits import enforce_limit
from apps.common.filters import QSearchFilter
from apps.common.pagination import StandardPagination
from apps.common.permissions import (
    ALL_ROLES,
    FINANCE_ROLES,
    MANAGER_ROLES,
    TenantRolePermission,
    get_membership,
)

from .models import OPEN_STAGES, Activity, ActivityKind, Client, ClientStatus, Contact, Lead, LeadStage, Note, Tag
from .serializers import (
    ActivitySerializer,
    ClientSerializer,
    ContactSerializer,
    LeadConvertSerializer,
    LeadMoveSerializer,
    LeadSerializer,
    NoteSerializer,
    TagSerializer,
)
from .services import (
    archive_client,
    convert_lead,
    finalize_stage_change,
    log_activity,
    move_lead,
    restore_client,
)
from .usage import active_client_count, usage_snapshot


class _CrmViewSet(viewsets.ModelViewSet):
    """Shared access rules: everyone reads, finance roles write, managers delete."""

    permission_classes = [TenantRolePermission]
    read_roles = ALL_ROLES
    write_roles = FINANCE_ROLES
    action_roles = {"destroy": MANAGER_ROLES}


class TagViewSet(_CrmViewSet):
    serializer_class = TagSerializer
    queryset = Tag.objects.order_by("name")


class ClientViewSet(_CrmViewSet):
    """?q=search &status=active|archived|all (default active) &tag=<name or id> &ordering=name &page=2"""

    serializer_class = ClientSerializer
    pagination_class = StandardPagination
    filter_backends = [QSearchFilter, OrderingFilter]
    search_fields = ["name", "company_name", "email", "phone"]
    ordering_fields = ["name", "created_at", "updated_at"]
    ordering = ["-created_at"]

    def get_queryset(self):
        qs = Client.objects.prefetch_related("tags")
        if self.action == "list":
            status_param = self.request.query_params.get("status", ClientStatus.ACTIVE.value)
            if status_param in ClientStatus.values:
                qs = qs.filter(status=status_param)
            elif status_param != "all":
                raise ValidationError({"status": "Use active, archived, or all."})
            tag = self.request.query_params.get("tag")
            if tag:
                qs = qs.filter(tags__pk=int(tag)) if tag.isdigit() else qs.filter(tags__name__iexact=tag)
                qs = qs.distinct()
        return qs

    def destroy(self, request, *args, **kwargs):
        try:
            return super().destroy(request, *args, **kwargs)
        except ProtectedError:
            # Invoices are legal records: a client that has any cannot be deleted, only archived.
            return Response(
                {"detail": "This client has invoices and cannot be deleted. Archive it instead."},
                status=status.HTTP_409_CONFLICT,
            )

    def perform_create(self, serializer):
        enforce_limit(self.request.tenant, "max_active_clients", active_client_count())
        client = serializer.save()
        log_activity(ActivityKind.CLIENT_CREATED, f"Created {client.name}", self.request.user, client=client)

    @action(detail=True, methods=["post"])
    def archive(self, request, pk=None):
        client = archive_client(self.get_object(), request.user)
        return Response(self.get_serializer(client).data)

    @action(detail=True, methods=["post"])
    def restore(self, request, pk=None):
        client = restore_client(self.get_object(), request.user)
        return Response(self.get_serializer(client).data)

    @action(detail=True, methods=["get"])
    def timeline(self, request, pk=None):
        client = self.get_object()
        qs = Activity.objects.filter(Q(client=client) | Q(lead__client=client)).order_by("-created_at", "-id")
        page = self.paginate_queryset(qs)
        return self.get_paginated_response(ActivitySerializer(page, many=True).data)


class ContactViewSet(_CrmViewSet):
    """Nested under /api/clients/<client_id>/contacts/. Only one contact per client is 'primary'."""

    serializer_class = ContactSerializer
    pagination_class = None
    http_method_names = ["get", "post", "patch", "delete", "head", "options"]

    def _client(self):
        return get_object_or_404(Client, pk=self.kwargs["client_id"])

    def get_queryset(self):
        self._client()  # 404 if the client doesn't exist
        return Contact.objects.filter(client_id=self.kwargs["client_id"]).order_by("-is_primary", "name")

    def _single_primary(self, contact):
        if contact.is_primary:
            Contact.objects.filter(client=contact.client).exclude(pk=contact.pk).update(is_primary=False)

    def perform_create(self, serializer):
        client = self._client()
        contact = serializer.save(client=client)
        self._single_primary(contact)
        log_activity(
            ActivityKind.CONTACT_ADDED, f"Added contact {contact.name}", self.request.user, client=client
        )

    def perform_update(self, serializer):
        self._single_primary(serializer.save())


class NoteViewSet(mixins.ListModelMixin, mixins.CreateModelMixin, mixins.DestroyModelMixin, viewsets.GenericViewSet):
    """Nested under /api/clients/<client_id>/notes/. Authors can delete their own notes; managers any."""

    serializer_class = NoteSerializer
    pagination_class = StandardPagination
    permission_classes = [TenantRolePermission]
    read_roles = ALL_ROLES
    write_roles = FINANCE_ROLES

    def _client(self):
        return get_object_or_404(Client, pk=self.kwargs["client_id"])

    def get_queryset(self):
        self._client()
        return Note.objects.filter(client_id=self.kwargs["client_id"]).order_by("-created_at", "-id")

    def perform_create(self, serializer):
        client = self._client()
        user = self.request.user
        note = serializer.save(client=client, author_id=user.pk, author_email=user.email)
        preview = note.body if len(note.body) <= 120 else note.body[:117] + "..."
        log_activity(ActivityKind.NOTE_ADDED, preview, user, client=client, metadata={"note_id": note.pk})

    def perform_destroy(self, instance):
        membership = get_membership(self.request)
        is_author = instance.author_id == self.request.user.pk
        if not (is_author or membership.role in MANAGER_ROLES):
            raise PermissionDenied("Only the author or an admin can delete this note.")
        instance.delete()


class LeadViewSet(_CrmViewSet):
    """?q=search &stage=lead|contacted|proposal|won|lost|open &ordering=-value_minor"""

    serializer_class = LeadSerializer
    pagination_class = StandardPagination
    filter_backends = [QSearchFilter, OrderingFilter]
    search_fields = ["title", "contact_name", "company_name", "contact_email"]
    ordering_fields = ["created_at", "updated_at", "value_minor", "expected_close_date"]
    ordering = ["-updated_at"]

    def get_queryset(self):
        qs = Lead.objects.select_related("client")
        if self.action == "list":
            stage = self.request.query_params.get("stage")
            if stage == "open":
                qs = qs.filter(stage__in=OPEN_STAGES)
            elif stage:
                if stage not in LeadStage.values:
                    raise ValidationError({"stage": "Unknown stage."})
                qs = qs.filter(stage=stage)
        return qs

    def perform_create(self, serializer):
        lead = serializer.save()
        if lead.stage in (LeadStage.WON, LeadStage.LOST):
            lead.closed_at = timezone.now()
            lead.save(update_fields=["closed_at"])
        log_activity(
            ActivityKind.LEAD_CREATED, f"Lead created: {lead.title}", self.request.user,
            lead=lead, metadata={"stage": lead.stage},
        )

    def perform_update(self, serializer):
        old_stage = serializer.instance.stage
        lead = serializer.save()
        if lead.stage != old_stage:
            finalize_stage_change(lead, old_stage, self.request.user)

    @action(detail=False, methods=["get"])
    def board(self, request):
        """Kanban view: every stage with its leads, count and total value."""
        by_stage = {value: [] for value in LeadStage.values}
        for lead in Lead.objects.select_related("client").order_by("-updated_at"):
            by_stage[lead.stage].append(lead)
        stages = [
            {
                "stage": value,
                "label": label,
                "count": len(by_stage[value]),
                "value_minor": sum(lead.value_minor for lead in by_stage[value]),
                "leads": LeadSerializer(by_stage[value], many=True).data,
            }
            for value, label in LeadStage.choices
        ]
        return Response({"stages": stages})

    @action(detail=True, methods=["post"])
    def move(self, request, pk=None):
        serializer = LeadMoveSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        lead = move_lead(self.get_object(), serializer.validated_data["stage"], request.user)
        return Response(LeadSerializer(lead).data)

    @action(detail=True, methods=["post"])
    def convert(self, request, pk=None):
        """Create a client from this lead (or pass {"client_id": N} to link an existing one); marks it won."""
        serializer = LeadConvertSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        existing = None
        if "client_id" in serializer.validated_data:
            existing = Client.objects.filter(pk=serializer.validated_data["client_id"]).first()
            if existing is None:
                raise ValidationError({"client_id": "No such client."})
        lead = self.get_object()
        client = convert_lead(lead, request.user, client=existing)
        return Response(
            {"lead": LeadSerializer(lead).data, "client": ClientSerializer(client).data},
            status=status.HTTP_201_CREATED,
        )

    @action(detail=True, methods=["get"])
    def timeline(self, request, pk=None):
        lead = self.get_object()
        qs = Activity.objects.filter(lead=lead).order_by("-created_at", "-id")
        page = self.paginate_queryset(qs)
        return self.get_paginated_response(ActivitySerializer(page, many=True).data)


class StatsView(APIView):
    """Dashboard numbers: client counts, pipeline value, conversion rate."""

    permission_classes = [TenantRolePermission]
    read_roles = ALL_ROLES

    def get(self, request):
        return Response(usage_snapshot())
