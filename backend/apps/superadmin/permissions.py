from rest_framework.permissions import BasePermission


class IsPlatformAdmin(BasePermission):
    """Platform operators only (Django superusers, made with `manage.py createsuperuser`).
    An organization's owner is NOT a platform admin: these endpoints see every organization."""

    message = "Platform administrators only."

    def has_permission(self, request, view):
        user = request.user
        return bool(user and user.is_authenticated and user.is_active and user.is_superuser)
