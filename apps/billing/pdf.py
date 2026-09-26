"""
PDF rendering for invoices and quotes (reportlab). Reads only stored records - a
PDF is a picture of the document as it was issued, never a recalculation - and
is deterministic (``invariant``), so the same invoice always yields the same bytes.

The look: the brand's logo (or name) and colour, a status badge (PAID, UNPAID, OVERDUE...), a summary band (issued, due, order,
amount due), the lines and totals, the ways to pay (your active payment methods with their account details, only while something is
owed), the payments received, and a large PAID mark on a settled invoice.

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
from reportlab.lib.utils import ImageReader
from reportlab.platypus import Image, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from .models import BillingSettings, InvoiceStatus, PaymentMethod, TransactionStatus, TransactionType

INK = colors.HexColor("#1f2933")
MUTED = colors.HexColor("#616e7c")
LINE = colors.HexColor("#d9dee5")
HEAD_BG = colors.HexColor("#eef2f7")
BAND_BG = colors.HexColor("#f6f8fb")
GREEN = colors.HexColor("#0f6e3f")
AMBER = colors.HexColor("#8f5700")
RED = colors.HexColor("#b42323")
GREY = colors.HexColor("#616e7c")
WIDTH = 174 * mm  # the printable width of an A4 page with 18 mm margins


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
        "muted": ParagraphStyle("muted", parent=base, fontName="Helvetica", fontSize=8.5, leading=11, textColor=MUTED),
        "title": ParagraphStyle("title", parent=base, fontName="Helvetica-Bold", fontSize=22, leading=26, textColor=INK,
                                alignment=2),
        "number": ParagraphStyle("number", parent=base, fontName="Helvetica", fontSize=10, leading=13, textColor=MUTED,
                                 alignment=2),
        "brand": ParagraphStyle("brand", parent=base, fontName="Helvetica-Bold", fontSize=16, leading=20),
        "h": ParagraphStyle("h", parent=base, fontName="Helvetica-Bold", fontSize=8, leading=10, textColor=MUTED),
        "num": ParagraphStyle("num", parent=base, fontName="Helvetica", fontSize=9, leading=12, alignment=2, textColor=INK),
        "numbold": ParagraphStyle("numbold", parent=base, fontName="Helvetica-Bold", fontSize=10, leading=13, alignment=2,
                                  textColor=INK),
        "band_label": ParagraphStyle("band_label", parent=base, fontName="Helvetica", fontSize=7.5, leading=10, textColor=MUTED),
        "band_value": ParagraphStyle("band_value", parent=base, fontName="Helvetica-Bold", fontSize=10.5, leading=13, textColor=INK),
        "band_big": ParagraphStyle("band_big", parent=base, fontName="Helvetica-Bold", fontSize=13, leading=16, textColor=INK),
        "pill": ParagraphStyle("pill", parent=base, fontName="Helvetica-Bold", fontSize=8.5, leading=11, textColor=colors.white,
                               alignment=1),
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


def _brand():
    """(name, colour, logo flowable or None): the portal's own brand, read from Brand settings."""
    from apps.branding import services as branding
    from apps.branding.models import BrandSettings

    brand = branding.get()
    try:
        colour = colors.HexColor(brand.primary)
    except (ValueError, TypeError):
        colour = colors.HexColor("#2459d6")
    logo = None
    row = BrandSettings.objects.filter(pk=1).values_list("logo", "logo_type").first()
    if row and row[0] and row[1] in ("image/png", "image/jpeg"):
        try:
            data = bytes(row[0])
            width, height = ImageReader(BytesIO(data)).getSize()
            scale = min(46 * mm / width, 13 * mm / height)
            logo = Image(BytesIO(data), width=width * scale, height=height * scale)
            logo.hAlign = "LEFT"
        except Exception:  # noqa: BLE001 - a logo that cannot be read is left out, never a failed invoice
            logo = None
    return brand.name, colour, logo


def _status_look(doc, kind):
    """(label, colour) for the badge at the top."""
    if kind == "Invoice":
        if doc.status == InvoiceStatus.PAID:
            return "PAID", GREEN
        if doc.status in (InvoiceStatus.CANCELLED, InvoiceStatus.REFUNDED, InvoiceStatus.DRAFT):
            return doc.get_status_display().upper(), GREY
        return ("OVERDUE", RED) if doc.is_overdue else ("PARTLY PAID" if doc.status == InvoiceStatus.PARTIALLY_PAID else "UNPAID", AMBER)
    label = doc.display_status_label.upper()
    return label, {"ACCEPTED": GREEN, "DECLINED": RED, "EXPIRED": RED}.get(label, GREY if label in ("DRAFT", "CANCELLED") else AMBER)


def _band(cells, st, accent):
    """The summary band under the addresses: label above value, one cell each, the last one (amount due) larger."""
    row = []
    for index, (label, value) in enumerate(cells):
        big = index == len(cells) - 1
        row.append([Paragraph(_t(label.upper()), st["band_label"]), Paragraph(_t(value), st["band_big"] if big else st["band_value"])])
    table = Table([row], colWidths=[WIDTH / len(cells)] * len(cells))
    table.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), BAND_BG), ("BOX", (0, 0), (-1, -1), 0.4, LINE),
                               ("LINEBEFORE", (0, 0), (-1, -1), 0.4, LINE), ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                               ("TOPPADDING", (0, 0), (-1, -1), 6), ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
                               ("LEFTPADDING", (0, 0), (-1, -1), 8), ("LINEABOVE", (0, 0), (-1, 0), 2, accent)]))
    return table


def _payment_methods(st):
    """The active offline payment methods as boxes, two to a row: how to send the money."""
    methods = [m for m in PaymentMethod.objects.filter(is_active=True, provider__isnull=True) if m.detail_rows or m.instructions]
    boxes = []
    for method in methods:
        body = [Paragraph(f"<b>{_t(method.name)}</b>", st["body"])]
        rows = [f"{label}: {value}" for label, value in method.detail_rows]
        if rows:
            body.append(Paragraph(_lines(*rows), st["body"]))
        if method.instructions:
            body.append(Paragraph(_lines(*method.instructions.splitlines()), st["muted"]))
        boxes.append(body)
    if not boxes:
        return None
    if len(boxes) % 2:
        boxes.append("")
    grid = [[boxes[i], boxes[i + 1]] for i in range(0, len(boxes), 2)]
    table = Table(grid, colWidths=[WIDTH / 2, WIDTH / 2])
    table.setStyle(TableStyle([("BOX", (0, 0), (-1, -1), 0.4, LINE), ("INNERGRID", (0, 0), (-1, -1), 0.4, LINE),
                               ("VALIGN", (0, 0), (-1, -1), "TOP"), ("TOPPADDING", (0, 0), (-1, -1), 6),
                               ("BOTTOMPADDING", (0, 0), (-1, -1), 6), ("LEFTPADDING", (0, 0), (-1, -1), 8)]))
    return table


def _build(doc, *, kind, title, meta_rows, footer_extra=None, payments=()):
    seller = BillingSettings.load()
    brand_name, accent, logo = _brand()
    st = _styles()
    buffer = BytesIO()
    label, tone = _status_look(doc, kind)
    paid_stamp = kind == "Invoice" and doc.status == InvoiceStatus.PAID

    def page(canvas, pdf_doc):
        canvas.saveState()
        if paid_stamp:  # a settled invoice carries a faint diagonal PAID
            canvas.setFillColor(GREEN)
            canvas.setFillAlpha(0.07)
            canvas.setFont("Helvetica-Bold", 120)
            canvas.translate(A4[0] / 2, A4[1] / 2)
            canvas.rotate(35)
            canvas.drawCentredString(0, 0, "PAID")
        canvas.restoreState()
        canvas.saveState()
        canvas.setStrokeColor(LINE)
        canvas.line(18 * mm, 13 * mm, A4[0] - 18 * mm, 13 * mm)
        canvas.setFont("Helvetica", 7.5)
        canvas.setFillColor(MUTED)
        canvas.drawString(18 * mm, 9 * mm, _t(seller.company_name or brand_name))
        canvas.drawRightString(A4[0] - 18 * mm, 9 * mm, f"Page {pdf_doc.page}")
        canvas.restoreState()

    pdf = SimpleDocTemplate(buffer, pagesize=A4, leftMargin=18 * mm, rightMargin=18 * mm, topMargin=14 * mm,
                            bottomMargin=18 * mm, title=title, author=seller.company_name or "", invariant=1, pageCompression=0)
    story = []

    brand_cell = logo or Paragraph(f'<font color="{accent.hexval().replace("0x", "#")}">{_t(brand_name)}</font>', st["brand"])
    pill = Table([[Paragraph(_t(label), st["pill"])]], colWidths=[34 * mm])
    pill.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), tone), ("TOPPADDING", (0, 0), (-1, -1), 3),
                              ("BOTTOMPADDING", (0, 0), (-1, -1), 4), ("ROUNDEDCORNERS", [4, 4, 4, 4])]))
    right = Table([[Paragraph(_t(kind.upper()), st["title"])], [Paragraph(_t(meta_rows[0][1]), st["number"])], [pill]],
                  colWidths=[80 * mm])
    right.setStyle(TableStyle([("ALIGN", (0, 2), (0, 2), "RIGHT"), ("TOPPADDING", (0, 0), (-1, -1), 1),
                               ("BOTTOMPADDING", (0, 0), (-1, -1), 1), ("RIGHTPADDING", (0, 0), (-1, -1), 0)]))
    right.hAlign = "RIGHT"
    head = Table([[brand_cell, right]], colWidths=[WIDTH - 82 * mm, 82 * mm])
    head.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("LEFTPADDING", (0, 0), (0, 0), 0),
                              ("RIGHTPADDING", (1, 0), (1, 0), 0)]))
    story += [head, Spacer(1, 7 * mm)]

    seller_lines = _lines(seller.company_name, seller.address, seller.email, seller.phone,
                          f"Tax ID: {seller.tax_id}" if seller.tax_id else "")
    people = Table([[Paragraph("FROM", st["h"]), Paragraph("BILL TO", st["h"])],
                    [Paragraph(seller_lines or _t(brand_name), st["body"]), Paragraph(_address(doc), st["body"])]],
                   colWidths=[WIDTH / 2, WIDTH / 2])
    people.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("LEFTPADDING", (0, 0), (-1, -1), 0)]))
    story += [people, Spacer(1, 5 * mm)]

    cells = [(k, v) for k, v in meta_rows[2:]]  # the number and status are already in the header
    if kind == "Invoice":
        cells.append(("Amount due", _money(doc.currency, doc.balance_due)))
    else:
        cells.append(("Total", _money(doc.currency, doc.total)))
    story += [_band(cells[-4:], st, accent), Spacer(1, 6 * mm)]

    rows = [[Paragraph("DESCRIPTION", st["h"]), Paragraph("QTY", st["h"]), Paragraph("UNIT PRICE", st["h"]),
             Paragraph("AMOUNT", st["h"])]]
    for item in doc.items.all():
        rows.append([Paragraph(_t(item.description), st["body"]), Paragraph(str(item.quantity), st["num"]),
                     Paragraph(_t(_money(doc.currency, item.unit_price)), st["num"]),
                     Paragraph(_t(_money(doc.currency, item.amount)), st["num"])])
    table = Table(rows, colWidths=[84 * mm, 16 * mm, 37 * mm, 37 * mm], repeatRows=1)
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), HEAD_BG), ("LINEBELOW", (0, 0), (-1, -1), 0.4, LINE),
        ("VALIGN", (0, 0), (-1, -1), "TOP"), ("TOPPADDING", (0, 0), (-1, -1), 5), ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#fafbfc")])]))
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
            extra.append(["Paid", "-" + _money(doc.currency, doc.amount_paid)])
        if doc.amount_refunded:
            extra.append(["Refunded", _money(doc.currency, doc.amount_refunded)])
        extra.append(["Amount due", _money(doc.currency, doc.balance_due)])
    all_rows = totals + extra
    strong = ("Total", "Amount due")
    grid = Table([[Paragraph(_t(k), st["numbold"] if k in strong else st["num"]),
                   Paragraph(_t(v), st["numbold"] if k in strong else st["num"])] for k, v in all_rows],
                 colWidths=[40 * mm, 38 * mm], hAlign="RIGHT")
    style = [("LINEABOVE", (0, len(totals) - 1), (-1, len(totals) - 1), 0.6, INK),
             ("TOPPADDING", (0, 0), (-1, -1), 2), ("BOTTOMPADDING", (0, 0), (-1, -1), 2)]
    if kind == "Invoice":  # the amount due is the number the reader is looking for
        style += [("BACKGROUND", (0, len(all_rows) - 1), (-1, -1), HEAD_BG), ("TOPPADDING", (0, len(all_rows) - 1), (-1, -1), 5),
                  ("BOTTOMPADDING", (0, len(all_rows) - 1), (-1, -1), 5)]
    grid.setStyle(TableStyle(style))
    story += [grid]

    if kind == "Invoice" and doc.status in (InvoiceStatus.UNPAID, InvoiceStatus.PARTIALLY_PAID) and doc.balance_due > 0:
        methods = _payment_methods(st)
        if methods is not None:
            ref = doc.number or doc.reference
            story += [Spacer(1, 7 * mm), Paragraph("HOW TO PAY", st["h"]), Spacer(1, 1.5 * mm), methods, Spacer(1, 1.5 * mm),
                      Paragraph(_t(f"Please use {ref} as the payment reference, then tell us you have paid from the invoice page."),
                                st["muted"])]

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

    pdf.build(story, onFirstPage=page, onLaterPages=page)
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
