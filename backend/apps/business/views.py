from rest_framework.generics import RetrieveUpdateAPIView

from apps.common.permissions import ALL_ROLES, MANAGER_ROLES, TenantRolePermission

from .models import BusinessProfile
from .serializers import BusinessProfileSerializer


class BusinessProfileView(RetrieveUpdateAPIView):
    """GET for every member; PUT/PATCH for owner + admin. Created on first access."""

    serializer_class = BusinessProfileSerializer
    permission_classes = [TenantRolePermission]
    read_roles = ALL_ROLES
    write_roles = MANAGER_ROLES
    http_method_names = ["get", "put", "patch", "head", "options"]

    def get_object(self):
        profile, _ = BusinessProfile.objects.get_or_create(
            pk=1, defaults={"business_name": self.request.tenant.name}
        )
        return profile
