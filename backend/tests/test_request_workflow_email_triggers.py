"""Focused tests for the two request SES emails and durable duplicate prevention."""
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

import notifications


GROUP = {
    "id": "H-RQ-1",
    "request_number": "RQHY2610030001",
    "order_number": "ORHY2610030001",
    "supplying_dealer": "FPL Automobiles PVT LTD",
    "supplying_branch": "Koyambedu",
    "requesting_dealer": "FPL Automobiles PVT LTD",
    "requesting_branch": "Vanagaram",
    "total_items": 2,
    "total_qty": 5,
    "total_value": 2500,
    "status": "Requested",
    "pdf_filename": "RQHY2610030001.pdf",
}


def _matches_claim(doc: dict, query: dict) -> bool:
    if doc.get("id") != query.get("id"):
        return False
    for key, cond in query.items():
        if key == "id":
            continue
        if isinstance(cond, dict):
            if "$ne" in cond and doc.get(key) == cond["$ne"]:
                return False
            if "$exists" in cond:
                exists = key in doc
                if cond["$exists"] is False and exists:
                    return False
                if cond["$exists"] is True and not exists:
                    return False
        elif doc.get(key) != cond:
            return False
    return True


class _FakeHeaders:
    def __init__(self, doc):
        self.doc = dict(doc)
        self.updates = []

    def claim(self, result=False, force=False):
        query = notifications.request_email_claim_filter(self.doc["id"], result=result)
        if force:
            sent_field = "result_email_sent" if result else "email_sent"
            query = {"id": self.doc["id"], sent_field: {"$ne": True}}
        if not _matches_claim(self.doc, query):
            return None
        claimed_field = "result_email_claimed_at" if result else "email_claimed_at"
        self.doc[claimed_field] = "2026-10-04T09:00:00+00:00"
        self.updates.append(claimed_field)
        return dict(self.doc)

    def mark_sent(self, result=False):
        sent_field = "result_email_sent" if result else "email_sent"
        self.doc[sent_field] = True


def test_only_two_workflow_email_kinds():
    assert notifications.workflow_email_kind_for_event(created=True) == "sent"
    assert notifications.workflow_email_kind_for_event(header_status="Received") == "received"
    assert notifications.workflow_email_kind_for_event(header_status="Completed") == ""
    for status in (
        "Requested", "Approved", "Partially Approved", "Rejected", "Cancelled",
        "Dispatched", "Picking", "picking_finished", "In Transit",
        "Receive Pending", "Snooze", "Completed",
    ):
        assert notifications.workflow_email_kind_for_event(header_status=status) == ""


def test_sent_email_summary_fields_and_review_link():
    content = notifications.build_request_workflow_content(GROUP, kind=notifications.WORKFLOW_EMAIL_SENT)
    body = content["text_body"]
    html = content["html_body"]
    assert content["attach_pdf"] is True
    assert "New stock request received" in content["subject"]
    for fragment in (
        "RQHY2610030001", "ORHY2610030001", "Vanagaram", "Koyambedu",
        "Items: 2", "Quantity: 5", "Value: 2,500", "Status: Requested",
        "https://sleepingstock.in",
    ):
        assert fragment in body
    assert "<table" in html
    assert "traceback" not in html.lower()
    assert "DEBUG" not in html


def test_receive_confirmed_email_summary_fields_with_pdf():
    group = dict(
        GROUP, status="Received", received_at="2026-10-04T08:15:00+00:00",
        received_user_name="Priya Receiver", accepted_total_qty=5,
    )
    content = notifications.build_request_workflow_content(group, kind=notifications.WORKFLOW_EMAIL_RECEIVED)
    body = content["text_body"]
    assert content["attach_pdf"] is True
    assert "Receive confirmed" in content["subject"]
    assert "Priya Receiver" in body
    assert "2026-10-04T08:15:00+00:00" in body
    assert "No further action is required" in body
    assert "RQHY2610030001" in body
    assert "ORHY2610030001" in body
    assert "Sending branch/dealer: FPL Automobiles PVT LTD Koyambedu" in body
    assert "Receiving branch/dealer: FPL Automobiles PVT LTD Vanagaram" in body
    assert "Final received status: Received" in body
    assert "Total items: 2" in body
    assert "Total quantity: 5" in body
    assert "Total value: 2,500" in body


def test_claim_prevents_duplicate_sent_and_completed_emails():
    headers = _FakeHeaders(GROUP)
    first = headers.claim(result=False)
    second = headers.claim(result=False)
    assert first is not None
    assert second is None
    headers.mark_sent(result=False)
    assert headers.claim(result=False) is None

    headers.doc["status"] = "Completed"
    done_first = headers.claim(result=True)
    done_second = headers.claim(result=True)
    assert done_first is not None
    assert done_second is None
    headers.mark_sent(result=True)
    assert headers.claim(result=True) is None


def test_force_resend_only_when_sent_flag_is_not_true():
    headers = _FakeHeaders(dict(GROUP, email_claimed_at="2026-10-04T07:00:00+00:00", email_sent=False))
    assert headers.claim(result=False) is None
    forced = headers.claim(result=False, force=True)
    assert forced is not None
    headers.mark_sent(result=False)
    assert headers.claim(result=False, force=True) is None


def test_routing_preserved_for_sent_and_swapped_for_receive():
    users = [
        {"email": "koyambedu.user@gmail.com", "role": "user", "group": "FPL Automobiles PVT LTD",
         "location": "Koyambedu", "status": "active"},
        {"email": "vanagaram.user@gmail.com", "role": "user", "group": "FPL Automobiles PVT LTD",
         "location": "Vanagaram", "status": "active"},
        {"email": "dealer.admin@gmail.com", "role": "admin", "group": "FPL Automobiles PVT LTD",
         "location": "", "status": "active"},
        {"email": "admin@sleepingstock.in", "role": "master", "group": "", "location": "", "status": "active"},
    ]
    to_emails, cc_emails = notifications.resolve_request_email_routing(users, GROUP)
    assert to_emails == ["koyambedu.user@gmail.com"]
    assert "vanagaram.user@gmail.com" in cc_emails
    assert "dealer.admin@gmail.com" in cc_emails
    assert "admin@sleepingstock.in" not in to_emails + cc_emails

    recv_to, recv_cc = notifications.resolve_receive_confirmed_email_routing(users, GROUP)
    assert recv_to == ["vanagaram.user@gmail.com"]
    assert "koyambedu.user@gmail.com" in recv_cc
    assert "dealer.admin@gmail.com" in recv_cc
    assert "admin@sleepingstock.in" not in recv_to + recv_cc
