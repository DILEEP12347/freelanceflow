import logging

from django.db import transaction
from django_tenants.utils import tenant_context

from .models import AuditLog

logger = logging.getLogger(__name__)


def record(*, action, actor_id=None, actor_email="", outcome="ok", status_code=200, method="", path="",
           object_type="", object_id="", parent_id="", object_repr="", changes=None, ip_address=None, user_agent=""):
    return AuditLog.objects.create(
        action=action, actor_id=actor_id, actor_email=(actor_email or "")[:254], outcome=outcome,
        status_code=status_code, method=method, path=path[:255], object_type=object_type, object_id=str(object_id or ""),
        parent_id=str(parent_id or ""), object_repr=(object_repr or "")[:200], changes=changes or {},
        ip_address=ip_address, user_agent=(user_agent or "")[:255],
    )


def record_for_tenant(tenant, **kwargs):
    """Write an entry into another organization's log (used by the Stripe webhook, which runs on the bare domain).
    The savepoint means a failure here can never poison the transaction it is called from."""
    try:
        with transaction.atomic():
            with tenant_context(tenant):
                record(**kwargs)
    except Exception:
        logger.exception("Could not write audit entry %s for tenant %s", kwargs.get("action"), getattr(tenant, "pk", None))
