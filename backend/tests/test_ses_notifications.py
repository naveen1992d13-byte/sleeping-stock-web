"""Focused tests for Amazon SES sending. No live SES calls."""
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

import notifications


GROUP = {
    "request_number": "RQHY2609080001",
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
