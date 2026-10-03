import re

from django.conf import settings
from django.db import transaction
from rest_framework.exceptions import ValidationError

from .models import Domain, Tenant

SUBDOMAIN_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,28}[a-z0-9]$")
RESERVED = {"www", "api", "admin", "app", "static", "mail", "public", "billing", "support"}


def validate_subdomain(subdomain: str) -> str:
    subdomain = subdomain.strip().lower()
    if not SUBDOMAIN_RE.match(subdomain):
        raise ValidationError(
            {"subdomain": "3-30 chars: lowercase letters, digits, hyphens; cannot start/end with a hyphen."}
        )
    if subdomain in RESERVED:
        raise ValidationError({"subdomain": "This subdomain is reserved."})
    if Domain.objects.filter(domain=f"{subdomain}.{settings.BASE_DOMAIN}").exists():
        raise ValidationError({"subdomain": "This subdomain is already taken."})
    return subdomain


def create_tenant(name: str, subdomain: str) -> Tenant:
    """Create tenant row + Postgres schema + domain. Schema creation runs migrations (a few seconds).
    Later weeks: move this into a Celery task and poll for a 'provisioning' status."""
    subdomain = validate_subdomain(subdomain)
    schema_name = "t_" + subdomain.replace("-", "_")  # schema names can't contain hyphens
    with transaction.atomic():
        tenant = Tenant(name=name, schema_name=schema_name)
        tenant.save()  # triggers schema creation
        Domain.objects.create(
            domain=f"{subdomain}.{settings.BASE_DOMAIN}", tenant=tenant, is_primary=True
        )
    return tenant
