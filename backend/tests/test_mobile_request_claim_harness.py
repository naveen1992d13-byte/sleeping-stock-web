"""Harness tests for mobile Pick / Reject / 50s confirm claim flow (no real Mongo)."""
import asyncio
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi import HTTPException
from pymongo.errors import DuplicateKeyError

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

import mobile_api
import mobile_push
import mobile_request_claim as claim


# ---------------------------------------------------------------------------
# Lightweight fakes
# ---------------------------------------------------------------------------

def _matches_ci(field_query, value):
    if not isinstance(field_query, dict):
        return (value or "") == (field_query or "")
    pattern = field_query.get("$regex")
    if pattern is None:
        return True
    flags = re.IGNORECASE if "i" in (field_query.get("$options") or "") else 0
    return bool(re.search(pattern, str(value or ""), flags))


def _query_match(doc, query):
    for key, expected in (query or {}).items():
        if key == "$and":
            if not all(_query_match(doc, clause) for clause in expected):
                return False
            continue
        if key == "$or":
            if not any(_query_match(doc, clause) for clause in expected):
                return False
            continue
        actual = doc.get(key)
        if isinstance(expected, dict):
            if "$in" in expected:
                if actual not in expected["$in"]:
                    return False
            elif "$nin" in expected:
                if actual in expected["$nin"]:
                    return False
            elif "$regex" in expected:
                if not _matches_ci(expected, actual):
                    return False
            else:
                if actual != expected:
                    return False
        elif actual != expected:
            return False
    return True


class FakeCursor:
    def __init__(self, rows):
        self.rows = list(rows)

    async def to_list(self, n):
        return self.rows[:n]

    def __aiter__(self):
        self._iter = iter(self.rows)
        return self

    async def __anext__(self):
        try:
            return next(self._iter)
        except StopIteration:
            raise StopAsyncIteration


class FakeLocks:
    def __init__(self):
        self.docs = {}

    async def insert_one(self, doc):
        key = doc["request_group_key"]
        if key in self.docs:
            raise DuplicateKeyError("dup")
        self.docs[key] = dict(doc)

    async def find_one(self, query, proj=None):
        key = query.get("request_group_key")
        if isinstance(key, dict) and "$in" in key:
            for candidate in key["$in"]:
                if candidate in self.docs and _query_match(self.docs[candidate], {**query, "request_group_key": candidate}):
                    return dict(self.docs[candidate])
            return None
        for doc in self.docs.values():
            if _query_match(doc, query):
                return dict(doc)
        return None

    def find(self, query, proj=None):
        return FakeCursor([dict(d) for d in self.docs.values() if _query_match(d, query)])

    async def find_one_and_update(self, query, update, return_document=None):
        doc = await self.find_one(query)
        if not doc:
            return None
        doc.update(update.get("$set") or {})
        self.docs[doc["request_group_key"]] = doc
        return dict(doc)

    async def find_one_and_delete(self, query):
        doc = self.docs.get(query.get("request_group_key"))
        if not doc:
            return None
        if not _query_match(doc, query):
            return None
        return self.docs.pop(doc["request_group_key"])

    async def delete_one(self, query):
        removed = await self.find_one_and_delete(query)
        return type("R", (), {"deleted_count": 1 if removed else 0})()

    async def update_one(self, query, update, upsert=False):
        doc = await self.find_one(query)
        if not doc:
            if not upsert:
                return type("R", (), {"matched_count": 0})()
            doc = {}
            key = (update.get("$set") or {}).get("request_group_key") or query.get("request_group_key")
            doc["request_group_key"] = key
        doc.update(update.get("$set") or {})
        self.docs[doc["request_group_key"]] = doc
        return type("R", (), {"matched_count": 1})()


class FakeActions:
    def __init__(self):
        self.docs = []

    async def find_one(self, query, proj=None):
        for doc in self.docs:
            if _query_match(doc, query):
                return dict(doc)
        return None

    def find(self, query, proj=None):
        return FakeCursor([dict(d) for d in self.docs if _query_match(d, query)])

    async def update_one(self, query, update, upsert=False):
        for i, doc in enumerate(self.docs):
            if _query_match(doc, query):
                doc.update(update.get("$set") or {})
                self.docs[i] = doc
                return type("R", (), {"matched_count": 1})()
        if upsert:
            new_doc = dict(query)
            # Strip operator forms from query keys used as identity
            clean = {k: v for k, v in query.items() if not isinstance(v, dict)}
            clean.update(update.get("$set") or {})
            self.docs.append(clean)
        return type("R", (), {"matched_count": 0})()


class FakeEvents:
    def __init__(self):
        self.docs = []

    async def insert_one(self, doc):
        self.docs.append(dict(doc))


class FakeOrderRequests:
    def __init__(self, lines):
        self.lines = [dict(x) for x in lines]

    def find(self, query, proj=None):
        matched = [dict(x) for x in self.lines if _query_match(x, query)]
        return FakeCursor(matched)

    async def find_one(self, query, proj=None):
        for line in self.lines:
            if _query_match(line, query):
                return dict(line)
        return None


class FakeHeaders:
    def __init__(self, docs=None):
        self.docs = list(docs or [])

    async def find_one(self, query, proj=None):
        for doc in self.docs:
            if _query_match(doc, query):
                return dict(doc)
        return None

    def find(self, query, proj=None):
        return FakeCursor([dict(d) for d in self.docs if _query_match(d, query)])


class FakeAudit:
    def __init__(self):
        self.docs = []

    async def insert_one(self, doc):
        self.docs.append(doc)


class FakeDevices:
    def __init__(self, rows):
        self.rows = rows

    def find(self, query, proj=None):
        # Ignore complex Mongo operators ($exists/$ne); eligibility is filtered in Python.
        return FakeCursor([dict(r) for r in self.rows])


class FakeLogs:
    async def insert_one(self, doc):
        return None


def _session(device_id="d1", user_id="u1", name="Ravi", dealer="Dealer A", branch="Vanagaram", brand="Honda"):
    return {
        "device": {
            "device_id": device_id,
            "device_user_name": name,
            "device_user_mobile": "999",
            "device_name": f"Phone-{device_id}",
        },
        "mobile_user": {"mobile_user_id": user_id, "name": name, "mobile_number": "999"},
        "brand_name": brand,
        "dealer_name": dealer,
        "branch": branch,
    }


def _live_line(group_key="RQ1", dealer="Dealer A", branch="Vanagaram", brand="Honda"):
    return {
        "id": "L1",
        "part_number": "P1",
        "status": "Requested",
        "requested_qty": 1,
        "request_number": group_key,
        "request_group_id": group_key,
        "supplying_brand": brand,
        "supplying_dealer": dealer,
        "supplying_branch": branch,
        "requesting_dealer": "Other Dealer",
        "requesting_branch": "Other Branch",
        "requested_at": datetime.now(timezone.utc).isoformat(),
    }


# ---------------------------------------------------------------------------
# Broadcast / push flags
# ---------------------------------------------------------------------------

def test_broadcast_pick_reject_flags_can_snooze_false(monkeypatch):
    asyncio.run(_test_broadcast_pick_reject_flags_can_snooze_false(monkeypatch))


async def _test_broadcast_pick_reject_flags_can_snooze_false(monkeypatch):
    sent = []

    class FakeCycles:
        async def find_one(self, query, proj=None):
            return None

        async def update_one(self, query, update, upsert=False):
            return type("R", (), {"matched_count": 1})()

        async def insert_one(self, doc):
            return None

    class FakeDB:
        mobile_devices = FakeDevices([
            {
                "device_id": "d1",
                "push_token": "ExponentPushToken[a]",
                "mobile_user_id": "u1",
                "status": "active",
                "session_active": True,
                "push_enabled": True,
                "brand_name": "Honda",
                "dealer_name": "Dealer A",
                "branch": "Vanagaram",
                "data_origin": "testing",
            },
            {
                "device_id": "d2",
                "push_token": "ExponentPushToken[b]",
                "mobile_user_id": "u2",
                "status": "active",
                "session_active": True,
                "push_enabled": True,
                "brand_name": "Honda",
                "dealer_name": "Dealer B",
                "branch": "Porur",
                "data_origin": "testing",
            },
        ])
        mobile_push_delivery_logs = FakeLogs()
        mobile_notification_actions = FakeActions()
        mobile_request_cycles = FakeCycles()

    monkeypatch.setattr(
        mobile_push, "_post_expo_push_http",
        lambda messages: sent.extend(messages) or {"ok": True, "sent": len(messages), "body": {}},
    )
    monkeypatch.setattr(
        mobile_push, "send_expo_push_messages",
        lambda messages: sent.extend(messages) or {"ok": True, "sent": len(messages)},
    )
    result = await mobile_push.notify_branch_request_push(
        FakeDB(),
        {
            "id": "RQ1",
            "request_number": "RQ1",
            "supplying_brand": "Honda",
            "supplying_dealer": "Dealer A",
            "supplying_branch": "Vanagaram",
            "requesting_dealer": "Other Dealer",
            "requesting_branch": "Other Branch",
            "total_items": 1,
            "total_qty": 1,
        },
        "new",
    )
    assert result.get("ok")
    assert result.get("sent") == 2
    assert result.get("broadcast") == "brand_dealers"
    assert len(sent) == 2
    for msg in sent:
        assert msg["data"]["actions"] == "pick_reject"
        assert msg["data"]["can_snooze"] is False


# ---------------------------------------------------------------------------
# Simultaneous Pick race
# ---------------------------------------------------------------------------

def test_simultaneous_pick_second_gets_already_picked(monkeypatch):
    asyncio.run(_test_simultaneous_pick_second_gets_already_picked(monkeypatch))


async def _test_simultaneous_pick_second_gets_already_picked(monkeypatch):
    locks = FakeLocks()
    actions = FakeActions()
    line = _live_line()

    class FakeDB:
        mobile_request_group_locks = locks
        mobile_notification_actions = actions
        mobile_audit_logs = FakeAudit()
        order_requests = FakeOrderRequests([line])
        request_headers = FakeHeaders([{
            "request_number": "RQ1",
            "response_deadline": (datetime.now(timezone.utc) + timedelta(minutes=30)).isoformat(),
        }])

    async def fake_brand_lines(key, session, statuses=None):
        return [dict(line)]

    async def fake_header(rn):
        return {
            "request_number": rn or "RQ1",
            "response_deadline": (datetime.now(timezone.utc) + timedelta(minutes=30)).isoformat(),
        }

    monkeypatch.setattr(mobile_api, "db", FakeDB())
    monkeypatch.setattr(mobile_api, "_brand_group_lines", fake_brand_lines)
    monkeypatch.setattr(mobile_api, "_header_for_request_number", fake_header)
    monkeypatch.setattr(mobile_push, "notify_request_picked_push", lambda *a, **k: asyncio.sleep(0))

    payload = mobile_api.NotificationActionRequest(request_group_key="RQ1")
    first = await mobile_api.accept_notification(payload, session=_session("d1", "u1", "Ravi"))
    assert first["status"] == "picked"
    assert first.get("confirm_deadline_at")
    assert first.get("confirm_window_seconds") == 3000

    with pytest.raises(HTTPException) as err:
        await mobile_api.accept_notification(
            payload, session=_session("d2", "u2", "Kumar", dealer="Dealer B", branch="Porur"),
        )
    assert err.value.status_code == 409
    assert err.value.detail["code"] == "ALREADY_PICKED"
    assert err.value.detail["message"] == "This request was already picked by another dealer."


# ---------------------------------------------------------------------------
# Reject stores reason
# ---------------------------------------------------------------------------

def test_reject_stores_reason_in_notification_actions(monkeypatch):
    asyncio.run(_test_reject_stores_reason_in_notification_actions(monkeypatch))


async def _test_reject_stores_reason_in_notification_actions(monkeypatch):
    locks = FakeLocks()
    actions = FakeActions()
    line = _live_line()

    class FakeDB:
        mobile_request_group_locks = locks
        mobile_notification_actions = actions
        mobile_audit_logs = FakeAudit()
        order_requests = FakeOrderRequests([line])
        request_headers = FakeHeaders([{
            "request_number": "RQ1",
            "response_deadline": (datetime.now(timezone.utc) + timedelta(minutes=30)).isoformat(),
        }])

    async def fake_scope(key, session, statuses=None):
        return [dict(line)]

    async def fake_header(rn):
        return {
            "request_number": rn or "RQ1",
            "response_deadline": (datetime.now(timezone.utc) + timedelta(minutes=30)).isoformat(),
        }

    monkeypatch.setattr(mobile_api, "db", FakeDB())
    monkeypatch.setattr(mobile_api, "_scope_group_lines", fake_scope)
    monkeypatch.setattr(mobile_api, "_header_for_request_number", fake_header)

    payload = mobile_api.NotificationRejectRequest(request_group_key="RQ1", reason="No stock today")
    result = await mobile_api.reject_notification(payload, session=_session())
    assert result["dealer_dismissed"] is True
    assert result["rejection_reason"] == "No stock today"
    assert len(actions.docs) == 1
    stored = actions.docs[0]
    assert stored["action"] == "reject"
    assert stored["rejection_reason"] == "No stock today"
    assert stored["dealer_name"] == "Dealer A"
    assert stored["request_id"] == "RQ1"


# ---------------------------------------------------------------------------
# Winner timeout release + reopen
# ---------------------------------------------------------------------------

def test_winner_timeout_release_and_reopen():
    asyncio.run(_test_winner_timeout_release_and_reopen())


async def _test_winner_timeout_release_and_reopen():
    deadline = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
    locks = FakeLocks()
    events = FakeEvents()
    await locks.insert_one({
        "request_group_key": "RQ1",
        "device_id": "d1",
        "lock_status": "picked",
        "confirm_deadline_at": deadline,
        "dealer_name": "Dealer A",
        "branch": "Vanagaram",
    })

    class FakeDB:
        mobile_request_group_locks = locks
        mobile_request_claim_events = events

    lock = await locks.find_one({"request_group_key": "RQ1"})
    assert claim.is_confirm_expired(lock) is True
    released = await claim.release_expired_claim(FakeDB(), lock)
    assert released is not None
    assert await locks.find_one({"request_group_key": "RQ1"}) is None
    assert events.docs[0]["event"] == "confirm_timeout_release"
    assert events.docs[0]["confirm_deadline_at"] == deadline

    # Mismatched deadline must not release
    await locks.insert_one({
        "request_group_key": "RQ2",
        "device_id": "d1",
        "lock_status": "picked",
        "confirm_deadline_at": deadline,
    })
    stale = dict(locks.docs["RQ2"])
    stale["confirm_deadline_at"] = (datetime.now(timezone.utc) - timedelta(seconds=5)).isoformat()
    assert await claim.release_expired_claim(FakeDB(), stale) is None
    assert "RQ2" in locks.docs


# ---------------------------------------------------------------------------
# Confirmation within window freezes (complete path)
# ---------------------------------------------------------------------------

def test_confirmation_within_window_not_expired():
    future = (datetime.now(timezone.utc) + timedelta(seconds=40)).isoformat()
    lock = {"lock_status": "picked", "confirm_deadline_at": future, "device_id": "d1"}
    assert claim.is_confirm_expired(lock) is False
    # complete_picking only blocks when expired; within-window path freezes via
    # existing ownership suite (test_complete_qty_full_approves_and_freezes_timer).


def test_stale_complete_after_confirm_expired(monkeypatch):
    asyncio.run(_test_stale_complete_after_confirm_expired(monkeypatch))


async def _test_stale_complete_after_confirm_expired(monkeypatch):
    past = (datetime.now(timezone.utc) - timedelta(seconds=2)).isoformat()
    locks = FakeLocks()
    await locks.insert_one({
        "request_group_key": "RQ1",
        "device_id": "d1",
        "mobile_user_id": "MU1",
        "device_user_name": "Ravi",
        "lock_status": "picked",
        "confirm_deadline_at": past,
        "line_responses": {"L1": {"accepted_qty": 1, "remark": ""}},
    })

    class FakeDB:
        mobile_request_group_locks = locks
        mobile_request_claim_events = FakeEvents()
        order_requests = FakeOrderRequests([])
        request_headers = FakeHeaders([])
        mobile_audit_logs = FakeAudit()

    monkeypatch.setattr(mobile_api, "db", FakeDB())
    with pytest.raises(HTTPException) as err:
        await mobile_api.complete_picking(
            mobile_api.NotificationCompleteRequest(request_group_key="RQ1"),
            session=_session(),
        )
    assert err.value.status_code == 409
    assert err.value.detail.get("status") == "confirm_expired"
    assert "RQ1" not in locks.docs


# ---------------------------------------------------------------------------
# Stale Pick rejection after another claimed
# ---------------------------------------------------------------------------

def test_stale_pick_rejected_after_another_claimed(monkeypatch):
    asyncio.run(_test_stale_pick_rejected_after_another_claimed(monkeypatch))


async def _test_stale_pick_rejected_after_another_claimed(monkeypatch):
    locks = FakeLocks()
    actions = FakeActions()
    line = _live_line()
    deadline = (datetime.now(timezone.utc) + timedelta(seconds=50)).isoformat()
    await locks.insert_one({
        "request_group_key": "RQ1",
        "device_id": "winner",
        "device_user_name": "Winner",
        "lock_status": "picked",
        "confirm_deadline_at": deadline,
        "dealer_name": "Dealer A",
        "branch": "Vanagaram",
    })

    class FakeDB:
        mobile_request_group_locks = locks
        mobile_notification_actions = actions
        mobile_audit_logs = FakeAudit()
        order_requests = FakeOrderRequests([line])
        request_headers = FakeHeaders([])

    async def fake_brand_lines(key, session, statuses=None):
        return [dict(line)]

    async def fake_header(rn):
        return {
            "request_number": "RQ1",
            "response_deadline": (datetime.now(timezone.utc) + timedelta(minutes=30)).isoformat(),
        }

    monkeypatch.setattr(mobile_api, "db", FakeDB())
    monkeypatch.setattr(mobile_api, "_brand_group_lines", fake_brand_lines)
    monkeypatch.setattr(mobile_api, "_header_for_request_number", fake_header)

    with pytest.raises(HTTPException) as err:
        await mobile_api.accept_notification(
            mobile_api.NotificationActionRequest(request_group_key="RQ1"),
            session=_session("late", "u9", "Late"),
        )
    assert err.value.detail["code"] == "ALREADY_PICKED"
    assert err.value.detail["message"] == claim.ALREADY_PICKED_MESSAGE
    assert err.value.detail["picked_by_name"] == "Winner"


# ---------------------------------------------------------------------------
# dismissed_dealer_keys after reject
# ---------------------------------------------------------------------------

def test_dismissed_dealer_keys_after_reject():
    asyncio.run(_test_dismissed_dealer_keys_after_reject())


async def _test_dismissed_dealer_keys_after_reject():
    actions = FakeActions()
    actions.docs.append({
        "request_id": "RQ1",
        "action": "reject",
        "dealer_name": "Dealer A",
        "rejection_reason": "busy",
    })
    actions.docs.append({
        "request_id": "RQ1",
        "action": "skip",
        "dealer_name": "Dealer B",
    })

    class FakeDB:
        mobile_notification_actions = actions

    keys = await claim.dismissed_dealer_keys(FakeDB(), "RQ1")
    assert "dealer a" in keys
    assert "dealer b" not in keys


def test_broadcast_excludes_dismissed_dealers(monkeypatch):
    asyncio.run(_test_broadcast_excludes_dismissed_dealers(monkeypatch))


async def _test_broadcast_excludes_dismissed_dealers(monkeypatch):
    sent = []
    actions = FakeActions()
    actions.docs.append({
        "request_id": "RQ1",
        "action": "reject",
        "dealer_name": "Dealer A",
    })

    class FakeCycles:
        async def find_one(self, query, proj=None):
            return None

        async def update_one(self, query, update, upsert=False):
            return type("R", (), {"matched_count": 1})()

        async def insert_one(self, doc):
            return None

    class FakeDB:
        mobile_devices = FakeDevices([
            {
                "device_id": "d1",
                "push_token": "ExponentPushToken[a]",
                "mobile_user_id": "u1",
                "status": "active",
                "session_active": True,
                "push_enabled": True,
                "brand_name": "Honda",
                "dealer_name": "Dealer A",
                "branch": "Vanagaram",
                "data_origin": "testing",
            },
            {
                "device_id": "d2",
                "push_token": "ExponentPushToken[b]",
                "mobile_user_id": "u2",
                "status": "active",
                "session_active": True,
                "push_enabled": True,
                "brand_name": "Honda",
                "dealer_name": "Dealer B",
                "branch": "Porur",
                "data_origin": "testing",
            },
        ])
        mobile_push_delivery_logs = FakeLogs()
        mobile_notification_actions = actions
        mobile_request_cycles = FakeCycles()

    monkeypatch.setattr(
        mobile_push, "_post_expo_push_http",
        lambda messages: sent.extend(messages) or {"ok": True, "sent": len(messages), "body": {}},
    )
    monkeypatch.setattr(
        mobile_push, "send_expo_push_messages",
        lambda messages: sent.extend(messages) or {"ok": True, "sent": len(messages)},
    )
    result = await mobile_push.notify_branch_request_push(
        FakeDB(),
        {
            "id": "RQ1",
            "request_number": "RQ1",
            "supplying_brand": "Honda",
            "requesting_dealer": "Other",
            "requesting_branch": "Elsewhere",
        },
        "new",
    )
    assert result["sent"] == 1
    assert sent[0]["to"] == "ExponentPushToken[b]"


# ---------------------------------------------------------------------------
# today-upload-status shape
# ---------------------------------------------------------------------------

def test_today_upload_status_shape(monkeypatch):
    asyncio.run(_test_today_upload_status_shape(monkeypatch))


async def _test_today_upload_status_shape(monkeypatch):
    import auto_perpetual as ap

    async def fake_date_key(db, *, brand_name, dealer_name, branch):
        return "20261017"

    monkeypatch.setattr(ap, "todays_branch_stock_date_key", fake_date_key)
    monkeypatch.setattr(ap, "ist_date_key", lambda: "2026-10-17")
    monkeypatch.setattr(mobile_api, "db", object())

    result = await mobile_api.today_upload_status(session=_session())
    assert result["uploaded"] is True
    assert result["error"] is False
    assert result["retry"] is False
    assert result["today"] == "2026-10-17"
    assert result["active_date_key"] == "20261017"
    assert result["brand_name"] == "Honda"
    assert result["dealer_name"] == "Dealer A"
    assert result["branch"] == "Vanagaram"
    assert result["message"] is None

    async def missing_key(db, *, brand_name, dealer_name, branch):
        return None

    monkeypatch.setattr(ap, "todays_branch_stock_date_key", missing_key)
    missing = await mobile_api.today_upload_status(session=_session())
    assert missing["uploaded"] is False
    assert missing["retry"] is True
    assert missing["message"] == ap.TODAY_STOCK_MISSING_MESSAGE


def test_already_picked_detail_message_constant():
    detail = claim.already_picked_detail({"device_user_name": "X"})
    assert detail["message"] == "This request was already picked by another dealer."
    assert mobile_api._already_picked_detail({"device_user_name": "X"})["message"] == (
        "This request was already picked by another dealer."
    )
