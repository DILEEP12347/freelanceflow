from django.utils.dateparse import parse_date
from rest_framework import mixins, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response

from apps.billing.limits import requires_feature
from apps.common.pagination import StandardPagination
from apps.common.permissions import MANAGER_ROLES, TenantRolePermission
from apps.invoicing.timeutils import local_day_bounds

from .models import AuditLog
from .serializers import AuditLogSerializer


class AuditLogViewSet(mixins.ListModelMixin, mixins.RetrieveModelMixin, viewsets.GenericViewSet):
    """Read-only, for owners and admins, on plans that include the audit log (Business).
    ?action=invoice. (a prefix) &actor=<part of an email> &object_type= &object_id= &outcome=ok|denied
    &from=YYYY-MM-DD &to=YYYY-MM-DD &q=<text in the object or path>"""

    serializer_class = AuditLogSerializer
    pagination_class = StandardPagination
    permission_classes = [TenantRolePermission, requires_feature("audit_log")]
    read_roles = MANAGER_ROLES
    http_method_names = ["get", "head", "options"]

    def get_queryset(self):
        qs = AuditLog.objects.all()
        if self.action != "list":
            return qs
        p = self.request.query_params
        if p.get("action"):
            qs = qs.filter(action__startswith=p["action"])
        if p.get("actor"):
            qs = qs.filter(actor_email__icontains=p["actor"])
        if p.get("object_type"):
            qs = qs.filter(object_type=p["object_type"])
        if p.get("object_id"):
            qs = qs.filter(object_id=p["object_id"])
        if p.get("outcome"):
            if p["outcome"] not in ("ok", "denied"):
                raise ValidationError({"outcome": "Use ok or denied."})
            qs = qs.filter(outcome=p["outcome"])
        days = {}
        for name in ("from", "to"):
            if p.get(name):
                days[name] = parse_date(p[name])
                if days[name] is None:
                    raise ValidationError({name: "Use YYYY-MM-DD."})
        start, end = local_day_bounds(days.get("from"), days.get("to"))
        if start:
            qs = qs.filter(created_at__gte=start)
        if end:
            qs = qs.filter(created_at__lt=end)
        if p.get("q"):
            from django.db.models import Q

            qs = qs.filter(Q(object_repr__icontains=p["q"]) | Q(path__icontains=p["q"]))
        return qs

    @action(detail=False, methods=["get"])
    def actions(self, request):
        """Every action name that has been recorded, for a filter dropdown."""
        return Response(sorted(AuditLog.objects.order_by().values_list("action", flat=True).distinct()))
