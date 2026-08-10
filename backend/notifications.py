"""
Notification service for NMTS / Sleeping Stock — Gmail SMTP + WhatsApp Cloud API.

Design rules (see UPDATE_NOTES.txt for the full explanation):
- Credentials are read only from environment variables (backend/.env). Nothing
  here ever hardcodes a password, token, or phone number.
- Every send is wrapped so a delivery failure NEVER raises out to the caller.
  The request/approval/rejection is always saved first; notifications are a
  best-effort side effect logged to db.notification_logs.
- WhatsApp test mode (WHATSAPP_TEST_MODE=true) always overrides the recipient
  with WHATSAPP_TEST_RECIPIENT_NUMBER and ignores database numbers.
"""
import os
import re
import smtplib
import ssl
import uuid
import asyncio
import logging
from io import BytesIO
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.application import MIMEApplication
from datetime import datetime, timezone

import requests

from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.platypus import BaseDocTemplate, PageTemplate, Frame, Table, TableStyle, Paragraph, Spacer
from reportlab.pdfgen import canvas as pdfcanvas

logger = logging.getLogger("nmts.notifications")


# --------------------------------------------------------------------------
# Config (env-only — never hardcoded)
# --------------------------------------------------------------------------
def _env(key: str, default: str = "") -> str:
    return os.environ.get(key, default) or default


def _first_env(*keys: str, default: str = "") -> str:
    for key in keys:
        value = os.environ.get(key)
        if value:
            return value
    return default


def gmail_settings() -> dict:
    """Resolve Gmail SMTP config, accepting either the GMAIL_SMTP_* names or
    the project's existing SMTP_* names — whichever is present in .env."""
    return {
        "host": _first_env("GMAIL_SMTP_HOST", "SMTP_HOST", default="smtp.gmail.com"),
        "port": int(_first_env("GMAIL_SMTP_PORT", "SMTP_PORT", default="587") or "587"),
        "username": _first_env("GMAIL_SMTP_USERNAME", "SMTP_EMAIL"),
        "password": _first_env("GMAIL_SMTP_APP_PASSWORD", "SMTP_PASSWORD"),
        "sender_name": _first_env("GMAIL_SENDER_NAME", "SMTP_FROM_NAME", default="Sleeping Stock - NMTS"),
    }


def gmail_configured() -> bool:
    settings = gmail_settings()
    return bool(settings["username"] and settings["password"])


def whatsapp_configured() -> bool:
    return bool(_env("WHATSAPP_ACCESS_TOKEN") and _env("WHATSAPP_PHONE_NUMBER_ID"))


def whatsapp_test_mode() -> bool:
    return _env("WHATSAPP_TEST_MODE", "true").strip().lower() in ("1", "true", "yes")


# --------------------------------------------------------------------------
# Sanitizers
# --------------------------------------------------------------------------
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def is_valid_email(value: str) -> bool:
    return bool(value) and bool(_EMAIL_RE.match(value.strip()))


def normalize_phone_number(value: str, default_country_code: str = "91") -> str:
    """Normalize to E.164-ish digits-only international format (no leading +)."""
    if not value:
        return ""
    digits = re.sub(r"[^0-9]", "", str(value))
    if not digits:
        return ""
    if len(digits) == 10:  # bare local number
        digits = default_country_code + digits
    return digits


def sanitize_text(value: str, max_len: int = 500) -> str:
    if value is None:
        return ""
    text = str(value).replace("\r", " ").replace("\n", " ").strip()
    return text[:max_len]


# --------------------------------------------------------------------------
# Gmail SMTP
# --------------------------------------------------------------------------
def _build_email_html(context: dict) -> str:
    rows = "".join(
        f'<tr><td style="padding:4px 10px;color:#6B7280;">{k}</td>'
        f'<td style="padding:4px 10px;font-weight:600;">{v}</td></tr>'
        for k, v in context.get("fields", [])
    )
    return f"""
    <div style="font-family:Arial,sans-serif;max-width:560px;margin:auto;border:1px solid #D1D5DB;border-radius:10px;overflow:hidden;">
      <div style="background:#047857;color:#fff;padding:16px;">
        <div style="font-size:16px;font-weight:800;">Sleeping Stock · NMTS</div>
        <div style="font-size:13px;opacity:0.9;">{context.get('headline', '')}</div>
      </div>
      <div style="padding:16px;">
        <table style="width:100%;border-collapse:collapse;font-size:13px;">{rows}</table>
        {f'<p style="margin-top:12px;font-size:12px;color:#6B7280;">{context.get("footer", "")}</p>' if context.get('footer') else ''}
      </div>
    </div>
    """


def _build_email_text(context: dict) -> str:
    lines = [context.get("headline", "")]
    lines += [f"{k}: {v}" for k, v in context.get("fields", [])]
    if context.get("footer"):
        lines.append(context["footer"])
    return "\n".join(lines)


def send_gmail_email(to_email: str, subject: str, context: dict, cc_email: str = "") -> dict:
    """Returns a result dict; never raises."""
    to_email = (to_email or "").strip()
    cc_email = (cc_email or "").strip()
    if cc_email and not is_valid_email(cc_email):
        cc_email = ""
    if not is_valid_email(to_email):
        return {"status": "skipped", "error": "invalid_or_missing_email"}
    if not gmail_configured():
        return {"status": "skipped", "error": "gmail_not_configured"}

    settings = gmail_settings()
    username = settings["username"]
    app_password = settings["password"]
    host = settings["host"]
    port = settings["port"]
    sender_name = settings["sender_name"]

    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = sanitize_text(subject, 200)
        msg["From"] = f"{sender_name} <{username}>"
        msg["To"] = to_email
        recipients = [to_email]
        if cc_email:
            msg["Cc"] = cc_email
            recipients.append(cc_email)
        msg.attach(MIMEText(_build_email_text(context), "plain"))
        msg.attach(MIMEText(_build_email_html(context), "html"))

        context_ssl = ssl.create_default_context()
        with smtplib.SMTP(host, port, timeout=15) as server:
            server.starttls(context=context_ssl)
            server.login(username, app_password)
            server.sendmail(username, recipients, msg.as_string())
        return {"status": "sent", "provider_response": "smtp_ok"}
    except Exception as exc:  # noqa: BLE001 — a delivery failure must never propagate
        # Never log the credentials themselves, only the (safe) error message.
        safe_error = str(exc).replace(app_password, "***") if app_password else str(exc)
        logger.warning("Gmail send failed: %s", safe_error)
        return {"status": "failed", "error": safe_error[:300]}


# --------------------------------------------------------------------------
# WhatsApp Cloud API (Meta official)
# --------------------------------------------------------------------------
def send_whatsapp_message(to_number: str, summary: dict) -> dict:
    """Sends a plain-text WhatsApp message via the Meta Cloud API. Never raises.
    In test mode the recipient is always forced to WHATSAPP_TEST_RECIPIENT_NUMBER,
    regardless of what `to_number` was passed in."""
    test_mode = whatsapp_test_mode()
    if test_mode:
        recipient = normalize_phone_number(_env("WHATSAPP_TEST_RECIPIENT_NUMBER"))
        logger.info("[WhatsApp TEST MODE] Redirecting notification to test recipient only.")
    else:
        recipient = normalize_phone_number(to_number)

    if not recipient:
        return {"status": "skipped", "error": "no_recipient_number", "test_mode": test_mode}
    if not whatsapp_configured():
        return {"status": "skipped", "error": "whatsapp_not_configured", "test_mode": test_mode}

    base_url = _env("WHATSAPP_API_BASE_URL", "https://graph.facebook.com")
    api_version = _env("WHATSAPP_API_VERSION", "v20.0")
    phone_number_id = _env("WHATSAPP_PHONE_NUMBER_ID")
    token = _env("WHATSAPP_ACCESS_TOKEN")

    text = sanitize_text(
        "Sleeping Stock / NMTS Request Notification\n"
        f"Request ID: {summary.get('request_id', '-')}\n"
        f"Status: {summary.get('status', '-')}\n"
        f"Requester: {summary.get('requester_name', '-')}\n"
        f"Sender Branch: {summary.get('sender_branch', '-')}\n"
        f"Receiver Branch: {summary.get('receiver_branch', '-')}\n"
        f"Part Count: {summary.get('part_count', '-')}\n"
        f"Requested Qty: {summary.get('requested_qty', '-')}\n"
        f"Date/Time: {summary.get('datetime', '-')}",
        1000,
    )
    url = f"{base_url}/{api_version}/{phone_number_id}/messages"
    payload = {
        "messaging_product": "whatsapp",
        "to": recipient,
        "type": "text",
        "text": {"body": text},
    }
    try:
        resp = requests.post(
            url, json=payload, headers={"Authorization": f"Bearer {token}"}, timeout=15
        )
        if resp.status_code >= 400:
            safe_error = str(resp.text)[:300].replace(token, "***") if token else str(resp.text)[:300]
            return {"status": "failed", "error": safe_error, "test_mode": test_mode}
        data = resp.json() if resp.content else {}
        message_id = (data.get("messages") or [{}])[0].get("id", "")
        return {"status": "sent", "provider_response": message_id, "test_mode": test_mode}
    except Exception as exc:  # noqa: BLE001
        safe_error = str(exc).replace(token, "***") if token else str(exc)
        logger.warning("WhatsApp send failed: %s", safe_error)
        return {"status": "failed", "error": safe_error[:300], "test_mode": test_mode}


# --------------------------------------------------------------------------
# High-level: notify + log, called after a request/status change is saved
# --------------------------------------------------------------------------
async def notify_request_event(db, event: str, request_doc: dict, recipients: list, remarks: str = ""):
    """recipients: list of {'email':..., 'mobile':..., 'name':...} dicts.
    Never raises — every channel attempt is wrapped and logged individually.
    Returns a summary the frontend can use for a status toast."""
    now = datetime.now(timezone.utc).isoformat()
    fields = [
        ("Request ID", request_doc.get("id", request_doc.get("order_number", "-"))),
        ("Status", request_doc.get("status", "-")),
        ("Requester", request_doc.get("requested_user_name", "-")),
        ("Sender (Requesting)", f"{request_doc.get('requesting_brand','-')} / {request_doc.get('requesting_dealer','-')} / {request_doc.get('requesting_branch','-')}"),
        ("Receiver (Supplying)", f"{request_doc.get('supplying_brand', request_doc.get('requesting_brand','-'))} / {request_doc.get('supplying_dealer','-')} / {request_doc.get('supplying_branch','-')}"),
        ("Part Number", request_doc.get("part_number", "-")),
        ("Requested Qty", request_doc.get("requested_qty", "-")),
        ("Purchase Aging", request_doc.get("purchase_aging_days_at_request", request_doc.get("purchase_aging", "-"))),
        ("Sales Aging", request_doc.get("sales_aging_days_at_request", request_doc.get("sales_aging", "-"))),
        ("Remarks", sanitize_text(remarks, 300) or "-"),
    ]
    context = {"headline": event, "fields": fields, "footer": "This is an automated notification from NMTS / Sleeping Stock."}

    email_result = {"status": "skipped", "error": "no_recipients"}
    whatsapp_result = {"status": "skipped", "error": "no_recipients"}
    any_email_sent, any_whatsapp_sent = False, False
    email_attempted, whatsapp_attempted = False, False

    for recipient in recipients:
        email = (recipient or {}).get("email")
        mobile = (recipient or {}).get("mobile")
        if email:
            email_attempted = True
            email_result = await asyncio.get_event_loop().run_in_executor(
                None, send_gmail_email, email, f"NMTS — {event}", context
            )
            await db.notification_logs.insert_one({
                "id": str(uuid.uuid4()), "request_id": request_doc.get("id"), "event": event,
                "channel": "Email", "recipient": email, "delivery_status": email_result.get("status"),
                "provider_response": email_result.get("provider_response", ""),
                "error": email_result.get("error", ""), "retry_count": 0, "created_at": now,
            })
            any_email_sent = any_email_sent or email_result.get("status") == "sent"
        if mobile:
            whatsapp_attempted = True
            whatsapp_result = await asyncio.get_event_loop().run_in_executor(
                None, send_whatsapp_message, mobile, {
                    "request_id": request_doc.get("id", request_doc.get("order_number", "-")),
                    "status": request_doc.get("status", "-"),
                    "requester_name": request_doc.get("requested_user_name", "-"),
                    "sender_branch": request_doc.get("requesting_branch", "-"),
                    "receiver_branch": request_doc.get("supplying_branch", "-"),
                    "part_count": 1,
                    "requested_qty": request_doc.get("requested_qty", "-"),
                    "datetime": now,
                },
            )
            await db.notification_logs.insert_one({
                "id": str(uuid.uuid4()), "request_id": request_doc.get("id"), "event": event,
                "channel": "WhatsApp", "recipient": mobile if not whatsapp_result.get("test_mode") else "(test recipient)",
                "delivery_status": whatsapp_result.get("status"),
                "provider_response": whatsapp_result.get("provider_response", ""),
                "error": whatsapp_result.get("error", ""), "retry_count": 0, "created_at": now,
            })
            any_whatsapp_sent = any_whatsapp_sent or whatsapp_result.get("status") == "sent"

    return {
        "email_attempted": email_attempted, "email_sent": any_email_sent,
        "whatsapp_attempted": whatsapp_attempted, "whatsapp_sent": any_whatsapp_sent,
    }


# --------------------------------------------------------------------------
# Parts Transfer Request PDF (ReportLab) — used as the Gmail attachment for
# a newly created request. One PDF per Requested-To destination group.
# --------------------------------------------------------------------------
_PDF_BASE_STYLE = getSampleStyleSheet()["Normal"]


def _pdf_escape(value) -> str:
    text = sanitize_text(value, 300) if value not in (None, "") else ""
    text = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    return text or "-"


def _pdf_format_number(value) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "-"
    if number == int(number):
        return f"{int(number):,}"
    return f"{number:,.2f}"


def _pdf_format_aging(value) -> str:
    """Purchase/Sales Aging must show '-' (per existing UI convention) rather
    than crash when older records are missing the field."""
    if value in (None, ""):
        return "—"
    try:
        return str(int(float(value)))
    except (TypeError, ValueError):
        return "—"


def _pdf_kv(label: str, value) -> Paragraph:
    return Paragraph(
        f"<font size=7 color='#6B7280'>{_pdf_escape(label)}</font><br/>"
        f"<font size=9.5 color='#111827'><b>{_pdf_escape(value)}</b></font>",
        _PDF_BASE_STYLE,
    )


class _NumberedCanvas(pdfcanvas.Canvas):
    """Draws 'Page X of Y' on every page — requires a two-pass save, which
    is the standard ReportLab pattern for a total page count that isn't
    known until the whole flowable story has been laid out."""

    def __init__(self, *args, **kwargs):
        pdfcanvas.Canvas.__init__(self, *args, **kwargs)
        self._saved_page_states = []

    def showPage(self):
        self._saved_page_states.append(dict(self.__dict__))
        self._startPage()

    def save(self):
        total_pages = len(self._saved_page_states)
        for state in self._saved_page_states:
            self.__dict__.update(state)
            self._draw_page_number(total_pages)
            pdfcanvas.Canvas.showPage(self)
        pdfcanvas.Canvas.save(self)

    def _draw_page_number(self, total_pages: int):
        self.setFont("Helvetica", 8)
        self.setFillColor(colors.HexColor("#6B7280"))
        self.drawRightString(A4[0] - 14 * mm, 10 * mm, f"Page {self._pageNumber} of {total_pages}")
        self.drawString(14 * mm, 10 * mm, "This is a system-generated Parts Transfer Request from Sleeping Stock / NMTS.")


def build_request_pdf(group: dict) -> bytes:
    """Builds the official Parts Transfer Request PDF for one Requested-To
    destination group (one request_number, one receiver). Multi-page safe:
    the item table's header row repeats on every page (repeatRows=1) and
    the signature block is the last flowable in the story, so it only ever
    renders on the final page."""
    buffer = BytesIO()
    doc = BaseDocTemplate(
        buffer, pagesize=A4,
        leftMargin=14 * mm, rightMargin=14 * mm, topMargin=14 * mm, bottomMargin=20 * mm,
    )
    frame = Frame(doc.leftMargin, doc.bottomMargin, doc.width, doc.height, id="main")
    doc.addPageTemplates([PageTemplate(id="main", frames=[frame])])

    styles = getSampleStyleSheet()
    title_style = styles["Title"].clone("RequestTitle")
    title_style.textColor = colors.HexColor("#047857")
    title_style.fontSize = 18
    title_style.spaceAfter = 2
    subtitle_style = styles["Normal"].clone("RequestSubtitle")
    subtitle_style.fontSize = 12
    subtitle_style.textColor = colors.HexColor("#374151")
    subtitle_style.spaceAfter = 8
    section_style = styles["Normal"].clone("SectionLabel")
    section_style.fontSize = 10
    section_style.textColor = colors.HexColor("#047857")
    cell_style = styles["Normal"].clone("Cell")
    cell_style.fontSize = 8
    cell_style.leading = 10
    header_cell_style = cell_style.clone("HeaderCell")
    header_cell_style.textColor = colors.white
    header_cell_style.fontName = "Helvetica-Bold"

    story = []
    story.append(Paragraph("Sleeping Stock", title_style))
    story.append(Paragraph("Parts Transfer Request", subtitle_style))

    created_display = str(group.get("created_at", ""))[:16].replace("T", "   ")
    header_table = Table(
        [
            [_pdf_kv("Request Number", group.get("request_number", "-")),
             _pdf_kv("Order / Reference Number", group.get("order_number", "-"))],
            [_pdf_kv("Created Date & Time", created_display),
             _pdf_kv("Request Status", group.get("status", "Requested"))],
        ],
        colWidths=[doc.width / 2, doc.width / 2],
    )
    header_table.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("BOTTOMPADDING", (0, 0), (-1, -1), 6)]))
    story.append(header_table)
    story.append(Spacer(1, 6))

    requested_by = Table(
        [
            [Paragraph("Requested By", section_style)],
            [_pdf_kv("Name", group.get("requested_user_name", "-"))],
            [_pdf_kv("Brand", group.get("requesting_brand", "-"))],
            [_pdf_kv("Dealer", group.get("requesting_dealer", "-"))],
            [_pdf_kv("Branch", group.get("requesting_branch", "-"))],
        ],
        colWidths=[doc.width / 2 - 4],
    )
    requested_to = Table(
        [
            [Paragraph("Requested To", section_style)],
            [_pdf_kv("Brand", group.get("supplying_brand", "-"))],
            [_pdf_kv("Dealer", group.get("supplying_dealer", "-"))],
            [_pdf_kv("Branch", group.get("supplying_branch", "-"))],
            [_pdf_kv("Location", group.get("supplying_branch", "-"))],
        ],
        colWidths=[doc.width / 2 - 4],
    )
    for t in (requested_by, requested_to):
        t.setStyle(TableStyle([("BOTTOMPADDING", (0, 0), (-1, -1), 3), ("TOPPADDING", (0, 0), (-1, -1), 1)]))
    two_col = Table([[requested_by, requested_to]], colWidths=[doc.width / 2, doc.width / 2])
    two_col.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP")]))
    story.append(two_col)
    story.append(Spacer(1, 10))

    header_row = ["S.No", "Part Number", "Part Name / Description", "Req. Qty", "Avail. Qty",
                  "Value", "Purchase Aging", "Sales Aging", "LOC"]
    table_data = [[Paragraph(h, header_cell_style) for h in header_row]]
    items = group.get("items", []) or []
    for idx, item in enumerate(items, start=1):
        table_data.append([
            Paragraph(str(idx), cell_style),
            Paragraph(_pdf_escape(item.get("part_number")), cell_style),
            Paragraph(_pdf_escape(item.get("description")), cell_style),
            Paragraph(_pdf_format_number(item.get("requested_qty")), cell_style),
            Paragraph(_pdf_format_number(item.get("available_qty_at_request")), cell_style),
            Paragraph(_pdf_format_number(item.get("value")), cell_style),
            Paragraph(_pdf_format_aging(item.get("purchase_aging_days")), cell_style),
            Paragraph(_pdf_format_aging(item.get("sales_aging_days")), cell_style),
            Paragraph(_pdf_escape(item.get("loc")) if item.get("loc") else "—", cell_style),
        ])

    fixed_widths_mm = [8, 24, 16, 18, 18, 18, 18, 18]  # all columns except description
    description_width = doc.width - sum(fixed_widths_mm) * mm
    col_widths = [
        fixed_widths_mm[0] * mm, fixed_widths_mm[1] * mm, description_width,
        fixed_widths_mm[2] * mm, fixed_widths_mm[3] * mm, fixed_widths_mm[4] * mm,
        fixed_widths_mm[5] * mm, fixed_widths_mm[6] * mm, fixed_widths_mm[7] * mm,
    ]
    item_table = Table(table_data, colWidths=col_widths, repeatRows=1)
    item_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#047857")),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#D1D5DB")),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#F0FDF4")]),
        ("LEFTPADDING", (0, 0), (-1, -1), 4), ("RIGHTPADDING", (0, 0), (-1, -1), 4),
        ("TOPPADDING", (0, 0), (-1, -1), 3), ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
    ]))
    story.append(item_table)
    story.append(Spacer(1, 10))

    summary_table = Table(
        [[
            _pdf_kv("Total Items", str(group.get("total_items", len(items)))),
            _pdf_kv("Total Quantity", _pdf_format_number(group.get("total_qty"))),
            _pdf_kv("Total Value", _pdf_format_number(group.get("total_value"))),
        ]],
        colWidths=[doc.width / 3] * 3,
    )
    story.append(summary_table)
    story.append(Spacer(1, 26))

    # Signature block — always the last flowable, so it only ever appears on
    # the final printed page, never repeated mid-document.
    signature_table = Table(
        [[
            Paragraph("Requested By (Signature)", cell_style),
            Paragraph("Requested To / Approved By (Signature)", cell_style),
        ]],
        colWidths=[doc.width / 2, doc.width / 2],
    )
    signature_table.setStyle(TableStyle([
        ("LINEABOVE", (0, 0), (0, 0), 0.75, colors.HexColor("#9CA3AF")),
        ("LINEABOVE", (1, 0), (1, 0), 0.75, colors.HexColor("#9CA3AF")),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
    ]))
    story.append(signature_table)

    doc.build(story, canvasmaker=_NumberedCanvas)
    return buffer.getvalue()


# --------------------------------------------------------------------------
# Parts Transfer Request email (Gmail SMTP, PDF attachment)
# --------------------------------------------------------------------------
def send_request_pdf_email(to_email: str, group: dict, pdf_bytes: bytes, cc_email: str = "") -> dict:
    """Sends the Parts Transfer Request PDF as a Gmail SMTP attachment.
    Returns a result dict; never raises — a delivery failure must never
    roll back the already-saved request."""
    to_email = (to_email or "").strip()
    cc_email = (cc_email or "").strip()
    if cc_email and not is_valid_email(cc_email): cc_email = ""
    if not is_valid_email(to_email):
        return {"status": "skipped", "error": "invalid_or_missing_email"}

    settings = gmail_settings()
    if not (settings["username"] and settings["password"]):
        return {"status": "skipped", "error": "gmail_not_configured"}

    request_number = group.get("request_number", "-")
    subject = f"Parts Transfer Request - {request_number}"
    filename = group.get("pdf_filename") or f"{request_number}.pdf"

    # Temporary professional placeholder body (PDF attachment is the source of truth).
    text_body = (
        "Dear Team,\n\n"
        "Please find attached the Parts Transfer Request for your review and necessary action.\n\n"
        "Kindly check the requested parts and update the request status accordingly.\n\n"
        "Regards,\n"
        "Sleeping Stock Team"
    )
    html_body = f"""
    <div style="font-family:Arial,sans-serif;max-width:560px;margin:auto;border:1px solid #D1D5DB;border-radius:10px;overflow:hidden;">
      <div style="background:#047857;color:#fff;padding:16px;">
        <div style="font-size:16px;font-weight:800;">Sleeping Stock · NMTS</div>
        <div style="font-size:13px;opacity:0.9;">Parts Transfer Request - {sanitize_text(request_number, 80)}</div>
      </div>
      <div style="padding:16px;font-size:13px;line-height:1.5;color:#111827;">
        <p>Dear Team,</p>
        <p>Please find attached the Parts Transfer Request for your review and necessary action.</p>
        <p>Kindly check the requested parts and update the request status accordingly.</p>
        <p>Regards,<br/>Sleeping Stock Team</p>
      </div>
    </div>
    """

    try:
        msg = MIMEMultipart("mixed")
        msg["Subject"] = sanitize_text(subject, 200)
        msg["From"] = f"{settings['sender_name']} <{settings['username']}>"
        msg["To"] = to_email
        if cc_email: msg["Cc"] = cc_email

        alt = MIMEMultipart("alternative")
        alt.attach(MIMEText(text_body, "plain"))
        alt.attach(MIMEText(html_body, "html"))
        msg.attach(alt)

        attachment = MIMEApplication(pdf_bytes, _subtype="pdf")
        attachment.add_header("Content-Disposition", "attachment", filename=filename)
        msg.attach(attachment)

        context_ssl = ssl.create_default_context()
        with smtplib.SMTP(settings["host"], settings["port"], timeout=20) as server:
            server.starttls(context=context_ssl)
            server.login(settings["username"], settings["password"])
            server.sendmail(settings["username"], [to_email] + ([cc_email] if cc_email else []), msg.as_string())
        return {"status": "sent", "provider_response": "smtp_ok"}
    except Exception as exc:  # noqa: BLE001 — a delivery failure must never propagate
        password = settings.get("password") or ""
        safe_error = str(exc).replace(password, "***") if password else str(exc)
        logger.warning("Gmail Parts Transfer Request send failed: %s", safe_error)
        return {"status": "failed", "error": safe_error[:300]}
