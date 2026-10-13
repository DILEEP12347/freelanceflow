"""Records every state-changing API call in the organization's audit log.

Why a middleware and not calls scattered through the code: nothing can be forgotten, new endpoints are covered
automatically (unknown ones are logged under a generic name), and the rules live in one tested file (rules.py).

It never breaks a request: a logging failure is logged and swallowed. Only successful calls (2xx) and refused ones
(401/403) are recorded. Validation errors and 404s are noise, not security events.
"""
import ipaddress
import logging

from django_tenants.utils import get_public_schema_name

from .redact import sanitize_body
from .rules import MUTATING, classify
from .services import record

logger = logging.getLogger(__name__)
MAX_BODY = 100_000
REPR_KEYS = ("number", "name", "title", "email", "description")


def _is_tenant_request(request) -> bool:
    tenant = getattr(request, "tenant", None)
    return tenant is not None and tenant.schema_name != get_public_schema_name()


def _ip(request):
    raw = request.META.get("REMOTE_ADDR")
    try:
        return str(ipaddress.ip_address(raw)) if raw else None
    except ValueError:
        return None


class AuditMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        capture = request.method in MUTATING and request.path.startswith("/api/") and _is_tenant_request(request)
        body = b""
        if capture:
            try:
                # Read the body BEFORE the view: DRF consumes the stream, after which it can no longer be read.
                if "json" in (request.content_type or "") and int(request.META.get("CONTENT_LENGTH") or 0) <= MAX_BODY:
                    body = request.body
            except Exception:
                body = b""
        response = self.get_response(request)
        if capture:
            try:
                self._log(request, response, body)
            except Exception:
                logger.exception("Audit logging failed")
        return response

    def _log(self, request, response, body):
        code = response.status_code
        if 200 <= code < 300:
            outcome = "ok"
        elif code in (401, 403):
            outcome = "denied"
        else:
            return
        info = classify(request.method, request.path)
        if request.path.startswith("/api/auth/") and info["action"] != "auth.login":
            return  # token refreshes and the like are not worth recording
        user = getattr(request, "user", None)
        known = bool(user is not None and getattr(user, "is_authenticated", False))
        changes = sanitize_body(body)
        actor_email = user.email if known else ""
        if info["action"] == "auth.login" and not actor_email:
            actor_email = str(changes.get("email", ""))  # a failed login has no user, but we know who was tried
        data = getattr(response, "data", None)
        object_id = info["object_id"]
        if not object_id and outcome == "ok" and isinstance(data, dict) and data.get("id") is not None:
            object_id = str(data["id"])
        object_repr = ""
        if outcome == "ok" and isinstance(data, dict) and info["action"] != "auth.login":
            object_repr = next((data[k] for k in REPR_KEYS if isinstance(data.get(k), str) and data.get(k)), "")
        if not object_repr and object_id and info["object_type"]:
            object_repr = f"{info['object_type']} #{object_id}"
        record(
            action=info["action"], actor_id=user.pk if known else None, actor_email=actor_email, outcome=outcome,
            status_code=code, method=request.method, path=request.path, object_type=info["object_type"],
            object_id=object_id, parent_id=info["parent_id"], object_repr=object_repr, changes=changes,
            ip_address=_ip(request), user_agent=request.META.get("HTTP_USER_AGENT", ""),
        )
