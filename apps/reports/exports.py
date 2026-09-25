"""
One place decides how a value looks, and one place turns a ``Result`` into a file.

* **Screen and PDF** show people-friendly text (``display``): thousands separators, "25 Sep 2026".
* **CSV and XLSX** carry real values (``raw`` / typed cells): a number stays a number, a date stays a date, so the
  spreadsheet can add them up.
* **Spreadsheet formula injection is closed off.** Anything a person could have typed (a client's name, a ticket
  subject, a payment reference) that starts with ``=``, ``+``, ``-``, ``@`` or a tab/return would be run as a formula by
  Excel; CSV values get a leading apostrophe and XLSX text cells are stored strictly as text.
"""
import csv
from datetime import date, datetime
from decimal import Decimal
from io import BytesIO, StringIO
from xml.sax.saxutils import escape

from django.utils import timezone

FORMULA_STARTS = ("=", "+", "-", "@", "\t", "\r")
EXPORT_TYPES = {
    "csv": ("text/csv; charset=utf-8", "csv"),
    "xlsx": ("application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "xlsx"),
    "pdf": ("application/pdf", "pdf"),
}


def safe_text(value):
    text = "" if value is None else str(value)
    return "'" + text if text.startswith(FORMULA_STARTS) else text


def _local(value):
    if isinstance(value, datetime) and timezone.is_aware(value):
        return timezone.localtime(value)
    return value


def display(kind, value):
    """How a value reads on the screen and in the PDF."""
    if value is None or value == "":
        return "-"
    value = _local(value)
    if kind == "money":
        return f"{Decimal(value):,.2f}"
    if kind == "int":
        return f"{int(value):,}"
    if kind == "hours":
        return f"{float(value):.1f} h"
    if kind == "percent":
        return f"{float(value):.1f}%"
    if kind == "datetime" and isinstance(value, datetime):
        return value.strftime("%d %b %Y %H:%M")
    if kind in ("date", "datetime") and isinstance(value, date):
        return value.strftime("%d %b %Y")
    return str(value)


def raw(kind, value):
    """The value as a CSV cell."""
    if value is None:
        return ""
    value = _local(value)
    if kind == "money":
        return f"{Decimal(value):.2f}"
    if kind in ("int", "hours", "percent"):
        return str(value)
    if kind == "datetime" and isinstance(value, datetime):
        return value.strftime("%Y-%m-%d %H:%M")
    if isinstance(value, date):
        return value.isoformat()
    return safe_text(value)


def is_number(kind):
    return kind in ("money", "int", "hours", "percent")


# --- CSV -----------------------------------------------------------------------------------------------------------

def to_csv(result):
    buffer = StringIO()
    writer = csv.writer(buffer)
    writer.writerow([safe_text(c.label) for c in result.columns])
    for row in result.rows:
        writer.writerow([raw(c.kind, row.get(c.key)) for c in result.columns])
    return buffer.getvalue().encode("utf-8-sig")  # the BOM makes Excel read accents and symbols correctly


# --- XLSX ----------------------------------------------------------------------------------------------------------

def to_xlsx(result):
    from openpyxl import Workbook
    from openpyxl.cell import WriteOnlyCell
    from openpyxl.styles import Font

    workbook = Workbook(write_only=True)
    sheet = workbook.create_sheet(title="Report")
    bold = Font(bold=True)

    def text(value, *, font=None):
        cell = WriteOnlyCell(sheet, value=safe_text(value)[:32000])
        cell.data_type = "s"  # never a formula, whatever it starts with
        if font:
            cell.font = font
        return cell

    def typed(kind, value):
        if value is None or value == "":
            return None
        value = _local(value)
        if kind == "money":
            cell = WriteOnlyCell(sheet, value=float(value))
            cell.number_format = "#,##0.00"
            return cell
        if kind in ("int", "hours", "percent"):
            return WriteOnlyCell(sheet, value=value)
        if isinstance(value, datetime):
            cell = WriteOnlyCell(sheet, value=value.replace(tzinfo=None))
            cell.number_format = "yyyy-mm-dd hh:mm"
            return cell
        if isinstance(value, date):
            cell = WriteOnlyCell(sheet, value=value)
            cell.number_format = "yyyy-mm-dd"
            return cell
        return text(value)

    sheet.append([text(result.title, font=Font(bold=True, size=14))])
    for label, value in result.params.items():
        sheet.append([text(label), text(value)])
    sheet.append([])
    for label, kind, value in result.summary:
        sheet.append([text(label, font=bold), typed(kind, value)])
    if result.summary:
        sheet.append([])
    sheet.append([text(c.label, font=bold) for c in result.columns])
    for row in result.rows:
        sheet.append([typed(c.kind, row.get(c.key)) for c in result.columns])
    for note in result.notes:
        sheet.append([text(note)])
    output = BytesIO()
    workbook.save(output)
    return output.getvalue()


# --- PDF -----------------------------------------------------------------------------------------------------------

def _pdf_text(value):
    """Text safe for the built-in fonts (Western European only; anything else prints as "?") and reportlab's markup."""
    return escape(str(value if value is not None else "").encode("cp1252", "replace").decode("cp1252"))


def to_pdf(result):
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    ink, muted, line, head = (colors.HexColor("#1f2933"), colors.HexColor("#616e7c"), colors.HexColor("#cbd2d9"),
                              colors.HexColor("#f0f4f8"))
    base = getSampleStyleSheet()["Normal"]
    small = ParagraphStyle("small", parent=base, fontName="Helvetica", fontSize=7.5, leading=9.5, textColor=ink)
    small_right = ParagraphStyle("small_right", parent=small, alignment=2)
    head_style = ParagraphStyle("head", parent=small, fontName="Helvetica-Bold")
    head_right = ParagraphStyle("head_right", parent=head_style, alignment=2)
    title = ParagraphStyle("title", parent=base, fontName="Helvetica-Bold", fontSize=15, leading=19, textColor=ink)
    meta = ParagraphStyle("meta", parent=base, fontName="Helvetica", fontSize=8.5, leading=11, textColor=muted)

    output = BytesIO()
    page = landscape(A4)
    document = SimpleDocTemplate(output, pagesize=page, leftMargin=10 * mm, rightMargin=10 * mm, topMargin=12 * mm,
                                 bottomMargin=14 * mm, title=result.title, invariant=True)
    width = page[0] - 20 * mm
    story = [Paragraph(_pdf_text(result.title), title)]
    if result.params:
        story.append(Paragraph(_pdf_text("   ".join(f"{k}: {v}" for k, v in result.params.items())), meta))
    story.append(Spacer(1, 4 * mm))

    if result.summary:
        rows = [[Paragraph(_pdf_text(label), small), Paragraph(_pdf_text(display(kind, value)), small_right)]
                for label, kind, value in result.summary]
        table = Table(rows, colWidths=[70 * mm, 35 * mm], hAlign="LEFT")
        table.setStyle(TableStyle([("LINEBELOW", (0, 0), (-1, -1), 0.25, line), ("VALIGN", (0, 0), (-1, -1), "TOP")]))
        story += [table, Spacer(1, 5 * mm)]

    if result.columns:
        weights = []
        for c in result.columns:
            longest = max([len(c.label)] + [len(display(c.kind, r.get(c.key))) for r in result.rows[:200]])
            weights.append(min(max(longest, 4), 32) + 2)
        widths = [width * w / sum(weights) for w in weights]
        data = [[Paragraph(_pdf_text(c.label), head_right if is_number(c.kind) else head_style)
                 for c in result.columns]]
        for row in result.rows:
            data.append([Paragraph(_pdf_text(display(c.kind, row.get(c.key))),
                                   small_right if is_number(c.kind) else small) for c in result.columns])
        if len(data) == 1:
            data.append([Paragraph("Nothing to show for these dates.", small)] + [""] * (len(result.columns) - 1))
        table = Table(data, colWidths=widths, repeatRows=1)
        table.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, 0), head), ("LINEBELOW", (0, 0), (-1, -1), 0.25, line),
                                   ("VALIGN", (0, 0), (-1, -1), "TOP"), ("TOPPADDING", (0, 0), (-1, -1), 2),
                                   ("BOTTOMPADDING", (0, 0), (-1, -1), 2)]))
        story.append(table)
    for note in result.notes:
        story += [Spacer(1, 3 * mm), Paragraph(_pdf_text(note), meta)]

    def footer(canvas, doc):
        canvas.saveState()
        canvas.setFont("Helvetica", 7.5)
        canvas.setFillColor(muted)
        canvas.drawString(10 * mm, 8 * mm, _pdf_text(result.title)[:80])
        canvas.drawRightString(page[0] - 10 * mm, 8 * mm, f"Page {doc.page}")
        canvas.restoreState()

    document.build(story, onFirstPage=footer, onLaterPages=footer)
    return output.getvalue()


def render(result, fmt):
    """(bytes, content type, extension) for ``fmt`` (csv, xlsx or pdf)."""
    builders = {"csv": to_csv, "xlsx": to_xlsx, "pdf": to_pdf}
    if fmt not in builders:
        raise ValueError(f"Unknown export format: {fmt}")
    content_type, extension = EXPORT_TYPES[fmt]
    return builders[fmt](result), content_type, extension
