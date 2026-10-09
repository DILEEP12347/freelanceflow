from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django_tenants.utils import tenant_context

from apps.invoicing import services


class Command(BaseCommand):
    help = "Run an organization's daily jobs (recurring invoices + payment reminders) now, without waiting for Celery."

    def add_arguments(self, parser):
        parser.add_argument("--subdomain", required=True)
        parser.add_argument("--force", action="store_true", help="run even if it already ran today / before 09:00")

    def handle(self, *args, **opts):
        from apps.tenants.models import Domain

        domain = Domain.objects.filter(domain=f"{opts['subdomain']}.{settings.BASE_DOMAIN}").select_related("tenant").first()
        if not domain:
            raise CommandError(f"No tenant with subdomain {opts['subdomain']}")
        with tenant_context(domain.tenant):
            result = services.run_daily_jobs_if_due(force=opts["force"])
        self.stdout.write("Nothing to do yet (before 09:00 local time, or already ran today). Use --force." if result is None else str(result))
