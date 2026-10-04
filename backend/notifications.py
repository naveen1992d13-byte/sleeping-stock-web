"""
Notification service for NMTS / Sleeping Stock — Gmail API + WhatsApp Cloud API.

Design rules:
- Email is sent with the Gmail API (OAuth 2.0 refresh token) as
  Sleeping Stock <notifications@sleepingstock.in>. No Gmail password, no SMTP
  app password, and no Amazon SES send path.
- Every send is wrapped so a delivery failure NEVER raises out to the caller.
  The request/approval/rejection is always saved first; notifications are a
  best-effort side effect logged to db.notification_logs.
- WhatsApp test mode (WHATSAPP_TEST_MODE=true) always overrides the recipient
  with WHATSAPP_TEST_RECIPIENT_NUMBER and ignores database numbers.
- Email test mode (EMAIL_TEST_MODE=true) always overrides recipients
  with EMAIL_TEST_RECIPIENT and drops CC. Unset in production.
"""
import os
import re
import uuid
import asyncio
import logging
import base64
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.application import MIMEApplication
from datetime import datetime, timezone

import requests

from request_print import build_request_pdf as build_request_print_pdf

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


DEFAULT_GMAIL_FROM_EMAIL = "notifications@sleepingstock.in"
DEFAULT_GMAIL_FROM_NAME = "Sleeping Stock"
GMAIL_SEND_SCOPE = "https://www.googleapis.com/auth/gmail.send"


def gmail_settings() -> dict:
    """Gmail API OAuth send config. Never reads a Gmail password or SMTP secret."""
    return {
        "from_email": _first_env("GMAIL_FROM_EMAIL", default=DEFAULT_GMAIL_FROM_EMAIL).strip(),
        "from_name": _first_env("GMAIL_FROM_NAME", default=DEFAULT_GMAIL_FROM_NAME).strip(),
        "client_id": _env("GMAIL_OAUTH_CLIENT_ID").strip(),
        "client_secret": _env("GMAIL_OAUTH_CLIENT_SECRET").strip(),
        "refresh_token": _env("GMAIL_OAUTH_REFRESH_TOKEN").strip(),
        "token_uri": _first_env("GMAIL_OAUTH_TOKEN_URI", default="https://oauth2.googleapis.com/token").strip(),
    }


def gmail_configured() -> bool:
    settings = gmail_settings()
    return bool(
        is_valid_email(settings["from_email"])
        and settings["client_id"]
        and settings["client_secret"]
        and settings["refresh_token"]
    )


def ses_settings() -> dict:
    """Compatibility alias. Email transport is Gmail API, not SES."""
    settings = gmail_settings()
    return {
        "from_email": settings["from_email"],
        "from_name": settings["from_name"],
        "region": "",
        "configuration_set": "",
    }


def ses_configured() -> bool:
    return gmail_configured()


def _gmail_from_header(settings=None) -> str:
    settings = settings or gmail_settings()
    name = sanitize_text(settings.get("from_name") or DEFAULT_GMAIL_FROM_NAME, 80)
    return f"{name} <{settings['from_email']}>"


def _gmail_credentials():
    settings = gmail_settings()
    from google.oauth2.credentials import Credentials
    return Credentials(
        token=None,
        refresh_token=settings["refresh_token"],
        token_uri=settings["token_uri"],
        client_id=settings["client_id"],
        client_secret=settings["client_secret"],
        scopes=[GMAIL_SEND_SCOPE],
    )


def _gmail_service():
    from googleapiclient.discovery import build
    return build("gmail", "v1", credentials=_gmail_credentials(), cache_discovery=False)


def _build_rfc822_message(
    *,
    to_list,
    cc_list,
    subject: str,
    text_body: str,
    html_body: str,
    pdf_bytes: bytes = None,
    filename: str = "",
):
    """Reuse the existing MIME/PDF layout. Gmail API sends this RFC822 blob."""
    if pdf_bytes:
        msg = MIMEMultipart("mixed")
    else:
        msg = MIMEMultipart("alternative")
    msg["Subject"] = sanitize_text(subject, 200)
    msg["From"] = _gmail_from_header()
    msg["To"] = ", ".join(to_list)
    if cc_list:
        msg["Cc"] = ", ".join(cc_list)
    if pdf_bytes:
        alt = MIMEMultipart("alternative")
        alt.attach(MIMEText(text_body or "", "plain"))
        alt.attach(MIMEText(html_body or "", "html"))
        msg.attach(alt)
        attachment = MIMEApplication(pdf_bytes, _subtype="pdf")
        attachment.add_header("Content-Disposition", "attachment", filename=filename or "request.pdf")
        msg.attach(attachment)
    else:
        msg.attach(MIMEText(text_body or "", "plain"))
        msg.attach(MIMEText(html_body or "", "html"))
    return msg


def _send_gmail_mime(raw_message: bytes) -> dict:
    """users.messages.send of a raw RFC822 message. Appears in Gmail Sent. Never raises."""
    if not gmail_configured():
        return {"status": "skipped", "error": "gmail_not_configured"}
    try:
        encoded = base64.urlsafe_b64encode(raw_message).decode("ascii")
        sent = _gmail_service().users().messages().send(
            userId="me",
            body={"raw": encoded},
        ).execute()
        return {"status": "sent", "provider_response": sent.get("id") or "gmail_ok"}
    except Exception as exc:  # noqa: BLE001
        logger.warning("Gmail API send failed: %s", str(exc)[:300])
        return {"status": "failed", "error": str(exc)[:300]}


def whatsapp_configured() -> bool:
    return bool(_env("WHATSAPP_ACCESS_TOKEN") and _env("WHATSAPP_PHONE_NUMBER_ID"))


def whatsapp_test_mode() -> bool:
    return _env("WHATSAPP_TEST_MODE", "true").strip().lower() in ("1", "true", "yes")


def email_test_mode() -> bool:
    return _env("EMAIL_TEST_MODE", "").strip().lower() in ("1", "true", "yes")


def _email_test_redirect(to_email: str, cc_email: str = "") -> tuple:
    """When EMAIL_TEST_MODE is on, send only to EMAIL_TEST_RECIPIENT."""
    if not email_test_mode():
        return to_email, cc_email
    test_to = _env("EMAIL_TEST_RECIPIENT").strip()
    logger.info("[Email TEST MODE] Redirecting notification to test recipient only.")
    return test_to, ""


# --------------------------------------------------------------------------
# Sanitizers
# --------------------------------------------------------------------------
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def is_valid_email(value: str) -> bool:
    return bool(value) and bool(_EMAIL_RE.match(value.strip()))


def _email_list(value) -> list:
    """Split a string or sequence into unique valid emails, first-seen order."""
    if not value:
        return []
    if isinstance(value, (list, tuple, set)):
        items = list(value)
    else:
        items = re.split(r"[,;]+", str(value))
    out, seen = [], set()
    for item in items:
        email = (item or "").strip()
        if not is_valid_email(email):
            continue
        key = email.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(email)
    return out


def _norm_scope(value: str) -> str:
    return (value or "").strip().lower()


def _role_of(user: dict) -> str:
    return str((user or {}).get("role") or "").strip().lower()


def _scope_emails(users, *, dealer: str, branch: str = None, roles=()) -> list:
    """Active non-master emails for a dealer, optionally pinned to a branch.
    Group/location matches are case-insensitive. Missing roles return []."""
    dealer_n = _norm_scope(dealer)
    branch_n = _norm_scope(branch) if branch else ""
    role_set = {str(role or "").strip().lower() for role in (roles or ())}
    out, seen = [], set()
    for user in users or []:
        if _role_of(user) == "master":
            continue
        status = str((user or {}).get("status") or "").strip().lower()
        if status and status != "active":
            continue
        if role_set and _role_of(user) not in role_set:
            continue
        if dealer_n and _norm_scope(user.get("group")) != dealer_n:
            continue
        if branch and _norm_scope(user.get("location")) != branch_n:
            continue
        email = str((user or {}).get("email") or "").strip()
        if not is_valid_email(email):
            continue
        key = email.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(email)
    return out


def resolve_request_email_routing(users, group: dict) -> tuple:
    """TO = supplying Dealer/Branch user when present.

    If that branch has no valid role=user email, the responsible Admin
    (supplying-dealer admin, else requesting-dealer admin) is promoted
    from CC to TO. CC is requesting Dealer/Branch user + remaining
    Admins. Master Admin is never included. Addresses are deduplicated.
    Empty TO still means 'Receiver email not configured'.
    """
    group = group or {}
    to_emails = _scope_emails(
        users,
        dealer=group.get("supplying_dealer"),
        branch=group.get("supplying_branch"),
        roles=("user",),
    )
    requesting_user_cc = _scope_emails(
        users,
        dealer=group.get("requesting_dealer"),
        branch=group.get("requesting_branch"),
        roles=("user",),
    )
    requesting_admin = _scope_emails(
        users, dealer=group.get("requesting_dealer"), roles=("admin",),
    )
    supplying_admin = _scope_emails(
        users, dealer=group.get("supplying_dealer"), roles=("admin",),
    )
    if not to_emails:
        to_emails = list(supplying_admin) or list(requesting_admin)
    cc_emails = []
    cc_emails.extend(requesting_user_cc)
    cc_emails.extend(requesting_admin)
    cc_emails.extend(supplying_admin)
    to_keys = {email.lower() for email in to_emails}
    seen_cc = set()
    deduped_cc = []
    for email in cc_emails:
        key = email.lower()
        if key in to_keys or key in seen_cc:
            continue
        seen_cc.add(key)
        deduped_cc.append(email)
    return to_emails, deduped_cc


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
# Transactional HTML helpers (unchanged templates)
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
    """Compatibility wrapper. Sends through Gmail API. Never raises."""
    return send_notification_email(to_email, subject, context, cc_email=cc_email)


def send_notification_email(to_email: str, subject: str, context: dict, cc_email: str = "") -> dict:
    """Returns a result dict; never raises."""
    to_email, cc_email = _email_test_redirect((to_email or "").strip(), (cc_email or "").strip())
    to_list = _email_list(to_email)
    to_keys = {email.lower() for email in to_list}
    cc_list = [email for email in _email_list(cc_email) if email.lower() not in to_keys]
    if not to_list:
        return {"status": "skipped", "error": "invalid_or_missing_email"}
    if not gmail_configured():
        return {"status": "skipped", "error": "gmail_not_configured"}
    try:
        msg = _build_rfc822_message(
            to_list=to_list,
            cc_list=cc_list,
            subject=subject,
            text_body=_build_email_text(context),
            html_body=_build_email_html(context),
        )
        return _send_gmail_mime(msg.as_bytes())
    except Exception as exc:  # noqa: BLE001
        logger.warning("Gmail notification email failed: %s", str(exc)[:300])
        return {"status": "failed", "error": str(exc)[:300]}


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
# Parts Transfer Request PDF — Request Center Print layout converted to PDF.
# --------------------------------------------------------------------------
def _pdf_format_number(value) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "-"
    if number == int(number):
        return f"{int(number):,}"
    return f"{number:,.2f}"


def build_request_pdf(group: dict) -> bytes:
    """Email attachment is the Request Center Print output converted to PDF."""
    return build_request_print_pdf(group)


# --------------------------------------------------------------------------
# Parts Transfer Request email (Amazon SES, PDF attachment)
# --------------------------------------------------------------------------
WORKFLOW_EMAIL_SENT = "sent"
WORKFLOW_EMAIL_COMPLETED = "completed"
COMPLETED_HEADER_STATUSES = frozenset({"Completed", "Received"})
NO_WORKFLOW_EMAIL_STATUSES = frozenset({
    "Requested", "Approved", "Partially Approved", "Rejected", "Cancelled",
    "Dispatched", "Picking", "picking_finished", "In Transit", "Receive Pending",
    "Snooze",
})


def public_app_url() -> str:
    return _first_env("PUBLIC_APP_BASE_URL", default="https://sleepingstock.in").rstrip("/")


def should_send_request_completion_email(header_status: str) -> bool:
    """True only for Received Confirmed / Finished. No intermediate or reject email."""
    return str(header_status or "").strip() in COMPLETED_HEADER_STATUSES


def workflow_email_kind_for_event(*, created: bool = False, header_status: str = "") -> str:
    """Map a durable request event to the only allowed SES email kind, or empty."""
    if created:
        return WORKFLOW_EMAIL_SENT
    if should_send_request_completion_email(header_status):
        return WORKFLOW_EMAIL_COMPLETED
    return ""


def request_email_claim_filter(header_id: str, *, result: bool = False) -> dict:
    """Durable Mongo claim: one in-flight/success send per request event."""
    sent_field = "result_email_sent" if result else "email_sent"
    claimed_field = "result_email_claimed_at" if result else "email_claimed_at"
    return {
        "id": header_id,
        sent_field: {"$ne": True},
        claimed_field: {"$exists": False},
    }


def _dealer_branch(dealer: str, branch: str) -> str:
    return " ".join(p for p in ((dealer or "").strip(), (branch or "").strip()) if p) or "-"


def build_request_email_subject(group: dict, subject_prefix: str = "", kind: str = WORKFLOW_EMAIL_SENT) -> str:
    prefix = (subject_prefix or "").strip()
    if prefix and not prefix.endswith(" "):
        prefix = prefix + " "
    request_number = str(group.get("request_number", "-") or "-").strip() or "-"
    dealer_branch = _dealer_branch(group.get("supplying_dealer"), group.get("supplying_branch"))
    if kind == WORKFLOW_EMAIL_COMPLETED:
        purpose = "Stock request completed"
    else:
        purpose = "New stock request received"
    return f"{prefix}Sleeping Stock \u2013 {purpose} \u2013 {dealer_branch} \u2013 {request_number}"


def _workflow_totals(group: dict, kind: str) -> dict:
    items = list(group.get("items") or [])
    total_items = group.get("total_items")
    if total_items in (None, ""):
        total_items = len(items)
    if kind == WORKFLOW_EMAIL_COMPLETED:
        finished = [
            item for item in items
            if str(item.get("status") or "") in ("Completed", "Received")
        ]
        qty = 0.0
        value = 0.0
        for item in finished or items:
            try:
                qty += float(item.get("accepted_qty") or item.get("approved_qty") or item.get("requested_qty") or 0)
            except (TypeError, ValueError):
                pass
            try:
                value += float(item.get("value") or item.get("value_at_request") or 0)
            except (TypeError, ValueError):
                pass
        if group.get("accepted_total_qty") not in (None, ""):
            qty = float(group.get("accepted_total_qty") or qty)
        if group.get("total_value") not in (None, "") and not finished:
            value = float(group.get("total_value") or value)
        return {
            "items": len(finished) or total_items,
            "qty": _pdf_format_number(qty if qty else group.get("total_qty")),
            "value": _pdf_format_number(value if value else group.get("total_value")),
        }
    return {
        "items": total_items,
        "qty": _pdf_format_number(group.get("total_qty")),
        "value": _pdf_format_number(group.get("total_value")),
    }


def build_request_workflow_content(group: dict, kind: str = WORKFLOW_EMAIL_SENT, subject_prefix: str = "") -> dict:
    """Customer-facing subject/text/html for the two allowed request emails."""
    group = group or {}
    subject = build_request_email_subject(group, subject_prefix, kind=kind)
    totals = _workflow_totals(group, kind)
    request_number = str(group.get("request_number") or "-")
    order_number = str(group.get("order_number") or "-")
    requesting = _dealer_branch(group.get("requesting_dealer"), group.get("requesting_branch"))
    receiving = _dealer_branch(group.get("supplying_dealer"), group.get("supplying_branch"))
    status = str(group.get("status") or ("Completed" if kind == WORKFLOW_EMAIL_COMPLETED else "Requested"))
    completed_at = str(
        group.get("completed_at")
        or group.get("received_at")
        or group.get("updated_at")
        or ""
    ).strip() or "-"
    app_url = public_app_url()
    test_note = "THIS IS A TEST EMAIL. Ignore for operations.\n\n" if (subject_prefix or "").upper().startswith("[TEST]") else ""
    test_html = (
        '<p style="color:#9F1239;font-weight:700;">THIS IS A TEST EMAIL. Ignore for operations.</p>'
        if test_note else ""
    )
    if kind == WORKFLOW_EMAIL_COMPLETED:
        headline = "Stock request completed"
        intro = (
            "This Sleeping Stock request is completed. "
            "The receiving branch has confirmed receipt and the request workflow is finished."
        )
        extra_rows = (("Completed at", completed_at),)
        footer = "No further action is required on this request."
        attach_pdf = False
    else:
        headline = "New stock request received"
        intro = (
            "A new Sleeping Stock request has been sent to your dealer/branch. "
            "Please review it in Request Center. The detailed part list is in the attached PDF."
        )
        extra_rows = (("Review in Sleeping Stock", app_url),)
        footer = f"Open Request Center in Sleeping Stock to accept or reject this request: {app_url}"
        attach_pdf = True
    rows = [
        ("Request number", request_number),
        ("Order number", order_number),
        ("From (requesting)", requesting),
        ("To (receiving)", receiving),
        ("Items", totals["items"]),
        ("Quantity", totals["qty"]),
        ("Value", totals["value"]),
        ("Status", status),
        *extra_rows,
    ]
    text_lines = [
        "Dear Team,",
        "",
        test_note.rstrip(),
        intro,
        "",
        *[f"{label}: {value}" for label, value in rows],
        "",
        footer,
        "",
        "Regards,",
        "Sleeping Stock Team",
    ]
    text_body = "\n".join(line for line in text_lines if line is not None)
    html_rows = "".join(
        f'<tr><td style="padding:4px 16px 4px 0;font-weight:700;">{sanitize_text(str(label), 40)}</td>'
        f'<td style="padding:4px 0;">{sanitize_text(str(value), 160)}</td></tr>'
        for label, value in rows
    )
    html_body = f"""
    <div style="font-family:Arial,sans-serif;max-width:560px;margin:auto;border:1px solid #D1D5DB;border-radius:10px;overflow:hidden;">
      <div style="background:#047857;color:#fff;padding:16px;">
        <div style="font-size:16px;font-weight:800;">Sleeping Stock</div>
        <div style="font-size:13px;opacity:0.9;">{sanitize_text(headline, 80)}</div>
      </div>
      <div style="padding:16px;font-size:13px;line-height:1.5;color:#111827;">
        <p>Dear Team,</p>
        {test_html}
        <p>{sanitize_text(intro, 400)}</p>
        <table style="border-collapse:collapse;margin:10px 0;">{html_rows}</table>
        <p style="font-size:12px;color:#6B7280;">{sanitize_text(footer, 240)}</p>
        <p>Regards,<br/>Sleeping Stock Team</p>
      </div>
    </div>
    """
    return {
        "subject": subject,
        "text_body": text_body,
        "html_body": html_body,
        "attach_pdf": attach_pdf,
        "headline": headline,
    }


def send_request_pdf_email(to_email: str, group: dict, pdf_bytes: bytes, cc_email: str = "", subject_prefix: str = "") -> dict:
    """Sent-request email with PDF. Compatibility wrapper around the workflow sender."""
    return send_request_workflow_email(
        to_email, group, pdf_bytes=pdf_bytes, cc_email=cc_email,
        subject_prefix=subject_prefix, kind=WORKFLOW_EMAIL_SENT,
    )


def send_request_workflow_email(
    to_email: str,
    group: dict,
    pdf_bytes: bytes = None,
    cc_email: str = "",
    subject_prefix: str = "",
    kind: str = WORKFLOW_EMAIL_SENT,
) -> dict:
    """Send one of the two allowed request emails through Gmail API. Never raises."""
    to_email, cc_email = _email_test_redirect(to_email, cc_email)
    to_list = _email_list(to_email)
    to_keys = {email.lower() for email in to_list}
    cc_list = [email for email in _email_list(cc_email) if email.lower() not in to_keys]
    if not to_list:
        return {"status": "skipped", "error": "invalid_or_missing_email"}
    if not gmail_configured():
        return {"status": "skipped", "error": "gmail_not_configured"}

    content = build_request_workflow_content(group, kind=kind, subject_prefix=subject_prefix)
    try:
        attach = bool(content["attach_pdf"] and pdf_bytes)
        filename = group.get("pdf_filename") or f"{group.get('request_number') or 'request'}.pdf"
        msg = _build_rfc822_message(
            to_list=to_list,
            cc_list=cc_list,
            subject=content["subject"],
            text_body=content["text_body"],
            html_body=content["html_body"],
            pdf_bytes=pdf_bytes if attach else None,
            filename=filename,
        )
        return _send_gmail_mime(msg.as_bytes())
    except Exception as exc:  # noqa: BLE001
        logger.warning("Gmail request %s email failed: %s", kind, str(exc)[:300])
        return {"status": "failed", "error": str(exc)[:300]}
