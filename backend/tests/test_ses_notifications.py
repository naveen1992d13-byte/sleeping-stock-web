"""Focused tests for Amazon SES sending. No live SES calls."""
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


class _FakeSES:
    def __init__(self):
        self.calls = []

    def send_email(self, **kwargs):
        self.calls.append(kwargs)
        return {"MessageId": "ses-message-1"}


def test_ses_settings_default_to_mumbai_notification_address(monkeypatch):
    monkeypatch.delenv("SES_REGION", raising=False)
    monkeypatch.delenv("SES_FROM_EMAIL", raising=False)
    monkeypatch.delenv("AWS_REGION", raising=False)
    monkeypatch.delenv("AWS_DEFAULT_REGION", raising=False)
    settings = notifications.ses_settings()
    assert settings["region"] == "ap-south-1"
    assert settings["from_email"] == "notification@sleepingstock.in"


def test_simple_notification_uses_ses_not_smtp(monkeypatch):
    fake = _FakeSES()
    monkeypatch.setattr(notifications, "_ses_client", lambda region: fake)
    assert not hasattr(notifications, "smtplib")
    result = notifications.send_notification_email(
        "dealer.user@example.com",
        "NMTS — Request Accepted",
        {"headline": "Request Accepted", "fields": [("Status", "Approved")]},
        cc_email="dealer.admin@example.com",
    )
    assert result["status"] == "sent"
    assert result["provider_response"] == "ses-message-1"
    assert len(fake.calls) == 1
    call = fake.calls[0]
    assert call["FromEmailAddress"].endswith("<notification@sleepingstock.in>")
    assert call["Destination"]["ToAddresses"] == ["dealer.user@example.com"]
    assert call["Destination"]["CcAddresses"] == ["dealer.admin@example.com"]
    assert "Simple" in call["Content"]


def test_pdf_request_email_sends_raw_attachment(monkeypatch):
    fake = _FakeSES()
    monkeypatch.setattr(notifications, "_ses_client", lambda region: fake)
    result = notifications.send_request_pdf_email(
        "koyambedu.user@example.com",
        GROUP,
        b"%PDF-1.4 test",
        cc_email="vanagaram.user@example.com",
    )
    assert result["status"] == "sent"
    call = fake.calls[0]
    assert call["FromEmailAddress"].endswith("<notification@sleepingstock.in>")
    raw = call["Content"]["Raw"]["Data"]
    assert b"application/pdf" in raw
    assert b"RQHY2609080001.pdf" in raw
    assert b"JVBERi0xLjQgdGVzdA==" in raw  # base64 of %PDF-1.4 test
    assert call["Destination"]["ToAddresses"] == ["koyambedu.user@example.com"]


def test_email_test_mode_redirects_and_drops_cc(monkeypatch):
    fake = _FakeSES()
    monkeypatch.setattr(notifications, "_ses_client", lambda region: fake)
    monkeypatch.setenv("EMAIL_TEST_MODE", "true")
    monkeypatch.setenv("EMAIL_TEST_RECIPIENT", "verified.sandbox@example.com")
    result = notifications.send_request_pdf_email(
        "koyambedu.user@example.com",
        GROUP,
        b"%PDF-1.4 test",
        cc_email="vanagaram.user@example.com",
    )
    assert result["status"] == "sent"
    call = fake.calls[0]
    assert call["Destination"]["ToAddresses"] == ["verified.sandbox@example.com"]
    assert call["Destination"]["CcAddresses"] == []


def test_invalid_recipient_skips_without_ses_call(monkeypatch):
    fake = _FakeSES()
    monkeypatch.setattr(notifications, "_ses_client", lambda region: fake)
    result = notifications.send_request_pdf_email("invalid", GROUP, b"%PDF")
    assert result["status"] == "skipped"
    assert fake.calls == []


def test_raw_access_denied_falls_back_to_simple_without_raising(monkeypatch):
    class RawDenied:
        def __init__(self):
            self.calls = []

        def send_email(self, **kwargs):
            self.calls.append(kwargs)
            if "Raw" in (kwargs.get("Content") or {}):
                raise RuntimeError("An error occurred (AccessDeniedException) SendRawEmail")
            return {"MessageId": "simple-fallback"}

    fake = RawDenied()
    monkeypatch.setattr(notifications, "_ses_client", lambda region: fake)
    result = notifications.send_request_workflow_email(
        "koyambedu.user@example.com", GROUP, pdf_bytes=b"%PDF-1.4 test",
        kind=notifications.WORKFLOW_EMAIL_SENT,
    )
    assert result["status"] == "sent"
    assert result["provider_response"] == "simple-fallback"
    assert any("Raw" in (call.get("Content") or {}) for call in fake.calls)
    assert any("Simple" in (call.get("Content") or {}) for call in fake.calls)


def test_ses_client_error_does_not_raise(monkeypatch):
    class Boom:
        def send_email(self, **kwargs):
            raise RuntimeError("sandbox recipient not verified")

    monkeypatch.setattr(notifications, "_ses_client", lambda region: Boom())
    result = notifications.send_notification_email(
        "dealer.user@example.com",
        "NMTS — Request",
        {"headline": "Request", "fields": []},
    )
    assert result["status"] == "failed"
    assert "sandbox" in result["error"]


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


def test_completed_email_has_no_pdf_and_confirms_finish():
    group = dict(GROUP, status="Completed", completed_at="2026-10-04T08:00:00+00:00", accepted_total_qty=3)
    content = notifications.build_request_workflow_content(group, kind=notifications.WORKFLOW_EMAIL_COMPLETED)
    assert content["attach_pdf"] is False
    assert "Stock request completed" in content["subject"]
    assert "workflow is finished" in content["text_body"]
    assert "2026-10-04T08:00:00+00:00" in content["text_body"]
    assert "Completed" in content["text_body"]


def test_completed_send_does_not_attach_pdf(monkeypatch):
    fake = _FakeSES()
    monkeypatch.setattr(notifications, "_ses_client", lambda region: fake)
    result = notifications.send_request_workflow_email(
        "koyambedu.user@example.com",
        dict(GROUP, status="Completed"),
        pdf_bytes=b"%PDF-should-not-attach",
        kind=notifications.WORKFLOW_EMAIL_COMPLETED,
    )
    assert result["status"] == "sent"
    content = fake.calls[0]["Content"]
    assert "Simple" in content
    assert "Raw" not in content
    simple = content["Simple"]
    assert b"%PDF-should-not-attach" not in str(simple).encode()
    assert "application/pdf" not in str(simple)


def test_completion_email_only_for_finished_statuses():
    assert notifications.should_send_request_completion_email("Completed") is True
    assert notifications.should_send_request_completion_email("Received") is True
    for status in (
        "Requested", "Approved", "Partially Approved", "Rejected", "Cancelled",
        "Dispatched", "Picking", "picking_finished", "In Transit", "Receive Pending",
        "Snooze",
    ):
        assert notifications.should_send_request_completion_email(status) is False
    assert notifications.workflow_email_kind_for_event(created=True) == notifications.WORKFLOW_EMAIL_SENT
    assert notifications.workflow_email_kind_for_event(header_status="Completed") == notifications.WORKFLOW_EMAIL_COMPLETED
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
