from django.conf import settings
from django.db import models
from django.utils import timezone
from django_tenants.models import DomainMixin, TenantMixin


class Tenant(TenantMixin):
    name = models.CharField(max_length=100)
    created_at = models.DateTimeField(auto_now_add=True)

    # True => the Postgres schema is created (and migrated) on first save()
    auto_create_schema = True

    def __str__(self):
        return self.name


class Domain(DomainMixin):
    """acme.localhost -> Tenant(acme). A tenant can have many domains (custom domains later)."""


class Role(models.TextChoices):
    OWNER = "owner", "Owner"
    ADMIN = "admin", "Admin"
    ACCOUNTANT = "accountant", "Accountant"
    VIEWER = "viewer", "Viewer"


class Membership(models.Model):
    """Which users belong to which tenant, and with what role. Lives in the public schema,
    so one user can be a member of many organizations."""

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="memberships")
    tenant = models.ForeignKey(Tenant, on_delete=models.CASCADE, related_name="memberships")
    role = models.CharField(max_length=20, choices=Role.choices)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["user", "tenant"], name="uniq_membership_user_tenant"),
        ]

    def __str__(self):
        return f"{self.user} -> {self.tenant} ({self.role})"


class Invitation(models.Model):
    """Pending invite. We store only a HASH of the token: a database leak can't be used to join teams."""

    tenant = models.ForeignKey(Tenant, on_delete=models.CASCADE, related_name="invitations")
    email = models.EmailField()
    role = models.CharField(max_length=20, choices=[c for c in Role.choices if c[0] != Role.OWNER])
    token_hash = models.CharField(max_length=64, unique=True)
    invited_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()
    accepted_at = models.DateTimeField(null=True, blank=True)

    @property
    def is_expired(self):
        return timezone.now() >= self.expires_at

    def __str__(self):
        return f"{self.email} -> {self.tenant} ({self.role})"
