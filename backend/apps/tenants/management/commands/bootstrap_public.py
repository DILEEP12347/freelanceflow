from django.conf import settings
from django.core.management.base import BaseCommand

from apps.tenants.models import Domain, Tenant


class Command(BaseCommand):
    help = "Create the 'public' tenant + base domain (run once after migrate_schemas --shared)."

    def handle(self, *args, **opts):
        tenant, _ = Tenant.objects.get_or_create(schema_name="public", defaults={"name": "Public"})
        Domain.objects.get_or_create(
            domain=settings.BASE_DOMAIN, defaults={"tenant": tenant, "is_primary": True}
        )
        self.stdout.write(self.style.SUCCESS(f"Public tenant ready on {settings.BASE_DOMAIN}"))
