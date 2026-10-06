"""Invoice PDF (ReportLab). Layout only: every number comes from the invoice, already computed.

Known limits (fine for now, noted in the README): the built-in Helvetica font has no glyphs for
Hindi/Tamil/etc. names or the rupee sign, so money is printed as 'INR 1,000.00'.
"""
from io import BytesIO
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.enums import TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from .calculations import format_money, format_quantity, tax_display_lines, tax_summary

INK = colors.HexColor("#111827")
MUTED = colors.HexColor("#6b7280")
LINE = colors.HexColor("#e5e7eb")
ACCENT = colors.HexColor("#1d4ed8")
STAMP_COLORS = {"draft": MUTED, "void": colors.HexColor("#b91c1c"), "paid": colors.HexColor("#15803d")}

_base = getSampleStyleSheet()["Normal"]
BODY = ParagraphStyle("body", parent=_base, fontName="Helvetica", fontSize=9, leading=12, textColor=INK)
SMALL = ParagraphStyle("small", parent=BODY, fontSize=8, leading=10.5, textColor=MUTED)
RIGHT = ParagraphStyle("right", parent=BODY, alignment=TA_RIGHT)
BOLD = ParagraphStyle("bold", parent=BODY, fontName="Helvetica-Bold")
BOLD_RIGHT = ParagraphStyle("boldright", parent=BOLD, alignment=TA_RIGHT)
TITLE = ParagraphStyle("title", parent=BODY, fontName="Helvetica-Bold", fontSize=22, leading=26, textColor=ACCENT)
H = ParagraphStyle("h", parent=SMALL, fontName="Helvetica-Bold", textColor=MUTED)


def esc(text) -> str:
    """Escape user text for ReportLab's mini-markup and keep line breaks."""
    return escape(str(text or "")).replace("\n", "<br/>")


def _party(info, heading):
    lines = [f"<b>{esc(info.get('company_name') or info.get('name'))}</b>"]
    if info.get("company_name") and info.get("name") and info["company_name"] != info["name"]:
        lines.append(esc(info["name"]))
    for key in ("line1", "line2"):
        if info.get(key):
            lines.append(esc(info[key]))
    city = ", ".join(p for p in (info.get("city"), info.get("state"), info.get("postal_code")) if p)
    if city:
        lines.append(esc(city))
    if info.get("country"):
        lines.append(esc(info["country"]))
    if info.get("tax_id"):
        lines.append(f"Tax ID: {esc(info['tax_id'])}")
    for key in ("email", "phone"):
        if info.get(key):
            lines.append(esc(info[key]))
    return [Paragraph(heading, H), Paragraph("<br/>".join(lines), BODY)]


def _live_snapshot(invoice):
    # Drafts have no frozen snapshot yet: preview with the current details.
    from .services import build_snapshot, get_profile

    return build_snapshot(invoice.client, get_profile(), invoice.currency)


def _stamp(canvas, doc, text, color):
    canvas.saveState()
    canvas.setFont("Helvetica-Bold", 80)
    canvas.setFillColor(color)
    canvas.setFillAlpha(0.10)
    canvas.translate(A4[0] / 2, A4[1] / 2)
    canvas.rotate(35)
    canvas.drawCentredString(0, 0, text)
    canvas.restoreState()


def render_invoice_pdf(invoice) -> bytes:
    snapshot = invoice.snapshot or _live_snapshot(invoice)
    seller, buyer = snapshot.get("from", {}), snapshot.get("to", {})
    cur = invoice.currency
    money = lambda minor: format_money(minor, cur)  # noqa: E731
    lines = list(invoice.lines.all())

    buf = BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=A4, leftMargin=18 * mm, rightMargin=18 * mm, topMargin=16 * mm, bottomMargin=16 * mm,
        title=f"Invoice {invoice.number or 'draft'}", author=seller.get("name", ""),
    )
    width = A4[0] - 36 * mm
    story = []

    # --- header: seller on the left, title and numbers on the right
    meta = [
        [Paragraph("INVOICE", TITLE)],
        [Paragraph(f"<b>{esc(invoice.number or 'DRAFT')}</b>", RIGHT)],
        [Paragraph(f"Issued: {invoice.issue_date.strftime('%d %b %Y') if invoice.issue_date else '-'}", RIGHT)],
        [Paragraph(f"Due: {invoice.due_date.strftime('%d %b %Y') if invoice.due_date else '-'}", RIGHT)],
    ]
    seller_info = dict(seller)
    seller_info["company_name"] = seller.get("legal_name") or seller.get("name")
    header = Table([[_party(seller_info, "FROM"), Table(meta, colWidths=[width * 0.4])]],
                   colWidths=[width * 0.6, width * 0.4])
    header.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("LEFTPADDING", (0, 0), (-1, -1), 0),
                                ("RIGHTPADDING", (0, 0), (-1, -1), 0)]))
    story += [header, Spacer(1, 8 * mm)]

    # --- bill to
    bill = Table([[_party(buyer, "BILL TO")]], colWidths=[width])
    bill.setStyle(TableStyle([("LEFTPADDING", (0, 0), (-1, -1), 0)]))
    story += [bill, Spacer(1, 7 * mm)]

    # --- line items
    rows = [[Paragraph(h, H) for h in ("DESCRIPTION", "QTY", "RATE", "TAX", "AMOUNT")]]
    rows[0][1:] = [Paragraph(t, ParagraphStyle("hr", parent=H, alignment=TA_RIGHT)) for t in ("QTY", "RATE", "TAX", "AMOUNT")]
    for line in lines:
        tax_cell = f"{format(line.tax_rate_bps / 100, 'g')}%" if line.tax_rate_bps else "-"
        rows.append([
            Paragraph(esc(line.description), BODY),
            Paragraph(format_quantity(line.quantity), RIGHT),
            Paragraph(money(line.unit_price_minor), RIGHT),
            Paragraph(tax_cell, RIGHT),
            Paragraph(money(line.subtotal_minor), RIGHT),
        ])
    items = Table(rows, colWidths=[width * 0.40, width * 0.09, width * 0.18, width * 0.09, width * 0.24], repeatRows=1)
    items.setStyle(TableStyle([
        ("LINEBELOW", (0, 0), (-1, 0), 0.8, INK), ("LINEBELOW", (0, 1), (-1, -1), 0.4, LINE),
        ("VALIGN", (0, 0), (-1, -1), "TOP"), ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5), ("LEFTPADDING", (0, 0), (-1, -1), 2),
        ("RIGHTPADDING", (0, 0), (-1, -1), 2),
    ]))
    story += [items, Spacer(1, 5 * mm)]

    # --- totals
    tax_lines = tax_display_lines(tax_summary(lines), snapshot.get("gst_mode"))
    t_rows = [[Paragraph("Subtotal", BODY), Paragraph(money(invoice.subtotal_minor), RIGHT)]]
    t_rows += [[Paragraph(label, BODY), Paragraph(money(amount), RIGHT)] for label, amount in tax_lines]
    t_rows.append([Paragraph("Total", BOLD), Paragraph(money(invoice.total_minor), BOLD_RIGHT)])
    if invoice.amount_paid_minor:
        t_rows.append([Paragraph("Paid", BODY), Paragraph("-" + money(invoice.amount_paid_minor), RIGHT)])
    t_rows.append([Paragraph("Balance due", BOLD), Paragraph(money(invoice.balance_due_minor), BOLD_RIGHT)])
    totals = Table(t_rows, colWidths=[width * 0.20, width * 0.28], hAlign="RIGHT")
    totals.setStyle(TableStyle([
        ("TOPPADDING", (0, 0), (-1, -1), 3), ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ("LINEABOVE", (0, len(t_rows) - 1), (-1, len(t_rows) - 1), 0.8, INK),
        ("LEFTPADDING", (0, 0), (-1, -1), 2), ("RIGHTPADDING", (0, 0), (-1, -1), 2),
    ]))
    story.append(totals)
    if cur != "INR" and invoice.exchange_rate and float(invoice.exchange_rate) != 1:
        story.append(Paragraph(f"Exchange rate at issue: 1 {cur} = {invoice.exchange_rate.normalize():f} base currency", SMALL))

    # --- notes and terms
    for heading, text in (("NOTES", invoice.notes), ("TERMS", invoice.terms), ("VOID REASON", invoice.void_reason)):
        if text:
            story += [Spacer(1, 6 * mm), Paragraph(heading, H), Paragraph(esc(text), BODY)]

    stamp = invoice.status if invoice.status in STAMP_COLORS else None
    if stamp:
        on_page = lambda c, d: _stamp(c, d, stamp.upper(), STAMP_COLORS[stamp])  # noqa: E731
        doc.build(story, onFirstPage=on_page, onLaterPages=on_page)
    else:
        doc.build(story)
    return buf.getvalue()
