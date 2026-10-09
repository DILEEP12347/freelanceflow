from django.http import HttpResponse
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.utils.dateparse import parse_date
from rest_framework import mixins, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from rest_framework.filters import OrderingFilter
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.common.filters import QSearchFilter
from apps.billing.limits import has_feature, requires_feature
from apps.common.pagination import StandardPagination
from apps.common.permissions import ALL_ROLES, FINANCE_ROLES, MANAGER_ROLES, TenantRolePermission

from . import services
from .emails import recipient_for
from .models import Invoice, InvoiceSettings, InvoiceStatus, Payment, RecurringInvoice, RecurringStatus, TaxRate
from .timeutils import business_today
from .pdf import render_invoice_pdf
from .serializers import (
    EmailInvoiceSerializer,
    InvoiceSettingsSerializer,
    RecurringInvoiceSerializer,
    InvoiceListSerializer,
    InvoiceSerializer,
    PaymentSerializer,
    SendInvoiceSerializer,
    TaxRateSerializer,
    VoidInvoiceSerializer,
)

OPEN = (InvoiceStatus.SENT.value, InvoiceStatus.PARTIAL.value)


class _FinanceViewSet(viewsets.ModelViewSet):
    """Everyone reads, finance roles (owner/admin/accountant) write, managers (owner/admin) delete."""

    permission_classes = [TenantRolePermission]
    read_roles = ALL_ROLES
    write_roles = FINANCE_ROLES
    action_roles = {"destroy": MANAGER_ROLES}


class TaxRateViewSet(_FinanceViewSet):
    serializer_class = TaxRateSerializer
    pagination_class = None
    queryset = TaxRate.objects.all()

    def list(self, request, *args, **kwargs):
        services.ensure_default_tax_rates()
        return super().list(request, *args, **kwargs)

    def perform_create(self, serializer):
        services.set_default_tax_rate(serializer.save())

    def perform_update(self, serializer):
        services.set_default_tax_rate(serializer.save())


class InvoiceViewSet(_FinanceViewSet):
    """?q= (number, client, notes) &status=draft|sent|partial|paid|void|overdue|open &client=<id>
    &issued_from=YYYY-MM-DD &issued_to=YYYY-MM-DD &ordering=-due_date &page=2"""

    pagination_class = StandardPagination
    filter_backends = [QSearchFilter, OrderingFilter]
    search_fields = ["number", "client__name", "client__company_name", "notes"]
    ordering_fields = ["created_at", "issue_date", "due_date", "total_minor", "number"]
    ordering = ["-created_at"]
    action_roles = {"destroy": MANAGER_ROLES, "void": MANAGER_ROLES}
    http_method_names = ["get", "post", "patch", "delete", "head", "options"]

    def get_serializer_class(self):
        return InvoiceListSerializer if self.action == "list" else InvoiceSerializer

    def get_queryset(self):
        qs = Invoice.objects.select_related("client").prefetch_related("lines", "payments")
        if self.action != "list":
            return qs
        params = self.request.query_params
        status_param = params.get("status")
        if status_param == "overdue":
            qs = qs.filter(status__in=OPEN, due_date__lt=business_today())
        elif status_param == "open":
            qs = qs.filter(status__in=OPEN)
        elif status_param:
            if status_param not in InvoiceStatus.values:
                raise ValidationError({"status": "Use draft, sent, partial, paid, void, overdue or open."})
            qs = qs.filter(status=status_param)
        client = params.get("client")
        if client:
            if not client.isdigit():
                raise ValidationError({"client": "Use a client id."})
            qs = qs.filter(client_id=int(client))
        for param, lookup in (("issued_from", "issue_date__gte"), ("issued_to", "issue_date__lte")):
            if params.get(param):
                day = parse_date(params[param])
                if day is None:
                    raise ValidationError({param: "Use YYYY-MM-DD."})
                qs = qs.filter(**{lookup: day})
        return qs

    def perform_destroy(self, instance):
        if instance.status != InvoiceStatus.DRAFT:
            raise ValidationError({"detail": "Only drafts can be deleted. Void a sent invoice instead."})
        instance.delete()

    @action(detail=False, methods=["get"])
    def summary(self, request):
        """Dashboard numbers per currency: outstanding, overdue, paid this month, drafts."""
        return Response(services.invoice_summary())

    @action(detail=True, methods=["post"])
    def send(self, request, pk=None):
        """draft -> sent. Assigns the invoice number. Optional body: {"issue_date", "due_date"}."""
        body = SendInvoiceSerializer(data=request.data)
        body.is_valid(raise_exception=True)
        options = dict(body.validated_data)
        want_email = options.pop("email", False)
        invoice = services.send_invoice(self.get_object(), request.user, **options)
        payload = dict(InvoiceSerializer(self._fresh(invoice)).data)
        payload["email_queued"] = bool(want_email and recipient_for(invoice))
        if payload["email_queued"]:
            services.queue_invoice_email(invoice)
        elif want_email:
            payload["email_note"] = "No email address on the client or its contacts, so nothing was emailed."
        return Response(payload)

    @action(detail=True, methods=["post"])
    def email(self, request, pk=None):
        """Email the invoice (PDF attached) to the client's primary contact. Optional body: {"message"}."""
        body = EmailInvoiceSerializer(data=request.data)
        body.is_valid(raise_exception=True)
        invoice = self.get_object()
        if invoice.status not in (InvoiceStatus.SENT, InvoiceStatus.PARTIAL):
            raise ValidationError({"detail": "Only sent invoices with a balance due can be emailed."})
        to = recipient_for(invoice)
        if not to:
            raise ValidationError({"detail": "No email address. Add one to the client or to one of its contacts."})
        services.queue_invoice_email(invoice, message=body.validated_data.get("message", ""))
        return Response({"detail": "Email queued.", "to": to}, status=202)

    @action(detail=True, methods=["get", "post"], url_path="portal-link")
    def portal_link(self, request, pk=None):
        """GET: the public client-portal link. POST: replace it (the old link stops working)."""
        invoice = self.get_object()
        if invoice.status == InvoiceStatus.DRAFT:
            raise ValidationError({"detail": "Send the invoice first: drafts have no portal link."})
        if request.method == "POST":
            services.rotate_portal_token(invoice)
        enabled = has_feature(request.tenant, "client_portal")
        return Response({"enabled": enabled, "url": services.portal_url(invoice) if enabled else None})

    @action(detail=True, methods=["post"])
    def void(self, request, pk=None):
        body = VoidInvoiceSerializer(data=request.data)
        body.is_valid(raise_exception=True)
        invoice = services.void_invoice(self.get_object(), request.user, body.validated_data.get("reason", ""))
        return Response(InvoiceSerializer(self._fresh(invoice)).data)

    @action(detail=True, methods=["get"])
    def pdf(self, request, pk=None):
        invoice = self.get_object()
        response = HttpResponse(render_invoice_pdf(invoice), content_type="application/pdf")
        name = invoice.number or f"draft-{invoice.pk}"
        response["Content-Disposition"] = f'inline; filename="{name}.pdf"'
        return response

    @staticmethod
    def _fresh(invoice):
        """Re-read with lines and payments, so a response never shows stale prefetched data."""
        return Invoice.objects.select_related("client").prefetch_related("lines", "payments").get(pk=invoice.pk)


class PaymentViewSet(mixins.ListModelMixin, mixins.CreateModelMixin, mixins.DestroyModelMixin, viewsets.GenericViewSet):
    """Nested under /api/invoices/<invoice_id>/payments/. Finance roles record; managers delete."""

    serializer_class = PaymentSerializer
    pagination_class = None
    permission_classes = [TenantRolePermission]
    read_roles = ALL_ROLES
    write_roles = FINANCE_ROLES
    action_roles = {"destroy": MANAGER_ROLES}

    def _invoice(self):
        return get_object_or_404(Invoice, pk=self.kwargs["invoice_id"])

    def get_queryset(self):
        self._invoice()
        return Payment.objects.filter(invoice_id=self.kwargs["invoice_id"])

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        payment = services.record_payment(self._invoice(), request.user, **serializer.validated_data)
        return Response(PaymentSerializer(payment).data, status=201)

    def perform_destroy(self, instance):
        services.delete_payment(instance, self.request.user)


class InvoiceSettingsView(APIView):
    """GET/PATCH /api/invoicing/settings/ : reminders, timezone and the email signature."""

    permission_classes = [TenantRolePermission]
    read_roles = ALL_ROLES
    write_roles = FINANCE_ROLES

    def get(self, request):
        return Response(InvoiceSettingsSerializer(InvoiceSettings.load()).data)

    def patch(self, request):
        serializer = InvoiceSettingsSerializer(InvoiceSettings.load(), data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data)


class RecurringInvoiceViewSet(viewsets.ModelViewSet):
    """Pro plan. ?status=active|paused|ended. Each run creates a draft invoice (or sends it, with auto_send)."""

    serializer_class = RecurringInvoiceSerializer
    pagination_class = StandardPagination
    permission_classes = [TenantRolePermission, requires_feature("recurring_invoices")]
    read_roles = ALL_ROLES
    write_roles = FINANCE_ROLES
    action_roles = {"destroy": MANAGER_ROLES}
    http_method_names = ["get", "post", "patch", "delete", "head", "options"]

    def get_queryset(self):
        qs = RecurringInvoice.objects.select_related("client")
        status_param = self.request.query_params.get("status")
        if status_param:
            if status_param not in RecurringStatus.values:
                raise ValidationError({"status": "Use active, paused or ended."})
            qs = qs.filter(status=status_param)
        return qs

    @action(detail=True, methods=["post"])
    def pause(self, request, pk=None):
        return Response(self.get_serializer(services.pause_recurring(self.get_object())).data)

    @action(detail=True, methods=["post"])
    def resume(self, request, pk=None):
        return Response(self.get_serializer(services.resume_recurring(self.get_object())).data)

    @action(detail=True, methods=["post"], url_path="run-now")
    def run_now(self, request, pk=None):
        """Create the next invoice immediately (it counts as a run and moves the schedule forward)."""
        invoice = services.generate_next(self.get_object().pk, force=True)
        if invoice is None:
            rec = RecurringInvoice.objects.get(pk=pk)
            raise ValidationError({"detail": rec.last_error or "This schedule is not active."})
        return Response({"invoice_id": invoice.pk, "number": invoice.number, "status": invoice.status}, status=201)
