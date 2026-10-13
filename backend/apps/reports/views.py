
from django.http import HttpResponse
from django.utils.dateparse import parse_date
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.billing.limits import requires_feature
from apps.common.permissions import FINANCE_ROLES, TenantRolePermission
from apps.invoicing.timeutils import business_today, local_day_bounds

from . import calculations as calc
from .periods import default_period, to_csv, validate_period


class _ReportView(APIView):
    """Business plan (`reports` feature), owner/admin/accountant. `?export=csv` downloads the table as a CSV file."""

    permission_classes = [TenantRolePermission, requires_feature("reports")]
    read_roles = FINANCE_ROLES

    @staticmethod
    def _day(request, name):
        raw = request.query_params.get(name)
        if not raw:
            return None
        day = parse_date(raw)
        if day is None:
            raise ValidationError({name: "Use YYYY-MM-DD."})
        return day

    def period(self, request):
        start, end = self._day(request, "from"), self._day(request, "to")
        default_start, default_end = default_period(business_today())
        start, end = start or default_start, end or default_end
        error = validate_period(start, end)
        if error:
            raise ValidationError({"detail": error})
        return start, end

    @staticmethod
    def respond(request, data, table, filename):
        if request.query_params.get("export") == "csv":
            columns, rows = table(data)
            response = HttpResponse(to_csv(columns, rows), content_type="text/csv; charset=utf-8")
            response["Content-Disposition"] = f'attachment; filename="{filename}.csv"'
            return response
        return Response(data)


class RevenueReportView(_ReportView):
    """Invoiced and collected per month. ?from=&to= (default: the last 12 months)."""

    def get(self, request):
        start, end = self.period(request)
        return self.respond(request, calc.revenue_report(start, end), calc.revenue_table, "revenue")


class ReceivablesReportView(_ReportView):
    """Who owes what, by how late. ?as_of=YYYY-MM-DD (default: today)."""

    def get(self, request):
        as_of = self._day(request, "as_of") or business_today()
        return self.respond(request, calc.receivables_report(as_of), calc.receivables_table, "receivables")


class ProfitLossReportView(_ReportView):
    def get(self, request):
        start, end = self.period(request)
        return self.respond(request, calc.profit_loss_report(start, end), calc.profit_loss_table, "profit-loss")


class ClientsReportView(_ReportView):
    """?limit=20 (max 100)"""

    def get(self, request):
        start, end = self.period(request)
        raw = request.query_params.get("limit", "20")
        if not raw.isdigit() or not 1 <= int(raw) <= 100:
            raise ValidationError({"limit": "Use a number from 1 to 100."})
        return self.respond(request, calc.clients_report(start, end, int(raw)), calc.clients_table, "clients")


class TimeReportView(_ReportView):
    def get(self, request):
        start, end = self.period(request)
        start_dt, end_dt = local_day_bounds(start, end)
        return Response(calc.time_report(start_dt, end_dt))
