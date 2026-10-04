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
    assert notifications.workflow_email_kind_for_event(header_status="Received") == "completed"
    assert notifications.workflow_email_kind_for_event(header_status="Completed") == "completed"
    for status in (
        "Requested", "Approved", "Partially Approved", "Rejected", "Cancelled",
        "Dispatched", "Picking", "picking_finished", "In Transit",
        "Receive Pending", "Snooze",
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


def test_completed_email_summary_fields_no_pdf():
    group = dict(
        GROUP, status="Completed", completed_at="2026-10-04T08:15:00+00:00",
        accepted_total_qty=5,
    )
    content = notifications.build_request_workflow_content(group, kind=notifications.WORKFLOW_EMAIL_COMPLETED)
    body = content["text_body"]
    assert content["attach_pdf"] is False
    assert "Stock request completed" in content["subject"]
    assert "workflow is finished" in body
    assert "2026-10-04T08:15:00+00:00" in body
    assert "No further action is required" in body
    assert "RQHY2610030001" in body
    assert "Koyambedu" in body
    assert "Vanagaram" in body


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


def test_routing_preserved_for_both_emails():
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
