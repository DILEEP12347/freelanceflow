from django.db import models
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
