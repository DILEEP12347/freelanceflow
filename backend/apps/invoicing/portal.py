"""The client portal: a public, login-free page where a client views an invoice and downloads the PDF.

Security model: the only credential is the unguessable token in the URL (192 random bits). Therefore:
- unknown, draft or wrong-organization tokens all answer 404, never "exists but forbidden";
- the page is only available while the organization's plan includes `client_portal`;
- owners can rotate a token to kill a leaked link;
- responses are never cached and carry noindex/no-referrer headers;
- requests are rate limited per IP.
"""
import html

from django.db import connection
from django.http import Http404, HttpResponse
from rest_framework.negotiation import BaseContentNegotiation
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.throttling import AnonRateThrottle
from rest_framework.views import APIView

from apps.billing.limits import has_feature

from . import services
from .calculations import format_money, format_quantity, tax_display_lines, tax_summary
from .models import Invoice, InvoiceStatus
from .pdf import render_invoice_pdf


class PortalThrottle(AnonRateThrottle):
    scope = "portal"
    rate = "60/min"


class IgnoreClientContentNegotiation(BaseContentNegotiation):
    """Browsers send odd Accept headers; always answer with what the view returns."""

    def select_parser(self, request, parsers):
        return parsers[0]

    def select_renderer(self, request, renderers, format_suffix):
        return renderers[0], renderers[0].media_type


def _load(token: str) -> Invoice:
    if not has_feature(connection.tenant, "client_portal") or not (16 <= len(token) <= 64):
        raise Http404
    invoice = (
        Invoice.objects.select_related("client").prefetch_related("lines", "payments")
        .filter(portal_token=token).exclude(status=InvoiceStatus.DRAFT).first()
    )
    if invoice is None:
        raise Http404
    return invoice


def _private(response):
    response["Cache-Control"] = "no-store"
    response["X-Robots-Tag"] = "noindex, nofollow"
    response["Referrer-Policy"] = "no-referrer"
    return response


def portal_payload(invoice: Invoice, token: str) -> dict:
    snap = invoice.snapshot or {}
    seller = dict(snap.get("from", {}))
    buyer = {k: v for k, v in snap.get("to", {}).items() if k not in ("email", "phone")}  # the client knows their own
    lines = list(invoice.lines.all())
    return {
        "number": invoice.number, "status": invoice.status, "display_status": invoice.display_status,
        "issue_date": invoice.issue_date, "due_date": invoice.due_date, "currency": invoice.currency,
        "subtotal_minor": invoice.subtotal_minor, "tax_minor": invoice.tax_minor, "total_minor": invoice.total_minor,
        "amount_paid_minor": invoice.amount_paid_minor, "balance_due_minor": invoice.balance_due_minor,
        "notes": invoice.notes, "terms": invoice.terms, "from": seller, "to": buyer,
        "lines": [
            {"description": l.description, "quantity": format_quantity(l.quantity), "unit_price_minor": l.unit_price_minor,
             "tax_rate_bps": l.tax_rate_bps, "amount_minor": l.subtotal_minor}
            for l in lines
        ],
        "tax_lines": [
            {"label": label, "amount_minor": amount}
            for label, amount in tax_display_lines(tax_summary(lines), snap.get("gst_mode"))
        ],
        "payments": [{"paid_on": p.paid_on, "amount_minor": p.amount_minor} for p in invoice.payments.all()],
        "pdf_url": f"/portal/{token}/pdf/",
    }


def render_portal_html(data: dict) -> str:
    e = html.escape
    money = lambda minor: e(format_money(minor, data["currency"]))  # noqa: E731
    seller, buyer = data["from"], data["to"]

    def party(info):
        parts = [info.get("company_name") or info.get("name") or ""]
        parts += [info.get("line1"), info.get("line2"), ", ".join(p for p in (info.get("city"), info.get("state"), info.get("postal_code")) if p), info.get("country")]
        if info.get("tax_id"):
            parts.append(f"Tax ID: {info['tax_id']}")
        return "<br>".join(e(str(p)) for p in parts if p)

    rows = "".join(
        f"<tr><td>{e(l['description'])}</td><td class=r>{e(l['quantity'])}</td>"
        f"<td class=r>{money(l['unit_price_minor'])}</td><td class=r>{money(l['amount_minor'])}</td></tr>"
        for l in data["lines"]
    )
    totals = f"<tr><td>Subtotal</td><td class=r>{money(data['subtotal_minor'])}</td></tr>"
    totals += "".join(f"<tr><td>{e(t['label'])}</td><td class=r>{money(t['amount_minor'])}</td></tr>" for t in data["tax_lines"])
    totals += f"<tr class=b><td>Total</td><td class=r>{money(data['total_minor'])}</td></tr>"
    if data["amount_paid_minor"]:
        totals += f"<tr><td>Paid</td><td class=r>-{money(data['amount_paid_minor'])}</td></tr>"
    totals += f"<tr class=b><td>Balance due</td><td class=r>{money(data['balance_due_minor'])}</td></tr>"
    label = {"overdue": "Overdue", "paid": "Paid", "void": "Void", "partial": "Partially paid"}.get(data["display_status"], "Awaiting payment")
    notes = "".join(f"<h4>{h}</h4><p>{e(data[k]).replace(chr(10), '<br>')}</p>" for h, k in (("Notes", "notes"), ("Terms", "terms")) if data[k])
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="robots" content="noindex,nofollow"><title>Invoice {e(str(data['number']))}</title>
<style>body{{font-family:system-ui,sans-serif;max-width:760px;margin:2rem auto;padding:0 1rem;color:#111827}}
table{{width:100%;border-collapse:collapse;margin:1rem 0}}td,th{{padding:.5rem .25rem;border-bottom:1px solid #e5e7eb;text-align:left}}
.r{{text-align:right}}.b td{{font-weight:700}}.top{{display:flex;justify-content:space-between;gap:2rem;flex-wrap:wrap}}
.badge{{display:inline-block;padding:.2rem .6rem;border-radius:999px;background:#eef2ff;color:#1d4ed8;font-size:.85rem}}
.btn{{display:inline-block;padding:.6rem 1rem;background:#1d4ed8;color:#fff;border-radius:6px;text-decoration:none}}
.small{{color:#6b7280;font-size:.9rem}}</style></head><body>
<div class="top"><div><h1 style="margin:0">Invoice {e(str(data['number']))}</h1><span class="badge">{e(label)}</span></div>
<div class="small">Issued {e(str(data['issue_date']))}<br>Due {e(str(data['due_date']))}</div></div>
<div class="top" style="margin-top:1.5rem"><div><div class="small">FROM</div>{party(seller)}</div><div><div class="small">BILL TO</div>{party(buyer)}</div></div>
<table><tr><th>Description</th><th class=r>Qty</th><th class=r>Rate</th><th class=r>Amount</th></tr>{rows}</table>
<table style="max-width:320px;margin-left:auto">{totals}</table>{notes}
<p><a class="btn" href="{e(data['pdf_url'])}">Download PDF</a></p></body></html>"""


class _PortalView(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]
    throttle_classes = [PortalThrottle]
    content_negotiation_class = IgnoreClientContentNegotiation


class PortalPageView(_PortalView):
    def get(self, request, token):
        invoice = _load(token)
        services.mark_viewed(invoice)
        return _private(HttpResponse(render_portal_html(portal_payload(invoice, token))))


class PortalApiView(_PortalView):
    """JSON twin of the page, for a future React portal."""

    def get(self, request, token):
        invoice = _load(token)
        services.mark_viewed(invoice)
        return _private(Response(portal_payload(invoice, token)))


class PortalPdfView(_PortalView):
    def get(self, request, token):
        invoice = _load(token)
        response = HttpResponse(render_invoice_pdf(invoice), content_type="application/pdf")
        response["Content-Disposition"] = f'inline; filename="{invoice.number}.pdf"'
        return _private(response)
