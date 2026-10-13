"""Turning "POST /api/invoices/12/void/" into "invoice.voided". Pure (no Django), so it is easy to test.

A rule is (method, regex, action, object_type). Named groups: `id` = the object, `parent` = its parent (an invoice for a
payment). Anything not listed still gets logged, as "post:invoices/<id>/something/", so nothing is ever invisible.
"""
import re

MUTATING = ("POST", "PUT", "PATCH", "DELETE")
_UPDATE = ("PATCH", "PUT")
RULES = []


def _add(methods, pattern, action, object_type=""):
    for method in ([methods] if isinstance(methods, str) else methods):
        RULES.append((method, re.compile(pattern), action, object_type))


def _crud(path, noun, created="created", updated="updated", deleted="deleted"):
    _add("POST", rf"^{path}/$", f"{noun}.{created}", noun)
    _add(_UPDATE, rf"^{path}/(?P<id>\d+)/$", f"{noun}.{updated}", noun)
    _add("DELETE", rf"^{path}/(?P<id>\d+)/$", f"{noun}.{deleted}", noun)


# ---- authentication and team
_add("POST", r"^auth/login/$", "auth.login")
_add(_UPDATE, r"^team/members/(?P<id>\d+)/$", "member.role_changed", "member")
_add("DELETE", r"^team/members/(?P<id>\d+)/$", "member.removed", "member")
_add("POST", r"^team/invites/accept/$", "member.joined")
_add("POST", r"^team/invites/$", "member.invited", "invite")
_add("DELETE", r"^team/invites/(?P<id>\d+)/$", "invite.revoked", "invite")
# ---- organization settings
_add(_UPDATE, r"^business-profile/$", "business.profile_updated", "business")
_add(_UPDATE, r"^invoicing/settings/$", "invoicing.settings_updated", "settings")
_crud("tax-rates", "tax_rate")
# ---- billing
_add("POST", r"^billing/checkout/$", "billing.checkout_started")
_add("POST", r"^billing/portal/$", "billing.portal_opened")
_add("POST", r"^billing/change-plan/$", "billing.plan_change_requested")
# ---- CRM
_crud("clients", "client")
_add("POST", r"^clients/(?P<id>\d+)/archive/$", "client.archived", "client")
_add("POST", r"^clients/(?P<id>\d+)/restore/$", "client.restored", "client")
_add("POST", r"^clients/(?P<parent>\d+)/contacts/$", "contact.created", "contact")
_add(_UPDATE, r"^clients/(?P<parent>\d+)/contacts/(?P<id>\d+)/$", "contact.updated", "contact")
_add("DELETE", r"^clients/(?P<parent>\d+)/contacts/(?P<id>\d+)/$", "contact.deleted", "contact")
_add("POST", r"^clients/(?P<parent>\d+)/notes/$", "note.created", "note")
_add("DELETE", r"^clients/(?P<parent>\d+)/notes/(?P<id>\d+)/$", "note.deleted", "note")
_crud("tags", "tag")
_crud("leads", "lead")
_add("POST", r"^leads/(?P<id>\d+)/move/$", "lead.moved", "lead")
_add("POST", r"^leads/(?P<id>\d+)/convert/$", "lead.converted", "lead")
# ---- invoicing
_crud("invoices", "invoice")
_add("POST", r"^invoices/(?P<id>\d+)/send/$", "invoice.sent", "invoice")
_add("POST", r"^invoices/(?P<id>\d+)/void/$", "invoice.voided", "invoice")
_add("POST", r"^invoices/(?P<id>\d+)/email/$", "invoice.emailed", "invoice")
_add("POST", r"^invoices/(?P<id>\d+)/portal-link/$", "invoice.portal_link_replaced", "invoice")
_add("POST", r"^invoices/(?P<parent>\d+)/payments/$", "payment.recorded", "payment")
_add("DELETE", r"^invoices/(?P<parent>\d+)/payments/(?P<id>\d+)/$", "payment.deleted", "payment")
_crud("recurring-invoices", "recurring")
_add("POST", r"^recurring-invoices/(?P<id>\d+)/pause/$", "recurring.paused", "recurring")
_add("POST", r"^recurring-invoices/(?P<id>\d+)/resume/$", "recurring.resumed", "recurring")
_add("POST", r"^recurring-invoices/(?P<id>\d+)/run-now/$", "recurring.run_now", "recurring")
# ---- projects, time, expenses
_crud("projects", "project")
_add("POST", r"^projects/(?P<id>\d+)/archive/$", "project.archived", "project")
_add("POST", r"^projects/(?P<id>\d+)/restore/$", "project.restored", "project")
_add("POST", r"^time/start/$", "time.started", "time_entry")
_add("POST", r"^time/(?P<id>\d+)/stop/$", "time.stopped", "time_entry")
_crud("time", "time_entry")
_crud("expenses", "expense")
_add("POST", r"^work/invoice/$", "work.invoiced", "invoice")

_ID = re.compile(r"/\d+(?=/|$)")


def classify(method: str, path: str):
    """-> dict(action, object_type, object_id, parent_id). `path` may start with /api/."""
    rel = path[5:] if path.startswith("/api/") else path.lstrip("/")
    for rule_method, regex, action, object_type in RULES:
        if rule_method == method:
            match = regex.match(rel)
            if match:
                groups = match.groupdict()
                return {"action": action, "object_type": object_type,
                        "object_id": groups.get("id") or "", "parent_id": groups.get("parent") or ""}
    generic = _ID.sub("/<id>", "/" + rel).strip("/")
    return {"action": f"{method.lower()}:{generic}"[:80], "object_type": "", "object_id": "", "parent_id": ""}
