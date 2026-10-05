from rest_framework import viewsets

from apps.common.permissions import ALL_ROLES, FINANCE_ROLES, TenantRolePermission

from .models import Client
from .serializers import ClientSerializer


class ClientViewSet(viewsets.ModelViewSet):
    queryset = Client.objects.order_by("-created_at")
    serializer_class = ClientSerializer
    permission_classes = [TenantRolePermission]
    read_roles = ALL_ROLES        # viewers can look
    write_roles = FINANCE_ROLES   # owner, admin, accountant can change
