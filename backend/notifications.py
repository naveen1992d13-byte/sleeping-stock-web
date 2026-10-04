"""
Notification service for NMTS / Sleeping Stock — Amazon SES + WhatsApp Cloud API.

Design rules (see UPDATE_NOTES.txt for the full explanation):
- Email is sent with Amazon SES (SESv2) in ap-south-1 using the default AWS
  credential chain (EC2 instance role). No SMTP password or long-lived access
  keys are used.
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
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.application import MIMEApplication
from datetime import datetime, timezone

import boto3
from botocore.exceptions import BotoCoreError, ClientError
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


DEFAULT_SES_REGION = "ap-south-1"
DEFAULT_SES_FROM_EMAIL = "notification@sleepingstock.in"
DEFAULT_SES_FROM_NAME = "Sleeping Stock - NMTS"


def ses_settings() -> dict:
    """SES send config. Credentials come from the instance role, not env keys."""
    return {
        "region": _first_env("SES_REGION", "AWS_REGION", "AWS_DEFAULT_REGION", default=DEFAULT_SES_REGION),
        "from_email": _first_env("SES_FROM_EMAIL", default=DEFAULT_SES_FROM_EMAIL).strip(),
        "from_name": _first_env("SES_FROM_NAME", "GMAIL_SENDER_NAME", "SMTP_FROM_NAME", default=DEFAULT_SES_FROM_NAME),
        "configuration_set": _env("SES_CONFIGURATION_SET").strip(),
    }


def ses_configured() -> bool:
    settings = ses_settings()
    return bool(settings["from_email"] and settings["region"] and is_valid_email(settings["from_email"]))


def gmail_settings() -> dict:
    """Deprecated SMTP settings. Sending uses SES; kept for env compatibility."""
    ses = ses_settings()
    return {
        "host": _first_env("GMAIL_SMTP_HOST", "SMTP_HOST", default="smtp.gmail.com"),
        "port": int(_first_env("GMAIL_SMTP_PORT", "SMTP_PORT", default="587") or "587"),
        "username": ses["from_email"],
        "password": "",
        "sender_name": ses["from_name"],
    }


def gmail_configured() -> bool:
    return ses_configured()


def _ses_from_header(settings=None) -> str:
    settings = settings or ses_settings()
    name = sanitize_text(settings.get("from_name") or DEFAULT_SES_FROM_NAME, 80)
    return f"{name} <{settings['from_email']}>"


def _ses_client(region: str):
    return boto3.client("sesv2", region_name=region)


def _ses_send_kwargs(settings: dict) -> dict:
    extra = {}
    if settings.get("configuration_set"):
        extra["ConfigurationSetName"] = settings["configuration_set"]
    return extra


def _send_ses_simple(to_list, cc_list, subject: str, text_body: str, html_body: str) -> dict:
    settings = ses_settings()
    if not ses_configured():
        return {"status": "skipped", "error": "ses_not_configured"}
    try:
        response = _ses_client(settings["region"]).send_email(
            FromEmailAddress=_ses_from_header(settings),
            Destination={"ToAddresses": list(to_list), "CcAddresses": list(cc_list or [])},
            Content={
                "Simple": {
                    "Subject": {"Data": sanitize_text(subject, 200), "Charset": "UTF-8"},
                    "Body": {
                        "Text": {"Data": text_body or "", "Charset": "UTF-8"},
                        "Html": {"Data": html_body or "", "Charset": "UTF-8"},
                    },
                }
            },
            **_ses_send_kwargs(settings),
        )
        return {"status": "sent", "provider_response": response.get("MessageId") or "ses_ok"}
    except (BotoCoreError, ClientError, Exception) as exc:  # noqa: BLE001
        logger.warning("SES send failed: %s", str(exc)[:300])
        return {"status": "failed", "error": str(exc)[:300]}


def _send_ses_raw(to_list, cc_list, raw_message: bytes) -> dict:
    settings = ses_settings()
    if not ses_configured():
        return {"status": "skipped", "error": "ses_not_configured"}
    try:
        response = _ses_client(settings["region"]).send_email(
            FromEmailAddress=_ses_from_header(settings),
            Destination={"ToAddresses": list(to_list), "CcAddresses": list(cc_list or [])},
            Content={"Raw": {"Data": raw_message}},
            **_ses_send_kwargs(settings),
        )
        return {"status": "sent", "provider_response": response.get("MessageId") or "ses_ok"}
    except (BotoCoreError, ClientError, Exception) as exc:  # noqa: BLE001
        logger.warning("SES raw send failed: %s", str(exc)[:300])
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
# Amazon SES (transactional)
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
    """Compatibility wrapper. Sends through Amazon SES. Never raises."""
    return send_notification_email(to_email, subject, context, cc_email=cc_email)


def send_notification_email(to_email: str, subject: str, context: dict, cc_email: str = "") -> dict:
    """Returns a result dict; never raises."""
    to_email, cc_email = _email_test_redirect((to_email or "").strip(), (cc_email or "").strip())
    to_list = _email_list(to_email)
    to_keys = {email.lower() for email in to_list}
    cc_list = [email for email in _email_list(cc_email) if email.lower() not in to_keys]
    if not to_list:
        return {"status": "skipped", "error": "invalid_or_missing_email"}
    if not ses_configured():
        return {"status": "skipped", "error": "ses_not_configured"}
    return _send_ses_simple(to_list, cc_list, subject, _build_email_text(context), _build_email_html(context))


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
def build_request_email_subject(group: dict, subject_prefix: str = "") -> str:
    """Finalized request-email subject line:
        '<prefix>Sleeping Stock Request - <Supplying Dealer> <Supplying Branch> - <Ref No>'
    (using en dashes). The supplying dealer/branch is the recipient (TO). Kept
    here so the email body and the notification_logs audit record stay in sync."""
    prefix = (subject_prefix or "").strip()
    if prefix and not prefix.endswith(" "):
        prefix = prefix + " "
    request_number = str(group.get("request_number", "-") or "-").strip() or "-"
    supplying_dealer = str(group.get("supplying_dealer") or "").strip()
    supplying_branch = str(group.get("supplying_branch") or "").strip()
    dealer_branch = " ".join(p for p in (supplying_dealer, supplying_branch) if p) or "-"
    return f"{prefix}Sleeping Stock Request \u2013 {dealer_branch} \u2013 {request_number}"


def send_request_pdf_email(to_email: str, group: dict, pdf_bytes: bytes, cc_email: str = "", subject_prefix: str = "") -> dict:
    """Sends the Parts Transfer Request PDF through Amazon SES.
    Returns a result dict; never raises — a delivery failure must never
    roll back the already-saved request."""
    to_email, cc_email = _email_test_redirect(to_email, cc_email)
    to_list = _email_list(to_email)
    to_keys = {email.lower() for email in to_list}
    cc_list = [email for email in _email_list(cc_email) if email.lower() not in to_keys]
    if not to_list:
        return {"status": "skipped", "error": "invalid_or_missing_email"}

    settings = ses_settings()
    if not ses_configured():
        return {"status": "skipped", "error": "ses_not_configured"}

    request_number = group.get("request_number", "-")
    prefix = (subject_prefix or "").strip()
    if prefix and not prefix.endswith(" "):
        prefix = prefix + " "
    subject = build_request_email_subject(group, subject_prefix)
    filename = group.get("pdf_filename") or f"{request_number}.pdf"
    test_note = "THIS IS A TEST EMAIL. Ignore for operations.\n\n" if prefix.upper().startswith("[TEST]") else ""
    test_html = (
        '<p style="color:#9F1239;font-weight:700;">THIS IS A TEST EMAIL. Ignore for operations.</p>'
        if test_note else ""
    )

    # Summary-only body. Detailed parts (Part Number / Part Name) live ONLY in the
    # attached Stock Transfer PDF — the email body must never list them.
    total_items = group.get("total_items")
    if total_items in (None, ""):
        total_items = len(group.get("items") or [])
    total_qty = _pdf_format_number(group.get("total_qty"))
    total_value = _pdf_format_number(group.get("total_value"))
    request_message = (
        "Please review and action the following Sleeping Stock request. "
        "The detailed part list is in the attached Parts Transfer Request PDF."
    )

    text_body = (
        "Dear Team,\n\n"
        f"{test_note}"
        f"{request_message}\n\n"
        f"Items: {total_items}\n"
        f"Quantity: {total_qty}\n"
        f"Value: {total_value}\n\n"
        "Regards,\n"
        "Sleeping Stock Team"
    )
    html_body = f"""
    <div style="font-family:Arial,sans-serif;max-width:560px;margin:auto;border:1px solid #D1D5DB;border-radius:10px;overflow:hidden;">
      <div style="background:#047857;color:#fff;padding:16px;">
        <div style="font-size:16px;font-weight:800;">Sleeping Stock · NMTS</div>
        <div style="font-size:13px;opacity:0.9;">{sanitize_text(subject, 120)}</div>
      </div>
      <div style="padding:16px;font-size:13px;line-height:1.5;color:#111827;">
        <p>Dear Team,</p>
        {test_html}
        <p>{request_message}</p>
        <table style="border-collapse:collapse;margin:10px 0;">
          <tr><td style="padding:4px 16px 4px 0;font-weight:700;">Items</td><td style="padding:4px 0;">{total_items}</td></tr>
          <tr><td style="padding:4px 16px 4px 0;font-weight:700;">Quantity</td><td style="padding:4px 0;">{total_qty}</td></tr>
          <tr><td style="padding:4px 16px 4px 0;font-weight:700;">Value</td><td style="padding:4px 0;">{total_value}</td></tr>
        </table>
        <p style="font-size:12px;color:#6B7280;">Detailed parts are in the attached Parts Transfer Request PDF.</p>
        <p>Regards,<br/>Sleeping Stock Team</p>
      </div>
    </div>
    """

    try:
        msg = MIMEMultipart("mixed")
        msg["Subject"] = sanitize_text(subject, 200)
        msg["From"] = _ses_from_header(settings)
        msg["To"] = ", ".join(to_list)
        if cc_list:
            msg["Cc"] = ", ".join(cc_list)

        alt = MIMEMultipart("alternative")
        alt.attach(MIMEText(text_body, "plain"))
        alt.attach(MIMEText(html_body, "html"))
        msg.attach(alt)

        attachment = MIMEApplication(pdf_bytes or b"", _subtype="pdf")
        attachment.add_header("Content-Disposition", "attachment", filename=filename)
        msg.attach(attachment)
        return _send_ses_raw(to_list, cc_list, msg.as_bytes())
    except Exception as exc:  # noqa: BLE001 — a delivery failure must never propagate
        logger.warning("SES Parts Transfer Request send failed: %s", str(exc)[:300])
        return {"status": "failed", "error": str(exc)[:300]}
