"""Focused tests for Gmail API sending. No live Gmail calls."""
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

import notifications


GROUP = {
    "request_number": "RQHY2609080001",
    "order_number": "ORHY2609080001",
    "supplying_dealer": "FPL Automobiles PVT LTD",
    "supplying_branch": "Koyambedu",
    "requesting_dealer": "FPL Automobiles PVT LTD",
    "requesting_branch": "Vanagaram",
    "total_items": 2,
    "total_qty": 3,
    "total_value": 1500,
    "pdf_filename": "RQHY2609080001.pdf",
}


class _FakeGmail:
    def __init__(self):
        self.raw_messages = []

    def __call__(self, raw_message: bytes):
        self.raw_messages.append(raw_message)
        return {"status": "sent", "provider_response": "gmail-message-1"}


def _enable_gmail(monkeypatch):
    monkeypatch.setenv("GMAIL_OAUTH_CLIENT_ID", "test-client-id")
    monkeypatch.setenv("GMAIL_OAUTH_CLIENT_SECRET", "test-client-secret")
    monkeypatch.setenv("GMAIL_OAUTH_REFRESH_TOKEN", "test-refresh-token")
    monkeypatch.delenv("GMAIL_FROM_EMAIL", raising=False)
    monkeypatch.delenv("GMAIL_FROM_NAME", raising=False)
    monkeypatch.delenv("GMAIL_OAUTH_USER", raising=False)
    monkeypatch.delenv("EMAIL_TEST_MODE", raising=False)
    monkeypatch.delenv("EMAIL_TEST_RECIPIENT", raising=False)


def test_gmail_defaults_to_plural_notifications_sender(monkeypatch):
    _enable_gmail(monkeypatch)
    settings = notifications.gmail_settings()
    assert settings["from_email"] == "notifications@sleepingstock.in"
    assert settings["from_name"] == "Sleeping Stock"
    assert settings["oauth_user"] == "naveen@sleepingstock.in"
    assert settings["oauth_user"] != settings["from_email"]
    assert notifications._gmail_from_header(settings) == "Sleeping Stock <notifications@sleepingstock.in>"
    assert not hasattr(notifications, "smtplib")
    assert not hasattr(notifications, "_ses_client")
    assert not hasattr(notifications, "_send_ses_raw")


def test_oauth_mailbox_is_naveen_send_as_is_notifications_alias(monkeypatch):
    """OAuth identity is the real mailbox; From is the Send-as alias only."""
    _enable_gmail(monkeypatch)
    fake = _FakeGmail()
    monkeypatch.setattr(notifications, "_send_gmail_mime", fake)
    result = notifications.send_request_pdf_email(
        "koyambedu.user@example.com",
        GROUP,
        b"%PDF-1.4 test",
        cc_email="vanagaram.user@example.com",
    )
    assert result["status"] == "sent"
    raw = fake.raw_messages[0]
    assert b"From: Sleeping Stock <notifications@sleepingstock.in>" in raw
    assert b"naveen@sleepingstock.in" not in raw
    assert notifications.gmail_settings()["oauth_user"] == "naveen@sleepingstock.in"


def test_simple_notification_uses_gmail_mime_not_smtp(monkeypatch):
    _enable_gmail(monkeypatch)
    fake = _FakeGmail()
    monkeypatch.setattr(notifications, "_send_gmail_mime", fake)
    result = notifications.send_notification_email(
        "dealer.user@example.com",
        "NMTS — Request Accepted",
        {"headline": "Request Accepted", "fields": [("Status", "Approved")]},
        cc_email="dealer.admin@example.com",
    )
    assert result["status"] == "sent"
    assert result["provider_response"] == "gmail-message-1"
    raw = fake.raw_messages[0]
    assert b"From: Sleeping Stock <notifications@sleepingstock.in>" in raw
    assert b"To: dealer.user@example.com" in raw
    assert b"Cc: dealer.admin@example.com" in raw
    assert b"application/pdf" not in raw


def test_pdf_request_email_attaches_pdf(monkeypatch):
    _enable_gmail(monkeypatch)
    fake = _FakeGmail()
    monkeypatch.setattr(notifications, "_send_gmail_mime", fake)
    result = notifications.send_request_pdf_email(
        "koyambedu.user@example.com",
        GROUP,
        b"%PDF-1.4 test",
        cc_email="vanagaram.user@example.com",
    )
    assert result["status"] == "sent"
    raw = fake.raw_messages[0]
    assert b"From: Sleeping Stock <notifications@sleepingstock.in>" in raw
    assert b"To: koyambedu.user@example.com" in raw
    assert b"Cc: vanagaram.user@example.com" in raw
    assert b"application/pdf" in raw
    assert b"RQHY2609080001.pdf" in raw
    assert b"JVBERi0xLjQgdGVzdA==" in raw


def test_email_test_mode_redirects_and_drops_cc(monkeypatch):
    _enable_gmail(monkeypatch)
    fake = _FakeGmail()
    monkeypatch.setattr(notifications, "_send_gmail_mime", fake)
    monkeypatch.setenv("EMAIL_TEST_MODE", "true")
    monkeypatch.setenv("EMAIL_TEST_RECIPIENT", "verified.sandbox@example.com")
    result = notifications.send_request_pdf_email(
        "koyambedu.user@example.com",
        GROUP,
        b"%PDF-1.4 test",
        cc_email="vanagaram.user@example.com",
    )
    assert result["status"] == "sent"
    raw = fake.raw_messages[0]
    assert b"To: verified.sandbox@example.com" in raw
    assert b"Cc:" not in raw
    assert b"koyambedu.user@example.com" not in raw


def test_invalid_recipient_skips_without_gmail_call(monkeypatch):
    _enable_gmail(monkeypatch)
    fake = _FakeGmail()
    monkeypatch.setattr(notifications, "_send_gmail_mime", fake)
    result = notifications.send_request_pdf_email("invalid", GROUP, b"%PDF")
    assert result["status"] == "skipped"
    assert fake.raw_messages == []


def test_missing_oauth_skips_without_raising(monkeypatch):
    monkeypatch.delenv("GMAIL_OAUTH_CLIENT_ID", raising=False)
    monkeypatch.delenv("GMAIL_OAUTH_CLIENT_SECRET", raising=False)
    monkeypatch.delenv("GMAIL_OAUTH_REFRESH_TOKEN", raising=False)
    result = notifications.send_notification_email(
        "dealer.user@example.com",
        "NMTS — Request",
        {"headline": "Request", "fields": []},
    )
    assert result["status"] == "skipped"
    assert result["error"] == "gmail_not_configured"


def test_gmail_api_error_does_not_raise(monkeypatch):
    _enable_gmail(monkeypatch)

    class Boom:
        def users(self):
            raise RuntimeError("invalid_grant")

    monkeypatch.setattr(notifications, "_gmail_service", lambda: Boom())
    result = notifications.send_notification_email(
        "dealer.user@example.com",
        "NMTS — Request",
        {"headline": "Request", "fields": []},
    )
    assert result["status"] == "failed"
    assert "invalid_grant" in result["error"]


def test_sent_email_includes_summary_and_pdf():
    content = notifications.build_request_workflow_content(GROUP, kind=notifications.WORKFLOW_EMAIL_SENT)
    assert content["attach_pdf"] is True
    assert "New stock request received" in content["subject"]
    assert "RQHY2609080001" in content["text_body"]
    assert "ORHY2609080001" in content["text_body"]
    assert "Vanagaram" in content["text_body"]
    assert "Koyambedu" in content["text_body"]
    assert "https://sleepingstock.in" in content["text_body"]
    assert "Requested" in content["html_body"] or "Status" in content["html_body"]


def test_receive_confirmed_email_attaches_receipt_and_shows_confirmer():
    group = dict(
        GROUP,
        status="Received",
        received_at="2026-10-04T08:00:00+00:00",
        received_user_name="Naveen Receiver",
        accepted_total_qty=3,
        pdf_filename="RQHY2609080001-receipt.pdf",
    )
    content = notifications.build_request_workflow_content(group, kind=notifications.WORKFLOW_EMAIL_RECEIVED)
    assert content["attach_pdf"] is True
    assert "Receive confirmed" in content["subject"]
    assert "Naveen Receiver" in content["text_body"]
    assert "2026-10-04T08:00:00+00:00" in content["text_body"]
    assert "Sending branch/dealer: FPL Automobiles PVT LTD Koyambedu" in content["text_body"]
    assert "Receiving branch/dealer: FPL Automobiles PVT LTD Vanagaram" in content["text_body"]
    assert "Final received status: Received" in content["text_body"]
    assert "Total items" in content["text_body"]
    assert "Stock request completed" not in content["subject"]


def test_receive_confirmed_send_attaches_pdf(monkeypatch):
    _enable_gmail(monkeypatch)
    fake = _FakeGmail()
    monkeypatch.setattr(notifications, "_send_gmail_mime", fake)
    result = notifications.send_request_workflow_email(
        "vanagaram.user@example.com",
        dict(GROUP, status="Received", pdf_filename="RQHY2609080001-receipt.pdf"),
        pdf_bytes=b"%PDF-1.4 receipt",
        cc_email="koyambedu.user@example.com",
        kind=notifications.WORKFLOW_EMAIL_RECEIVED,
    )
    assert result["status"] == "sent"
    raw = fake.raw_messages[0]
    assert b"application/pdf" in raw
    assert b"RQHY2609080001-receipt.pdf" in raw
    assert b"From: Sleeping Stock <notifications@sleepingstock.in>" in raw
    assert b"To: vanagaram.user@example.com" in raw
    assert b"Cc: koyambedu.user@example.com" in raw


def test_completion_email_only_for_received_not_completed():
    assert notifications.should_send_request_completion_email("Received") is True
    assert notifications.should_send_request_completion_email("Completed") is False
    for status in (
        "Requested", "Approved", "Partially Approved", "Rejected", "Cancelled",
        "Dispatched", "Picking", "picking_finished", "In Transit", "Receive Pending",
        "Snooze", "Completed",
    ):
        assert notifications.should_send_request_completion_email(status) is False
    assert notifications.workflow_email_kind_for_event(created=True) == notifications.WORKFLOW_EMAIL_SENT
    assert notifications.workflow_email_kind_for_event(header_status="Received") == notifications.WORKFLOW_EMAIL_RECEIVED
    assert notifications.workflow_email_kind_for_event(header_status="Completed") == ""
    assert notifications.workflow_email_kind_for_event(header_status="Dispatched") == ""


def test_durable_claim_filter_is_one_shot():
    sent = notifications.request_email_claim_filter("H1", result=False)
    assert sent["id"] == "H1"
    assert sent["email_sent"] == {"$ne": True}
    assert sent["email_claimed_at"] == {"$exists": False}
    done = notifications.request_email_claim_filter("H1", result=True)
    assert done["result_email_sent"] == {"$ne": True}
    assert done["result_email_claimed_at"] == {"$exists": False}


def test_routing_still_targets_receiving_branch_user():
    users = [
        {"email": "koyambedu.user@gmail.com", "role": "user", "group": "FPL Automobiles PVT LTD", "location": "Koyambedu", "status": "active"},
        {"email": "vanagaram.user@gmail.com", "role": "user", "group": "FPL Automobiles PVT LTD", "location": "Vanagaram", "status": "active"},
        {"email": "admin@sleepingstock.in", "role": "master", "group": "", "location": "", "status": "active"},
    ]
    to_emails, cc_emails = notifications.resolve_request_email_routing(users, GROUP)
    assert to_emails == ["koyambedu.user@gmail.com"]
    assert "vanagaram.user@gmail.com" in cc_emails
    assert "admin@sleepingstock.in" not in to_emails
    assert "admin@sleepingstock.in" not in cc_emails


def test_email_test_mode_defaults_true_in_testing(monkeypatch):
    monkeypatch.setenv("APP_ENV", "testing")
    monkeypatch.delenv("EMAIL_TEST_MODE", raising=False)
    assert notifications.email_test_mode() is True
    monkeypatch.setenv("EMAIL_TEST_MODE", "false")
    assert notifications.email_test_mode() is False


def test_email_test_mode_defaults_false_in_production(monkeypatch):
    monkeypatch.delenv("APP_ENV", raising=False)
    monkeypatch.delenv("EMAIL_TEST_MODE", raising=False)
    assert notifications.email_test_mode() is False


def test_receive_confirmed_routing_swaps_to_receiving_branch():
    users = [
        {"email": "koyambedu.user@gmail.com", "role": "user", "group": "FPL Automobiles PVT LTD", "location": "Koyambedu", "status": "active"},
        {"email": "vanagaram.user@gmail.com", "role": "user", "group": "FPL Automobiles PVT LTD", "location": "Vanagaram", "status": "active"},
        {"email": "dealer.admin@gmail.com", "role": "admin", "group": "FPL Automobiles PVT LTD", "location": "", "status": "active"},
        {"email": "admin@sleepingstock.in", "role": "master", "group": "", "location": "", "status": "active"},
    ]
    to_emails, cc_emails = notifications.resolve_receive_confirmed_email_routing(users, GROUP)
    assert to_emails == ["vanagaram.user@gmail.com"]
    assert "koyambedu.user@gmail.com" in cc_emails
    assert "dealer.admin@gmail.com" in cc_emails
    assert "admin@sleepingstock.in" not in to_emails
    assert "admin@sleepingstock.in" not in cc_emails
