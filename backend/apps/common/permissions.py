"""Role-based access for tenant APIs.

Authentication (JWT) says WHO you are. Authorization (this file) says what you may do
IN THE TENANT being requested: it looks up your Membership for request.tenant.
A valid token for tenant A is therefore useless on tenant B (403).
"""
from rest_framework.permissions import BasePermission

from apps.tenants.models import Membership, Role

SAFE_METHODS = ("GET", "HEAD", "OPTIONS")

ALL_ROLES = frozenset(Role.values)
MANAGER_ROLES = frozenset({Role.OWNER.value, Role.ADMIN.value})
FINANCE_ROLES = frozenset({Role.OWNER.value, Role.ADMIN.value, Role.ACCOUNTANT.value})

_UNSET = object()


def get_membership(request):
    """The current user's Membership in request.tenant (cached per request), or None."""
    cached = getattr(request, "_ff_membership", _UNSET)
    if cached is not _UNSET:
        return cached
    membership = None
    user = request.user
    tenant = getattr(request, "tenant", None)
    if user and user.is_authenticated and tenant is not None:
        membership = Membership.objects.filter(user=user, tenant=tenant).first()
    request._ff_membership = membership
    return membership


class TenantRolePermission(BasePermission):
    """Views declare which roles may read (safe methods) and write (everything else):

        read_roles  = ALL_ROLES        # default
        write_roles = MANAGER_ROLES    # default
    """

    message = "You do not have permission to do that in this organization."

    def has_permission(self, request, view):
        membership = get_membership(request)
        if membership is None:
            return False
        if request.method in SAFE_METHODS:
            allowed = getattr(view, "read_roles", ALL_ROLES)
        else:
            allowed = getattr(view, "write_roles", MANAGER_ROLES)
        return membership.role in allowed
