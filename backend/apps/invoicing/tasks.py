"""Celery tasks. They receive only simple values (the schema name and ids), never model objects."""
import logging

from celery import shared_task
from django_tenants.utils import tenant_context

logger = logging.getLogger(__name__)


def _tenant(schema_name):
    from apps.tenants.models import Tenant

    return Tenant.objects.get(schema_name=schema_name)


@shared_task(name="invoicing.send_invoice_email", autoretry_for=(OSError,), retry_backoff=True, max_retries=3)
def send_invoice_email(schema_name, invoice_id, kind="invoice", offset_days=None, message=""):
    """Email an invoice or a payment reminder with the PDF attached. Retries on network/SMTP errors."""
    from . import emails

    with tenant_context(_tenant(schema_name)):
        return emails.deliver_invoice_email(invoice_id, kind, offset_days, message)


@shared_task(name="invoicing.run_scheduled_jobs")
def run_scheduled_jobs():
    """Every hour, for every organization: run its daily jobs if it is past 09:00 local time and they have not
    run yet today. One organization failing never stops the others."""
    from apps.tenants.models import Tenant

    from . import services

    results = {}
    for tenant in Tenant.objects.exclude(schema_name="public"):
        try:
            with tenant_context(tenant):
                outcome = services.run_daily_jobs_if_due()
            if outcome is not None:
                results[tenant.schema_name] = outcome
        except Exception:
            logger.exception("Daily jobs failed for %s", tenant.schema_name)
    return results
