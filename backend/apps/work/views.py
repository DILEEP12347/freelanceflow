from django.db.models import BigIntegerField, Count, ExpressionWrapper, F, ProtectedError, Q, Sum
from django.db.models.functions import Coalesce
from django.utils.dateparse import parse_date
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.filters import OrderingFilter
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.common.filters import QSearchFilter
from apps.common.pagination import StandardPagination
from apps.common.permissions import ALL_ROLES, FINANCE_ROLES, MANAGER_ROLES, TenantRolePermission, get_membership
from apps.invoicing.models import TaxRate
from apps.invoicing.timeutils import local_day_bounds

from . import services
from .hours import value_from_product
from .models import Expense, Project, ProjectStatus, TimeEntry
from .serializers import (
    ExpenseSerializer,
    ProjectSerializer,
    StartTimerSerializer,
    TimeEntryCreateSerializer,
    TimeEntrySerializer,
    TimeEntryUpdateSerializer,
    WorkInvoiceSerializer,
)


def _flag(params, name):
    """?billable=true|false -> True/False, absent -> None, anything else -> 400."""
    raw = params.get(name)
    if raw is None or raw == "":
        return None
    if raw.lower() in ("true", "1", "yes"):
        return True
    if raw.lower() in ("false", "0", "no"):
        return False
    raise ValidationError({name: "Use true or false."})


def _date(params, name):
    raw = params.get(name)
    if not raw:
        return None
    day = parse_date(raw)
    if day is None:
        raise ValidationError({name: "Use YYYY-MM-DD."})
    return day


def _int(params, name):
    raw = params.get(name)
    if raw in (None, ""):
        return None
    if not raw.isdigit():
        raise ValidationError({name: "Use a number."})
    return int(raw)


class _Base:
    permission_classes = [TenantRolePermission]
    read_roles = ALL_ROLES
    write_roles = FINANCE_ROLES
    pagination_class = StandardPagination
    http_method_names = ["get", "post", "patch", "delete", "head", "options"]


class ProjectViewSet(_Base, viewsets.ModelViewSet):
    """?q= &client=<id> &status=active|archived"""

    serializer_class = ProjectSerializer
    action_roles = {"destroy": MANAGER_ROLES}
    filter_backends = [QSearchFilter, OrderingFilter]
    search_fields = ["name", "client__name"]
    ordering_fields = ["name", "created_at"]
    ordering = ["name"]

    def get_queryset(self):
        qs = Project.objects.select_related("client")
        params = self.request.query_params
        if _int(params, "client"):
            qs = qs.filter(client_id=_int(params, "client"))
        if params.get("status"):
            if params["status"] not in ProjectStatus.values:
                raise ValidationError({"status": "Use active or archived."})
            qs = qs.filter(status=params["status"])
        return qs

    def destroy(self, request, *args, **kwargs):
        try:
            return super().destroy(request, *args, **kwargs)
        except ProtectedError:
            return Response({"detail": "This project has time or expenses logged. Archive it instead."},
                            status=status.HTTP_409_CONFLICT)

    @action(detail=True, methods=["post"])
    def archive(self, request, pk=None):
        return Response(self.get_serializer(services.archive_project(self.get_object())).data)

    @action(detail=True, methods=["post"])
    def restore(self, request, pk=None):
        return Response(self.get_serializer(services.restore_project(self.get_object())).data)


class TimeEntryViewSet(_Base, viewsets.ModelViewSet):
    """?project= &client= &user=me|<id> &from= &to= &billable= &billed= &running=
    The list also returns `summary` for everything that matches the filters (not just the page)."""

    ordering = ["-started_at"]
    filter_backends = [OrderingFilter]
    ordering_fields = ["started_at", "duration_seconds"]

    def get_serializer_class(self):
        if self.action == "create":
            return TimeEntryCreateSerializer
        if self.action == "partial_update":
            return TimeEntryUpdateSerializer
        if self.action == "start":
            return StartTimerSerializer
        return TimeEntrySerializer

    def get_queryset(self):
        qs = TimeEntry.objects.select_related("project", "project__client")
        if self.action != "list":
            return qs
        params = self.request.query_params
        if _int(params, "project"):
            qs = qs.filter(project_id=_int(params, "project"))
        if _int(params, "client"):
            qs = qs.filter(project__client_id=_int(params, "client"))
        who = params.get("user")
        if who == "me":
            qs = qs.filter(user_id=self.request.user.pk)
        elif who:
            qs = qs.filter(user_id=_int(params, "user"))
        start, end = local_day_bounds(_date(params, "from"), _date(params, "to"))
        if start:
            qs = qs.filter(started_at__gte=start)
        if end:
            qs = qs.filter(started_at__lt=end)
        for name, field in (("billable", "billable"),):
            flag = _flag(params, name)
            if flag is not None:
                qs = qs.filter(**{field: flag})
        billed, running = _flag(params, "billed"), _flag(params, "running")
        if billed is not None:
            qs = qs.filter(invoice__isnull=not billed)
        if running is not None:
            qs = qs.filter(ended_at__isnull=running)   # running = no end time yet
        return qs

    def _check_can_modify(self, entry):
        membership = get_membership(self.request)
        role = membership.role if membership else None
        if entry.user_id != self.request.user.pk and role not in MANAGER_ROLES:
            raise PermissionDenied("You can only change your own time entries.")

    def list(self, request, *args, **kwargs):
        response = super().list(request, *args, **kwargs)
        stopped = self.filter_queryset(self.get_queryset()).filter(ended_at__isnull=False)
        worth = ExpressionWrapper(F("duration_seconds") * F("hourly_rate_minor"), output_field=BigIntegerField())
        unbilled = stopped.filter(billable=True, invoice__isnull=True)
        total = stopped.aggregate(seconds=Sum("duration_seconds"), billable_secs=Sum("duration_seconds", filter=Q(billable=True)))
        value = unbilled.aggregate(v=Sum(worth), seconds=Sum("duration_seconds"))
        response.data["summary"] = {
            "total_seconds": total["seconds"] or 0,
            "billable_seconds": total["billable_secs"] or 0,
            "unbilled_seconds": value["seconds"] or 0,
            "unbilled_value_minor": value_from_product(value["v"] or 0),
        }
        return response

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        entry = services.create_manual_entry(request.user, **serializer.validated_data)
        return Response(TimeEntrySerializer(entry).data, status=status.HTTP_201_CREATED)

    def partial_update(self, request, *args, **kwargs):
        entry = self.get_object()
        self._check_can_modify(entry)
        serializer = self.get_serializer(data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        return Response(TimeEntrySerializer(services.update_entry(entry, **serializer.validated_data)).data)

    def destroy(self, request, *args, **kwargs):
        entry = self.get_object()
        self._check_can_modify(entry)
        services.delete_entry(entry)
        return Response(status=status.HTTP_204_NO_CONTENT)

    @action(detail=False, methods=["post"])
    def start(self, request):
        """Start a timer. Any timer you still had running is stopped first."""
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        entry, stopped = services.start_timer(request.user, **serializer.validated_data)
        data = dict(TimeEntrySerializer(entry).data)
        data["stopped_previous"] = stopped.pk if stopped else None
        return Response(data, status=status.HTTP_201_CREATED)

    @action(detail=False, methods=["get"])
    def running(self, request):
        entry = self.get_queryset().filter(user_id=request.user.pk, ended_at__isnull=True).first()
        return Response({"running": TimeEntrySerializer(entry).data if entry else None})

    @action(detail=True, methods=["post"])
    def stop(self, request, pk=None):
        entry = self.get_object()
        self._check_can_modify(entry)
        return Response(TimeEntrySerializer(services.stop_timer(entry)).data)


class ExpenseViewSet(_Base, viewsets.ModelViewSet):
    """?q= &category= &project= &client= &from= &to= &billable= &billed="""

    serializer_class = ExpenseSerializer
    action_roles = {"destroy": MANAGER_ROLES}
    filter_backends = [QSearchFilter, OrderingFilter]
    search_fields = ["description", "vendor", "category", "notes"]
    ordering_fields = ["incurred_on", "amount_minor", "created_at"]
    ordering = ["-incurred_on", "-id"]

    def get_queryset(self):
        qs = Expense.objects.select_related("project", "project__client", "client")
        if self.action != "list":
            return qs
        params = self.request.query_params
        if params.get("category"):
            qs = qs.filter(category__iexact=params["category"])
        if _int(params, "project"):
            qs = qs.filter(project_id=_int(params, "project"))
        if _int(params, "client"):
            qs = qs.annotate(effective_client=Coalesce("client_id", "project__client_id")).filter(effective_client=_int(params, "client"))
        if _date(params, "from"):
            qs = qs.filter(incurred_on__gte=_date(params, "from"))
        if _date(params, "to"):
            qs = qs.filter(incurred_on__lte=_date(params, "to"))
        billable, billed = _flag(params, "billable"), _flag(params, "billed")
        if billable is not None:
            qs = qs.filter(billable=billable)
        if billed is not None:
            qs = qs.filter(invoice__isnull=not billed)
        return qs

    def list(self, request, *args, **kwargs):
        response = super().list(request, *args, **kwargs)
        rows = self.filter_queryset(self.get_queryset()).order_by().values("currency").annotate(
            amount=Sum("amount_minor"), tax=Sum("tax_minor"), count=Count("id"))
        response.data["summary"] = [
            {"currency": r["currency"], "amount_minor": r["amount"], "tax_minor": r["tax"], "count": r["count"]}
            for r in rows
        ]
        return response

    def _require_unbilled(self, expense):
        if expense.invoice_id:
            raise ValidationError({"detail": "This expense is already on an invoice and cannot be changed."})

    def perform_update(self, serializer):
        self._require_unbilled(serializer.instance)
        serializer.save()

    def perform_destroy(self, instance):
        self._require_unbilled(instance)
        instance.delete()

    @action(detail=False, methods=["get"])
    def categories(self, request):
        """Categories used so far, for a dropdown."""
        names = Expense.objects.order_by().values_list("category", flat=True).distinct()
        return Response(sorted(set(names) | {"General"}))


class WorkInvoiceView(APIView):
    """POST /api/work/invoice/ : one draft invoice from unbilled time and billable expenses."""

    permission_classes = [TenantRolePermission]
    write_roles = FINANCE_ROLES

    def post(self, request):
        serializer = WorkInvoiceSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        tax_rate = None
        if data["tax_rate_id"] is not None:
            tax_rate = TaxRate.objects.filter(pk=data["tax_rate_id"], is_active=True).first()
            if tax_rate is None:
                raise ValidationError({"tax_rate_id": "Unknown or inactive tax rate."})
        invoice, n_time, n_expenses = services.invoice_from_work(
            request.user, data["client"], data["time_entry_ids"], data["expense_ids"],
            tax_rate=tax_rate, group_by=data["group_by"], exchange_rate=data["exchange_rate"],
        )
        return Response(
            {"invoice_id": invoice.pk, "status": invoice.status, "currency": invoice.currency,
             "total_minor": invoice.total_minor, "time_entries": n_time, "expenses": n_expenses},
            status=status.HTTP_201_CREATED,
        )
