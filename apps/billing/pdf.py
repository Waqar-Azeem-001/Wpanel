"""
PDF rendering for invoices and quotes (reportlab). Reads only stored records - a
PDF is a picture of the document as it was issued, never a recalculation - and
is deterministic (``invariant``), so the same invoice always yields the same bytes.

The built-in PDF fonts cover Western European text only; characters outside that
range are printed as "?" rather than failing (a known limit, see the Phase 07
gap report).
"""
from io import BytesIO
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from .models import BillingSettings, TransactionStatus, TransactionType

INK = colors.HexColor("#1f2933")
MUTED = colors.HexColor("#616e7c")
LINE = colors.HexColor("#cbd2d9")
HEAD_BG = colors.HexColor("#f0f4f8")


def _t(value):
    """Text safe for the built-in fonts and for reportlab's mini-markup."""
    text = str(value or "").encode("cp1252", "replace").decode("cp1252")
    return escape(text)


def _lines(*values):
    return "<br/>".join(_t(v) for v in values if v)


def _styles():
    base = getSampleStyleSheet()["Normal"]
    return {
        "body": ParagraphStyle("body", parent=base, fontName="Helvetica", fontSize=9, leading=12, textColor=INK),
        "muted": ParagraphStyle("muted", parent=base, fontName="Helvetica", fontSize=8.5, leading=11,
                                textColor=MUTED),
        "title": ParagraphStyle("title", parent=base, fontName="Helvetica-Bold", fontSize=20, leading=24,
                                textColor=INK),
        "h": ParagraphStyle("h", parent=base, fontName="Helvetica-Bold", fontSize=8.5, leading=11, textColor=MUTED),
        "num": ParagraphStyle("num", parent=base, fontName="Helvetica", fontSize=9, leading=12, alignment=2,
                              textColor=INK),
        "numbold": ParagraphStyle("numbold", parent=base, fontName="Helvetica-Bold", fontSize=10, leading=13,
                                  alignment=2, textColor=INK),
    }


def _money(currency, amount):
    return f"{currency} {amount:,.2f}"


def _address(doc):
    parts = [doc.billing_name, doc.billing_company, doc.billing_address_line1, doc.billing_address_line2,
             " ".join(p for p in (doc.billing_city, doc.billing_state, doc.billing_postcode) if p),
             doc.billing_country, doc.billing_email]
    if doc.billing_tax_id:
        parts.append(f"Tax ID: {doc.billing_tax_id}")
    return _lines(*parts)


def _build(doc, *, kind, title, meta_rows, footer_extra=None, payments=()):
    seller = BillingSettings.load()
    st = _styles()
    buffer = BytesIO()
    pdf = SimpleDocTemplate(buffer, pagesize=A4, leftMargin=18 * mm, rightMargin=18 * mm, topMargin=16 * mm,
                            bottomMargin=16 * mm, title=title, author=seller.company_name or "", invariant=1,
                            pageCompression=0)
    story = []

    seller_block = Paragraph(_lines(seller.company_name, seller.address, seller.email, seller.phone,
                                    f"Tax ID: {seller.tax_id}" if seller.tax_id else ""), st["body"])
    head = Table([[Paragraph(_t(kind.upper()), st["title"]), seller_block]], colWidths=[85 * mm, 89 * mm])
    head.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("ALIGN", (1, 0), (1, 0), "RIGHT")]))
    story += [head, Spacer(1, 6 * mm)]

    meta = Table([[Paragraph("BILL TO", st["h"]), Paragraph("DETAILS", st["h"])],
                  [Paragraph(_address(doc), st["body"]),
                   Table([[Paragraph(_t(k), st["muted"]), Paragraph(_t(v), st["body"])] for k, v in meta_rows],
                         colWidths=[30 * mm, 50 * mm], style=TableStyle([
                             ("VALIGN", (0, 0), (-1, -1), "TOP"), ("LEFTPADDING", (0, 0), (-1, -1), 0),
                             ("TOPPADDING", (0, 0), (-1, -1), 1), ("BOTTOMPADDING", (0, 0), (-1, -1), 1)]))]],
                 colWidths=[90 * mm, 84 * mm])
    meta.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("LEFTPADDING", (0, 0), (-1, -1), 0)]))
    story += [meta, Spacer(1, 7 * mm)]

    rows = [[Paragraph("DESCRIPTION", st["h"]), Paragraph("QTY", st["h"]), Paragraph("UNIT PRICE", st["h"]),
             Paragraph("AMOUNT", st["h"])]]
    for item in doc.items.all():
        rows.append([Paragraph(_t(item.description), st["body"]), Paragraph(str(item.quantity), st["num"]),
                     Paragraph(_t(_money(doc.currency, item.unit_price)), st["num"]),
                     Paragraph(_t(_money(doc.currency, item.amount)), st["num"])])
    table = Table(rows, colWidths=[86 * mm, 16 * mm, 36 * mm, 36 * mm], repeatRows=1)
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), HEAD_BG), ("LINEBELOW", (0, 0), (-1, -1), 0.4, LINE),
        ("VALIGN", (0, 0), (-1, -1), "TOP"), ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4)]))
    story += [table, Spacer(1, 4 * mm)]

    totals = [["Subtotal", _money(doc.currency, doc.subtotal)]]
    if doc.discount_total:
        totals.append([doc.discount_label or "Discount", "-" + _money(doc.currency, doc.discount_total)])
    if doc.tax_total or doc.tax_name:
        totals.append([f"{doc.tax_name or 'Tax'} ({doc.tax_rate.normalize():f}%)", _money(doc.currency, doc.tax_total)])
    totals.append(["Total", _money(doc.currency, doc.total)])
    extra = []
    if kind == "Invoice":
        if doc.amount_paid:
            extra.append(["Paid", _money(doc.currency, doc.amount_paid)])
        if doc.amount_refunded:
            extra.append(["Refunded", _money(doc.currency, doc.amount_refunded)])
        extra.append(["Amount due", _money(doc.currency, doc.balance_due)])
    all_rows = totals + extra
    grid = Table([[Paragraph(_t(k), st["numbold"] if k in ("Total", "Amount due") else st["num"]),
                   Paragraph(_t(v), st["numbold"] if k in ("Total", "Amount due") else st["num"])]
                  for k, v in all_rows], colWidths=[40 * mm, 36 * mm], hAlign="RIGHT")
    grid.setStyle(TableStyle([("LINEABOVE", (0, len(totals) - 1), (-1, len(totals) - 1), 0.6, INK),
                              ("TOPPADDING", (0, 0), (-1, -1), 2), ("BOTTOMPADDING", (0, 0), (-1, -1), 2)]))
    story += [grid]

    if payments:
        story += [Spacer(1, 6 * mm), Paragraph("PAYMENTS", st["h"]), Spacer(1, 1 * mm)]
        pay_rows = [[Paragraph(_t(p.occurred_at.strftime("%Y-%m-%d")), st["body"]),
                     Paragraph(_t(("Refund - " if p.type == TransactionType.REFUND else "") + (p.method_name or "Payment")),
                               st["body"]),
                     Paragraph(_t(p.reference), st["muted"]),
                     Paragraph(_t(("-" if p.type == TransactionType.REFUND else "") + _money(p.currency, p.amount)),
                               st["num"])] for p in payments]
        pay_table = Table(pay_rows, colWidths=[26 * mm, 60 * mm, 50 * mm, 38 * mm])
        pay_table.setStyle(TableStyle([("LINEBELOW", (0, 0), (-1, -1), 0.3, LINE), ("VALIGN", (0, 0), (-1, -1), "TOP")]))
        story.append(pay_table)

    if doc.notes:
        story += [Spacer(1, 6 * mm), Paragraph("NOTES", st["h"]), Paragraph(_lines(*doc.notes.splitlines()), st["body"])]
    if footer_extra:
        story += [Spacer(1, 6 * mm), Paragraph(_lines(*footer_extra.splitlines()), st["muted"])]

    pdf.build(story)
    return buffer.getvalue()


def render_invoice_pdf(invoice):
    settings_row = BillingSettings.load()
    meta = [("Invoice no.", invoice.number or "Draft"), ("Status", invoice.display_status_label)]
    if invoice.issue_date:
        meta.append(("Issued", invoice.issue_date.isoformat()))
    if invoice.due_date:
        meta.append(("Due", invoice.due_date.isoformat()))
    if invoice.order_id:
        meta.append(("Order", invoice.order.reference))
    payments = list(invoice.transactions.filter(status=TransactionStatus.SUCCEEDED).order_by("occurred_at", "id"))
    return _build(invoice, kind="Invoice", title=f"Invoice {invoice.reference}", meta_rows=meta,
                  footer_extra=settings_row.invoice_footer, payments=payments)


def render_quote_pdf(quote):
    meta = [("Quote no.", quote.number or "Draft"), ("Status", quote.display_status_label)]
    if quote.issue_date:
        meta.append(("Issued", quote.issue_date.isoformat()))
    if quote.valid_until:
        meta.append(("Valid until", quote.valid_until.isoformat()))
    return _build(quote, kind="Quote", title=f"Quote {quote.reference}", meta_rows=meta)
