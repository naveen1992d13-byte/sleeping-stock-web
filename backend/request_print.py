"""Request Center Print layout — the single source used for Print and email PDF.

The email attachment is this Print content/layout converted to PDF.
Do not add a separate PDF template.
"""
from __future__ import annotations

from html import escape as html_escape
from io import BytesIO
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas as pdfcanvas
from reportlab.platypus import (
    BaseDocTemplate,
    Frame,
    Image,
    KeepTogether,
    PageTemplate,
    Paragraph,
    Spacer,
    Table,
    TableStyle,
)

ITEMS_PER_PAGE = 12
GREEN = colors.HexColor("#14532d")
GREEN_TEXT = colors.HexColor("#166534")
INK = colors.HexColor("#17211b")
RULE = colors.HexColor("#d5ddd8")
PRINT_NOTES = "Please verify part number, accepted quantity and LOC before dispatch."

_LOGO_CANDIDATES = [
    Path(__file__).resolve().parents[1] / "frontend" / "public" / "sleeping-stock-logo.png",
    Path("/agent/repos/sleeping-stock-web/frontend/public/sleeping-stock-logo.png"),
]


def _display_status(status) -> str:
    if status == "Approved":
        return "Accepted"
    if status == "Partially Approved":
        return "Partially Accepted"
    return status or "Requested"


def _nfmt(value) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "0"
    if number == int(number):
        return f"{int(number):,}"
    return f"{number:,.2f}"


def _dtfmt(value) -> str:
    if not value:
        return "-"
    return str(value)[:16].replace("T", " ")


def _esc(value) -> str:
    return html_escape("" if value is None else str(value), quote=True)


def _text(value, fallback="-"):
    if value in (None, ""):
        return fallback
    return str(value)


def _item_accepted(item: dict) -> float:
    try:
        return float(item.get("accepted_qty", item.get("approved_qty", 0)) or 0)
    except (TypeError, ValueError):
        return 0.0


def _item_loc(item: dict) -> str:
    return _text(item.get("loc_at_request") or item.get("loc"), "-")


def _item_purchase_aging(item: dict) -> str:
    return _text(
        item.get("purchase_aging_days_at_request",
                 item.get("purchase_aging_at_request",
                          item.get("purchase_aging_days", item.get("purchase_aging")))),
        "-",
    )


def _item_sales_aging(item: dict) -> str:
    return _text(
        item.get("sales_aging_days_at_request",
                 item.get("sales_aging_at_request",
                          item.get("sales_aging_days", item.get("sales_aging")))),
        "-",
    )


def _receiver_label(group: dict) -> str:
    users = group.get("receiver_users") or []
    if users:
        parts = []
        for user in users:
            name = user.get("name") or "User"
            uid = user.get("id") or ""
            parts.append(f"{name} ({uid})" if uid else name)
        return ", ".join(parts)
    return "Supplying Branch Team"


def _requested_by_label(group: dict) -> str:
    name = group.get("requested_user_name") or "-"
    uid = group.get("requested_user_id") or ""
    return f"{name} ({uid})" if uid else name


def print_column_headers() -> list:
    return [
        "S.No",
        "PART NUMBER",
        "PART DESCRIPTION",
        "LOC",
        "REQUEST QTY",
        "ACCEPT QTY",
        "PURCHASE AGING",
        "SALES AGING",
        "STATUS / REMARKS",
    ]


def build_request_print_html(group: dict, logo_url: str = "") -> str:
    """Same HTML document as Request Center Print (`openRequestPrint`)."""
    items = list(group.get("items") or [])
    pages = [items[i:i + ITEMS_PER_PAGE] for i in range(0, len(items), ITEMS_PER_PAGE)] or [[]]
    total_accepted = sum(_item_accepted(item) for item in items)
    total_items = group.get("total_items", len(items))
    total_qty = group.get("total_qty", sum(float(i.get("requested_qty") or 0) for i in items))
    total_value = group.get("total_value", 0)
    status = _display_status(group.get("status"))
    page_html = []
    for page_index, page_items in enumerate(pages):
        rows = []
        for idx, item in enumerate(page_items):
            remarks = item.get("approval_remarks") or item.get("remarks") or ""
            rows.append(
                "<tr>"
                f"<td>{page_index * ITEMS_PER_PAGE + idx + 1}</td>"
                f"<td>{_esc(item.get('part_number'))}</td>"
                f"<td>{_esc(item.get('description') or '-')}</td>"
                f"<td>{_esc(_item_loc(item))}</td>"
                f"<td>{_nfmt(item.get('requested_qty'))}</td>"
                f"<td>{_nfmt(_item_accepted(item))}</td>"
                f"<td>{_esc(_item_purchase_aging(item))}</td>"
                f"<td>{_esc(_item_sales_aging(item))}</td>"
                f"<td>{_esc(_display_status(item.get('status')))}<br/><small>{_esc(remarks)}</small></td>"
                "</tr>"
            )
        footer = (
            '<div class="signatures">'
            "<div>REQUESTED BY<span></span><small>Signature</small></div>"
            "<div>RECEIVED BY<span></span><small>Signature</small></div>"
            "<div>APPROVED BY<span></span><small>Signature</small></div>"
            "<div>DISPATCHED BY<span></span><small>Signature</small></div>"
            "</div>"
            f'<div class="notes"><b>Notes:</b> {PRINT_NOTES}</div>'
            if page_index == len(pages) - 1
            else f'<div class="continued">(Contd... Page {page_index + 2})</div>'
        )
        page_html.append(f"""
    <section class="page">
      <div class="page-no">PAGE {page_index + 1} OF {len(pages)}</div>
      <header><img src="{_esc(logo_url)}"/><div><div class="brand">Sleeping<span>Stocks</span></div><div class="sub">NMTS | Non Moving Tracking System</div></div><h1>PARTS TRANSFER REQUEST</h1></header>
      <div class="summary">
        <div class="meta"><b>REQUEST NO</b><span>: {_esc(group.get('request_number') or '-')}</span><b>REFERENCE NO (ORDER NO)</b><span>: {_esc(group.get('order_number') or '-')}</span><b>REQUEST DATE</b><span>: {_esc(_dtfmt(group.get('requested_at') or group.get('created_at')))}</span></div>
        <div class="metric"><small>TOTAL ITEMS</small><strong>{_esc(total_items)}</strong></div>
        <div class="metric"><small>REQUEST QTY</small><strong>{_nfmt(total_qty)}</strong></div>
        <div class="metric"><small>ACCEPT QTY</small><strong>{_nfmt(total_accepted)}</strong></div>
        <div class="metric"><small>TOTAL VALUE</small><strong>₹ {_nfmt(total_value)}</strong></div>
        <div class="metric"><small>STATUS</small><strong class="status">{_esc(status)}</strong></div>
      </div>
      <div class="route">
        <div><h3>STOCK SOURCE (FROM)</h3><p><b>Brand:</b> {_esc(group.get('supplying_brand') or group.get('requesting_brand') or '-')}</p><p><b>Dealer:</b> {_esc(group.get('supplying_dealer') or '-')}</p><p><b>Branch:</b> {_esc(group.get('supplying_branch') or '-')}</p><p><b>Requested To:</b> {_esc(_receiver_label(group))}</p></div>
        <div class="arrow">→</div>
        <div><h3>STOCK DESTINATION (TO)</h3><p><b>Brand:</b> {_esc(group.get('requesting_brand') or '-')}</p><p><b>Dealer:</b> {_esc(group.get('requesting_dealer') or '-')}</p><p><b>Branch:</b> {_esc(group.get('requesting_branch') or '-')}</p><p><b>Requested By:</b> {_esc(_requested_by_label(group))}</p></div>
      </div>
      <table><thead><tr>{''.join(f'<th>{html_escape(h)}</th>' for h in print_column_headers())}</tr></thead>
      <tbody>{''.join(rows)}</tbody></table>
      {footer}
    </section>""")

    title = _esc(group.get("request_number") or "Request")
    return f"""<!doctype html><html><head><title>{title}</title><style>
    @page{{size:A4 portrait;margin:8mm}}*{{box-sizing:border-box}}body{{margin:0;font-family:Arial,sans-serif;color:#17211b;background:#eee}}.page{{position:relative;width:100%;min-height:190mm;background:white;padding:8mm;page-break-after:always}}.page:last-child{{page-break-after:auto}}.page-no{{position:absolute;right:8mm;top:1.5mm;font-size:9px;font-weight:bold;line-height:1;white-space:nowrap;z-index:2}}header{{display:flex;align-items:center;border-bottom:2px solid #14532d;padding:7mm 34mm 8px 0;margin-bottom:8px;min-height:24mm}}header img{{width:70px;height:55px;object-fit:contain}}.brand{{font-size:24px;font-weight:800}}.brand span{{color:#15803d}}.sub{{font-size:9px;font-weight:bold;color:#166534}}h1{{margin-left:auto;margin-right:0;color:#14532d;font-size:22px;white-space:nowrap}}.summary{{display:grid;grid-template-columns:2.2fr repeat(5,.75fr);border:1px solid #cbd5d1;border-radius:8px;overflow:hidden}}.meta{{display:grid;grid-template-columns:150px 1fr;gap:7px;padding:10px;font-size:10px}}.metric{{border-left:1px solid #d5ddd8;text-align:center;padding:13px 5px}}.metric small{{font-size:8px}}.metric strong{{display:block;color:#14532d;font-size:15px;margin-top:6px}}.metric .status{{font-size:11px}}.route{{display:grid;grid-template-columns:1fr 60px 1fr;gap:10px;margin:10px 0}}.route>div:not(.arrow){{border:1px solid #d5ddd8;border-radius:8px;padding:10px}}.route h3{{color:#166534;font-size:11px;margin:0 0 8px}}.route p{{font-size:9px;margin:5px 0}}.arrow{{display:flex;align-items:center;justify-content:center;font-size:30px;color:#15803d}}table{{width:100%;border-collapse:collapse;font-size:8px}}th{{background:#14532d;color:white;padding:6px 4px}}td{{border:1px solid #d7ddd9;padding:5px 4px;text-align:center}}td:nth-child(2),td:nth-child(3),td:last-child{{text-align:left}}.continued{{text-align:right;margin-top:7px;font-size:9px;font-weight:bold}}.signatures{{display:grid;grid-template-columns:repeat(4,1fr);border:1px solid #d5ddd8;margin-top:12px}}.signatures div{{text-align:center;padding:8px;border-right:1px solid #d5ddd8;font-size:9px;font-weight:bold}}.signatures div:last-child{{border-right:0}}.signatures span{{display:block;height:45px;border:1px dashed #b8c2bc;margin:8px}}.signatures small{{font-weight:normal}}.notes{{border:1px solid #d5ddd8;border-top:0;padding:8px;font-size:8px}}@media print{{body{{background:white}}.page{{padding:0;min-height:auto}}}}
  </style></head><body>{''.join(page_html)}</body></html>"""


class _PrintCanvas(pdfcanvas.Canvas):
    def __init__(self, *args, **kwargs):
        kwargs.setdefault("pageCompression", 0)
        pdfcanvas.Canvas.__init__(self, *args, **kwargs)
        self._saved_page_states = []

    def showPage(self):
        self._saved_page_states.append(dict(self.__dict__))
        self._startPage()

    def save(self):
        total_pages = len(self._saved_page_states)
        for state in self._saved_page_states:
            self.__dict__.update(state)
            self.setFont("Helvetica-Bold", 8)
            self.setFillColor(INK)
            self.drawRightString(A4[0] - 10 * mm, A4[1] - 8 * mm, f"PAGE {self._pageNumber} OF {total_pages}")
            pdfcanvas.Canvas.showPage(self)
        pdfcanvas.Canvas.save(self)


def _logo_path() -> Path | None:
    for path in _LOGO_CANDIDATES:
        if path.exists():
            return path
    return None


def _logo_flowable():
    path = _logo_path()
    if not path:
        return None
    try:
        from PIL import Image as PILImage
        img = PILImage.open(path)
        img.thumbnail((120, 90))
        buf = BytesIO()
        img.save(buf, format="PNG")
        buf.seek(0)
        return Image(buf, width=18 * mm, height=14 * mm)
    except Exception:
        return Image(str(path), width=18 * mm, height=14 * mm)


def _p(text, style):
    return Paragraph(html_escape("" if text is None else str(text)).replace("\n", "<br/>"), style)


def build_request_pdf(group: dict) -> bytes:
    """Convert Request Center Print content/layout to PDF (no separate template)."""
    items = list(group.get("items") or [])
    total_accepted = sum(_item_accepted(item) for item in items)
    total_items = group.get("total_items", len(items))
    total_qty = group.get("total_qty", sum(float(i.get("requested_qty") or 0) for i in items))
    total_value = group.get("total_value", 0)
    status = _display_status(group.get("status"))

    buffer = BytesIO()
    doc = BaseDocTemplate(
        buffer,
        pagesize=A4,
        leftMargin=8 * mm,
        rightMargin=8 * mm,
        topMargin=12 * mm,
        bottomMargin=10 * mm,
    )
    frame = Frame(doc.leftMargin, doc.bottomMargin, doc.width, doc.height, id="main")
    doc.addPageTemplates([PageTemplate(id="main", frames=[frame])])

    styles = getSampleStyleSheet()
    brand = styles["Normal"].clone("PrintBrand")
    brand.fontName = "Helvetica-Bold"
    brand.fontSize = 16
    brand.textColor = INK
    brand.leading = 18
    sub = styles["Normal"].clone("PrintSub")
    sub.fontName = "Helvetica-Bold"
    sub.fontSize = 8
    sub.textColor = GREEN_TEXT
    title = styles["Normal"].clone("PrintTitle")
    title.fontName = "Helvetica-Bold"
    title.fontSize = 14
    title.textColor = GREEN
    title.alignment = 2
    cell = styles["Normal"].clone("PrintCell")
    cell.fontName = "Helvetica"
    cell.fontSize = 7
    cell.leading = 9
    cell.textColor = INK
    head = cell.clone("PrintHead")
    head.fontName = "Helvetica-Bold"
    head.textColor = colors.white
    small = cell.clone("PrintSmall")
    small.fontSize = 8
    small.leading = 10
    section = styles["Normal"].clone("PrintSection")
    section.fontName = "Helvetica-Bold"
    section.fontSize = 9
    section.textColor = GREEN_TEXT

    story = []
    logo = _logo_flowable()
    brand_block = [
        Paragraph("SleepingStocks", brand),
        Paragraph("NMTS | Non Moving Tracking System", sub),
    ]
    if logo:
        header_row = [[
            logo,
            brand_block,
            Paragraph("PARTS TRANSFER REQUEST", title),
        ]]
        col_widths = [20 * mm, doc.width - 72 * mm, 52 * mm]
    else:
        header_row = [[brand_block, Paragraph("PARTS TRANSFER REQUEST", title)]]
        col_widths = [doc.width - 52 * mm, 52 * mm]
    header_table = Table(header_row, colWidths=col_widths)
    header_table.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LINEBELOW", (0, 0), (-1, -1), 2, GREEN),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
        ("TOPPADDING", (0, 0), (-1, -1), 0),
    ]))
    story.append(header_table)
    story.append(Spacer(1, 4))

    meta = Table(
        [
            [Paragraph("<b>REQUEST NO</b>", small), Paragraph(f": {_text(group.get('request_number'))}", small)],
            [Paragraph("<b>REFERENCE NO (ORDER NO)</b>", small), Paragraph(f": {_text(group.get('order_number'))}", small)],
            [Paragraph("<b>REQUEST DATE</b>", small), Paragraph(f": {_dtfmt(group.get('requested_at') or group.get('created_at'))}", small)],
        ],
        colWidths=[42 * mm, 48 * mm],
    )
    def _metric(label, value):
        return Paragraph(
            f"<font size='6'>{html_escape(str(label))}</font><br/>"
            f"<font size='10' color='#14532d'><b>{html_escape(str(value))}</b></font>",
            cell,
        )
    summary = Table(
        [[meta, _metric("TOTAL ITEMS", total_items), _metric("REQUEST QTY", _nfmt(total_qty)),
          _metric("ACCEPT QTY", _nfmt(total_accepted)), _metric("TOTAL VALUE", f"₹ {_nfmt(total_value)}"),
          _metric("STATUS", status)]],
        colWidths=[90 * mm, 22 * mm, 24 * mm, 24 * mm, 28 * mm, 22 * mm],
    )
    summary.setStyle(TableStyle([
        ("BOX", (0, 0), (-1, -1), 0.6, colors.HexColor("#cbd5d1")),
        ("INNERGRID", (1, 0), (-1, -1), 0.4, RULE),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("ALIGN", (1, 0), (-1, -1), "CENTER"),
        ("LEFTPADDING", (0, 0), (-1, -1), 4),
        ("RIGHTPADDING", (0, 0), (-1, -1), 4),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
    ]))
    story.append(summary)
    story.append(Spacer(1, 6))

    from_block = [
        Paragraph("STOCK SOURCE (FROM)", section),
        Paragraph(f"<b>Brand:</b> {_text(group.get('supplying_brand') or group.get('requesting_brand'))}", small),
        Paragraph(f"<b>Dealer:</b> {_text(group.get('supplying_dealer'))}", small),
        Paragraph(f"<b>Branch:</b> {_text(group.get('supplying_branch'))}", small),
        Paragraph(f"<b>Requested To:</b> {_receiver_label(group)}", small),
    ]
    to_block = [
        Paragraph("STOCK DESTINATION (TO)", section),
        Paragraph(f"<b>Brand:</b> {_text(group.get('requesting_brand'))}", small),
        Paragraph(f"<b>Dealer:</b> {_text(group.get('requesting_dealer'))}", small),
        Paragraph(f"<b>Branch:</b> {_text(group.get('requesting_branch'))}", small),
        Paragraph(f"<b>Requested By:</b> {_requested_by_label(group)}", small),
    ]
    route = Table(
        [[[p for p in from_block], Paragraph("→", title), [p for p in to_block]]],
        colWidths=[doc.width / 2 - 10 * mm, 20 * mm, doc.width / 2 - 10 * mm],
    )
    route.setStyle(TableStyle([
        ("BOX", (0, 0), (0, 0), 0.6, RULE),
        ("BOX", (2, 0), (2, 0), 0.6, RULE),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("ALIGN", (1, 0), (1, 0), "CENTER"),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
    ]))
    story.append(route)
    story.append(Spacer(1, 6))

    header_cells = [_p(h, head) for h in print_column_headers()]
    table_data = [header_cells]
    for idx, item in enumerate(items, start=1):
        remarks = item.get("approval_remarks") or item.get("remarks") or ""
        status_cell = _display_status(item.get("status"))
        if remarks:
            status_cell = f"{status_cell}\n{remarks}"
        table_data.append([
            _p(str(idx), cell),
            _p(item.get("part_number"), cell),
            _p(item.get("description") or "-", cell),
            _p(_item_loc(item), cell),
            _p(_nfmt(item.get("requested_qty")), cell),
            _p(_nfmt(_item_accepted(item)), cell),
            _p(_item_purchase_aging(item), cell),
            _p(_item_sales_aging(item), cell),
            _p(status_cell, cell),
        ])
    widths = [10 * mm, 24 * mm, 38 * mm, 16 * mm, 18 * mm, 18 * mm, 20 * mm, 18 * mm, 32 * mm]
    item_table = Table(table_data, colWidths=widths, repeatRows=1)
    item_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), GREEN),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#d7ddd9")),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("ALIGN", (0, 1), (0, -1), "CENTER"),
        ("ALIGN", (4, 1), (7, -1), "CENTER"),
        ("LEFTPADDING", (0, 0), (-1, -1), 3),
        ("RIGHTPADDING", (0, 0), (-1, -1), 3),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
    ]))
    story.append(item_table)
    story.append(Spacer(1, 10))

    sig_cell = styles["Normal"].clone("Sig")
    sig_cell.fontName = "Helvetica-Bold"
    sig_cell.fontSize = 8
    sig_cell.alignment = 1
    sig_small = styles["Normal"].clone("SigSmall")
    sig_small.fontSize = 7
    sig_small.alignment = 1
    signatures = Table(
        [[
            [Paragraph("REQUESTED BY", sig_cell), Spacer(1, 14 * mm), Paragraph("Signature", sig_small)],
            [Paragraph("RECEIVED BY", sig_cell), Spacer(1, 14 * mm), Paragraph("Signature", sig_small)],
            [Paragraph("APPROVED BY", sig_cell), Spacer(1, 14 * mm), Paragraph("Signature", sig_small)],
            [Paragraph("DISPATCHED BY", sig_cell), Spacer(1, 14 * mm), Paragraph("Signature", sig_small)],
        ]],
        colWidths=[doc.width / 4] * 4,
    )
    signatures.setStyle(TableStyle([
        ("BOX", (0, 0), (-1, -1), 0.6, RULE),
        ("INNERGRID", (0, 0), (-1, -1), 0.4, RULE),
        ("ALIGN", (0, 0), (-1, -1), "CENTER"),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
    ]))
    notes = Table(
        [[Paragraph(f"<b>Notes:</b> {PRINT_NOTES}", small)]],
        colWidths=[doc.width],
    )
    notes.setStyle(TableStyle([
        ("BOX", (0, 0), (-1, -1), 0.6, RULE),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
    ]))
    story.append(KeepTogether([signatures, notes]))

    def _canvas_factory(*args, **kwargs):
        kwargs["pageCompression"] = 0
        return _PrintCanvas(*args, **kwargs)

    doc.build(story, canvasmaker=_canvas_factory)
    return buffer.getvalue()
