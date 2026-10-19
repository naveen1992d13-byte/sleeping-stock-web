"""Testing-env request ringing: lifecycle chokepoint, cycles, scheduler (no live Expo)."""
from __future__ import annotations

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
import mobile_request_cycle as cycles
import mobile_request_cycle_scheduler as cycle_sched


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
            elif "$exists" in expected:
                exists = key in doc and doc.get(key) is not None
                if bool(expected["$exists"]) != exists:
                    return False
            elif "$ne" in expected:
                if actual == expected["$ne"]:
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


class FakeDevices:
    def __init__(self, rows):
        self.rows = [dict(r) for r in rows]

    def find(self, query, proj=None):
        # Eligibility is re-checked in Python; ignore complex Mongo operators here.
        return FakeCursor([dict(r) for r in self.rows])

    async def update_many(self, query, update):
        n = 0
        for row in self.rows:
            if _query_match(row, query or {}):
                row.update(update.get("$set") or {})
                n += 1
        return type("R", (), {"modified_count": n, "matched_count": n})()

    async def update_one(self, query, update, upsert=False):
        return await self.update_many(query, update)


class FakeCycles:
    def __init__(self):
        self.docs = {}

    async def find_one(self, query, proj=None):
        for doc in self.docs.values():
            if _query_match(doc, query):
                return dict(doc)
        return None

    def find(self, query, proj=None):
        return FakeCursor([dict(d) for d in self.docs.values() if _query_match(d, query)])

    async def insert_one(self, doc):
        key = doc["request_group_key"]
        if key in self.docs:
            raise DuplicateKeyError("dup")
        self.docs[key] = dict(doc)

    async def update_one(self, query, update, upsert=False):
        doc = await self.find_one(query)
        if not doc:
            if not upsert:
                return type("R", (), {"matched_count": 0})()
            doc = {"request_group_key": query.get("request_group_key")}
        doc.update(update.get("$set") or {})
        self.docs[doc["request_group_key"]] = doc
        return type("R", (), {"matched_count": 1})()


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

    async def find_one_and_delete(self, query):
        for key, doc in list(self.docs.items()):
            if _query_match(doc, query):
                return self.docs.pop(key)
        return None

    async def delete_one(self, query):
        removed = await self.find_one_and_delete(query)
        return type("R", (), {"deleted_count": 1 if removed else 0})()


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
            clean = {k: v for k, v in query.items() if not isinstance(v, dict)}
            clean.update(update.get("$set") or {})
            self.docs.append(clean)
        return type("R", (), {"matched_count": 0})()


class FakeLogs:
    async def insert_one(self, doc):
        return None


class FakeEvents:
    def __init__(self):
        self.docs = []

    async def insert_one(self, doc):
        self.docs.append(dict(doc))


class FakeOrderRequests:
    def __init__(self, lines):
        self.lines = [dict(x) for x in lines]

    def find(self, query, proj=None):
        return FakeCursor([dict(x) for x in self.lines if _query_match(x, query)])

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


class FakeAudit:
    def __init__(self):
        self.docs = []

    async def insert_one(self, doc):
        self.docs.append(doc)


def _testing_dev(device_id, token, dealer, branch, *, origin="testing", brand="Honda", **extra):
    row = {
        "device_id": device_id,
        "push_token": token,
        "mobile_user_id": f"u-{device_id}",
        "status": "active",
        "session_active": True,
        "push_enabled": True,
        "brand_name": brand,
        "dealer_name": dealer,
        "branch": branch,
        "data_origin": origin,
    }
    row.update(extra)
    return row


def _group_doc():
    return {
        "id": "RQ1",
        "request_number": "RQ1",
        "supplying_brand": "Honda",
        "supplying_dealer": "Dealer A",
        "supplying_branch": "Vanagaram",
        "requesting_brand": "Honda",
        "requesting_dealer": "Requester Dealer",
        "requesting_branch": "Requester Branch",
        "total_items": 1,
        "total_qty": 1,
    }


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


def _live_line():
    return {
        "id": "L1",
        "part_number": "P1",
        "status": "Requested",
        "requested_qty": 1,
        "request_number": "RQ1",
        "request_group_id": "RQ1",
        "supplying_brand": "Honda",
        "supplying_dealer": "Dealer A",
        "supplying_branch": "Vanagaram",
        "requesting_dealer": "Requester Dealer",
        "requesting_branch": "Requester Branch",
        "requested_at": datetime.now(timezone.utc).isoformat(),
    }


def _patch_testing_env(monkeypatch):
    monkeypatch.setenv("APP_ENV", "testing")
    monkeypatch.setenv("PUSH_TEST_MODE", "true")


# ---------------------------------------------------------------------------
# Generic send still blocked
# ---------------------------------------------------------------------------

def test_generic_send_still_blocked_under_push_test_mode(monkeypatch):
    _patch_testing_env(monkeypatch)
    called = []

    def boom(messages):
        called.append(messages)
        return {"ok": True, "sent": len(messages)}

    monkeypatch.setattr(mobile_push, "_post_expo_push_http", boom)
    result = mobile_push.send_expo_push_messages([
        {"to": "ExponentPushToken[x]", "title": "nope", "data": {"type": "auto_perpetual"}},
    ])
    assert result["reason"] == "testing_push_blocked"
    assert result["sent"] == 0
    assert called == []


def test_unrelated_lifecycle_type_blocked(monkeypatch):
    asyncio.run(_test_unrelated_lifecycle_type_blocked(monkeypatch))


async def _test_unrelated_lifecycle_type_blocked(monkeypatch):
    _patch_testing_env(monkeypatch)
    called = []
    monkeypatch.setattr(
        mobile_push, "_post_expo_push_http",
        lambda messages: called.append(messages) or {"ok": True, "sent": len(messages), "body": {}},
    )
    devices = [_testing_dev("d1", "ExponentPushToken[a]", "Dealer A", "Vanagaram")]
    result = await mobile_push.send_approved_request_lifecycle_push(
        type("DB", (), {"mobile_devices": FakeDevices(devices)})(),
        [{"to": "ExponentPushToken[a]", "data": {"type": "auto_perpetual"}}],
        lifecycle_type="auto_perpetual",
        devices=devices,
    )
    assert result["reason"] == "lifecycle_type_blocked"
    assert result["sent"] == 0
    assert called == []


# ---------------------------------------------------------------------------
# Broadcast filters
# ---------------------------------------------------------------------------

def test_broadcast_eligible_testing_excludes_requester_and_production(monkeypatch):
    asyncio.run(_test_broadcast_eligible_testing_excludes_requester_and_production(monkeypatch))


async def _test_broadcast_eligible_testing_excludes_requester_and_production(monkeypatch):
    _patch_testing_env(monkeypatch)
    sent = []

    def capture(messages):
        sent.extend(messages)
        return {"ok": True, "sent": len(messages), "body": {"data": [{"status": "ok"} for _ in messages]}}

    monkeypatch.setattr(mobile_push, "_post_expo_push_http", capture)

    devices = FakeDevices([
        _testing_dev("t1", "ExponentPushToken[t1]", "Dealer A", "Vanagaram"),
        _testing_dev("t2", "ExponentPushToken[t2]", "Dealer B", "Porur"),
        _testing_dev("req", "ExponentPushToken[req]", "Requester Dealer", "Requester Branch"),
        _testing_dev("prod", "ExponentPushToken[prod]", "Dealer C", "Chrompet", origin="production"),
        _testing_dev("snap", "ExponentPushToken[snap]", "Dealer D", "Guindy", origin="snapshot"),
        _testing_dev("mirror", "ExponentPushToken[mirror]", "Dealer E", "Tambaram", origin=""),  # missing
    ])

    class FakeDB:
        mobile_devices = devices
        mobile_push_delivery_logs = FakeLogs()
        mobile_notification_actions = FakeActions()
        mobile_request_cycles = FakeCycles()

    result = await mobile_push.notify_branch_request_push(FakeDB(), _group_doc(), "new")
    assert result.get("ok")
    assert result.get("sent") == 2
    tokens = {m["to"] for m in sent}
    assert tokens == {"ExponentPushToken[t1]", "ExponentPushToken[t2]"}
    assert all(m["data"]["type"] == "branch_request" for m in sent)
    cycle = await cycles.get_cycle(FakeDB(), "RQ1")
    assert cycle["status"] == cycles.STATUS_RINGING
    assert cycle["cycle_number"] == 1


# ---------------------------------------------------------------------------
# Pick / reject / closed
# ---------------------------------------------------------------------------

def test_simultaneous_pick_one_winner(monkeypatch):
    asyncio.run(_test_simultaneous_pick_one_winner(monkeypatch))


async def _test_simultaneous_pick_one_winner(monkeypatch):
    _patch_testing_env(monkeypatch)
    locks = FakeLocks()
    line = _live_line()

    class FakeDB:
        mobile_request_group_locks = locks
        mobile_notification_actions = FakeActions()
        mobile_audit_logs = FakeAudit()
        order_requests = FakeOrderRequests([line])
        request_headers = FakeHeaders([{
            "request_number": "RQ1",
            "response_deadline": (datetime.now(timezone.utc) + timedelta(minutes=30)).isoformat(),
        }])
        mobile_request_cycles = FakeCycles()
        mobile_devices = FakeDevices([])

    async def fake_brand_lines(key, session, statuses=None):
        return [dict(line)]

    async def fake_header(rn):
        return {"request_number": rn or "RQ1", "response_deadline": (datetime.now(timezone.utc) + timedelta(minutes=30)).isoformat()}

    monkeypatch.setattr(mobile_api, "db", FakeDB())
    monkeypatch.setattr(mobile_api, "_brand_group_lines", fake_brand_lines)
    monkeypatch.setattr(mobile_api, "_header_for_request_number", fake_header)
    monkeypatch.setattr(mobile_push, "notify_request_picked_push", lambda *a, **k: asyncio.sleep(0))

    payload = mobile_api.NotificationActionRequest(request_group_key="RQ1")
    first = await mobile_api.accept_notification(payload, session=_session("d1", "u1", "Ravi"))
    assert first["status"] == "picked"
    with pytest.raises(HTTPException) as err:
        await mobile_api.accept_notification(
            payload, session=_session("d2", "u2", "Kumar", dealer="Dealer B", branch="Porur"),
        )
    assert err.value.status_code == 409
    assert err.value.detail["code"] == "ALREADY_PICKED"


def test_reject_without_reason_fails(monkeypatch):
    asyncio.run(_test_reject_without_reason_fails(monkeypatch))


async def _test_reject_without_reason_fails(monkeypatch):
    monkeypatch.setattr(mobile_api, "db", type("DB", (), {
        "mobile_request_group_locks": FakeLocks(),
        "mobile_notification_actions": FakeActions(),
    })())
    with pytest.raises(HTTPException) as err:
        await mobile_api.reject_notification(
            mobile_api.NotificationRejectRequest(request_group_key="RQ1", reason="  "),
            session=_session(),
        )
    assert err.value.status_code == 400


def test_reject_reason_stored(monkeypatch):
    asyncio.run(_test_reject_reason_stored(monkeypatch))


async def _test_reject_reason_stored(monkeypatch):
    locks = FakeLocks()
    actions = FakeActions()
    cycle_col = FakeCycles()
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
        mobile_request_cycles = cycle_col
        mobile_devices = FakeDevices([
            _testing_dev("d2", "ExponentPushToken[b]", "Dealer B", "Porur"),
        ])

    async def fake_scope(key, session, statuses=None):
        return [dict(line)]

    async def fake_header(rn):
        return {"request_number": rn or "RQ1", "response_deadline": (datetime.now(timezone.utc) + timedelta(minutes=30)).isoformat()}

    monkeypatch.setattr(mobile_api, "db", FakeDB())
    monkeypatch.setattr(mobile_api, "_scope_group_lines", fake_scope)
    monkeypatch.setattr(mobile_api, "_header_for_request_number", fake_header)
    await cycles.start_or_reset_cycle(FakeDB(), _group_doc(), cycle_number=1)

    result = await mobile_api.reject_notification(
        mobile_api.NotificationRejectRequest(request_group_key="RQ1", reason="No stock today"),
        session=_session(),
    )
    assert result["rejection_reason"] == "No stock today"
    assert actions.docs[0]["rejection_reason"] == "No stock today"
    stored = await cycles.get_cycle(FakeDB(), "RQ1")
    assert any(x.casefold() == "dealer a" for x in (stored.get("rejected_dealers") or []))


def test_picked_closed_clears_others(monkeypatch):
    asyncio.run(_test_picked_closed_clears_others(monkeypatch))


async def _test_picked_closed_clears_others(monkeypatch):
    _patch_testing_env(monkeypatch)
    sent = []

    def capture(messages):
        sent.extend(messages)
        return {"ok": True, "sent": len(messages), "body": {"data": [{"status": "ok"} for _ in messages]}}

    monkeypatch.setattr(mobile_push, "_post_expo_push_http", capture)
    devices = [
        _testing_dev("winner", "ExponentPushToken[w]", "Dealer A", "Vanagaram"),
        _testing_dev("other", "ExponentPushToken[o]", "Dealer B", "Porur"),
    ]

    class FakeDB:
        mobile_devices = FakeDevices(devices)
        mobile_notification_actions = FakeActions()
        mobile_request_cycles = FakeCycles()

    await cycles.start_or_reset_cycle(FakeDB(), _group_doc(), cycle_number=1)
    result = await mobile_push.notify_request_picked_push(
        FakeDB(),
        _group_doc(),
        picked_by_name="Ravi",
        exclude_device_id="winner",
    )
    assert result["sent"] >= 2
    types = {m["data"]["type"] for m in sent}
    assert "request_picked" in types
    assert "request_closed" in types
    assert all(m["to"] == "ExponentPushToken[o]" for m in sent)
    cycle = await cycles.get_cycle(FakeDB(), "RQ1")
    assert cycle["status"] == cycles.STATUS_PICKED


# ---------------------------------------------------------------------------
# Ring TTL / confirm timeout / max cycles / all-rejected
# ---------------------------------------------------------------------------

def test_ring_ttl_advances_cycle_and_excludes_timed_out_on_confirm(monkeypatch):
    asyncio.run(_test_ring_ttl_advances_cycle_and_excludes_timed_out_on_confirm(monkeypatch))


async def _test_ring_ttl_advances_cycle_and_excludes_timed_out_on_confirm(monkeypatch):
    _patch_testing_env(monkeypatch)
    sent = []
    monkeypatch.setattr(
        mobile_push, "_post_expo_push_http",
        lambda messages: sent.extend(messages) or {"ok": True, "sent": len(messages), "body": {}},
    )
    cycle_col = FakeCycles()
    locks = FakeLocks()
    events = FakeEvents()

    class FakeDB:
        mobile_devices = FakeDevices([
            _testing_dev("d1", "ExponentPushToken[a]", "Dealer A", "Vanagaram"),
            _testing_dev("d2", "ExponentPushToken[b]", "Dealer B", "Porur"),
        ])
        mobile_notification_actions = FakeActions()
        mobile_request_cycles = cycle_col
        mobile_request_group_locks = locks
        mobile_request_claim_events = events
        mobile_push_delivery_logs = FakeLogs()
        request_headers = FakeHeaders([_group_doc()])

    db = FakeDB()
    await cycles.start_or_reset_cycle(db, _group_doc(), cycle_number=1)
    # Force ring deadline into the past.
    await db.mobile_request_cycles.update_one(
        {"request_group_key": "RQ1"},
        {"$set": {"ring_deadline_at": (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()}},
    )
    out = await cycle_sched.run_cycle_scheduler_once(db)
    assert out["ring_due"] == 1
    cycle = await cycles.get_cycle(db, "RQ1")
    assert cycle["cycle_number"] == 2
    assert cycle["status"] == cycles.STATUS_RINGING
    assert any(m["data"]["type"] == "request_cycle_renotify" for m in sent)

    # Confirm timeout excludes the picked dealer.
    sent.clear()
    deadline = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
    await locks.insert_one({
        "request_group_key": "RQ1",
        "device_id": "d1",
        "lock_status": "picked",
        "confirm_deadline_at": deadline,
        "dealer_name": "Dealer A",
        "branch": "Vanagaram",
        "brand_name": "Honda",
    })
    await db.mobile_request_cycles.update_one(
        {"request_group_key": "RQ1"},
        {"$set": {"status": cycles.STATUS_PICKED}},
    )
    out2 = await cycle_sched.run_cycle_scheduler_once(db)
    assert out2["confirm_due"] == 1
    cycle = await cycles.get_cycle(db, "RQ1")
    assert any(x.casefold() == "dealer a" for x in (cycle.get("timed_out_dealers") or []))
    assert any(x.casefold() == "dealer a" for x in (cycle.get("excluded_dealers") or []))
    # Renotify should not include Dealer A
    renotify_tokens = {m["to"] for m in sent if m["data"]["type"] == "request_cycle_renotify"}
    assert "ExponentPushToken[a]" not in renotify_tokens
    assert "ExponentPushToken[b]" in renotify_tokens


def test_max_three_cycles_notifies_requester(monkeypatch):
    asyncio.run(_test_max_three_cycles_notifies_requester(monkeypatch))


async def _test_max_three_cycles_notifies_requester(monkeypatch):
    _patch_testing_env(monkeypatch)
    sent = []
    monkeypatch.setattr(
        mobile_push, "_post_expo_push_http",
        lambda messages: sent.extend(messages) or {"ok": True, "sent": len(messages), "body": {}},
    )
    cycle_col = FakeCycles()

    class FakeDB:
        mobile_devices = FakeDevices([
            _testing_dev("d1", "ExponentPushToken[a]", "Dealer A", "Vanagaram"),
            _testing_dev("req", "ExponentPushToken[req]", "Requester Dealer", "Requester Branch"),
        ])
        mobile_notification_actions = FakeActions()
        mobile_request_cycles = cycle_col
        mobile_request_group_locks = FakeLocks()
        mobile_push_delivery_logs = FakeLogs()
        request_headers = FakeHeaders([_group_doc()])

    db = FakeDB()
    await cycles.start_or_reset_cycle(db, _group_doc(), cycle_number=3)
    await db.mobile_request_cycles.update_one(
        {"request_group_key": "RQ1"},
        {"$set": {"ring_deadline_at": (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()}},
    )
    out = await cycle_sched.run_cycle_scheduler_once(db)
    assert out["ring_due"] == 1
    cycle = await cycles.get_cycle(db, "RQ1")
    assert cycle["status"] == cycles.STATUS_EXHAUSTED
    assert any(m["data"].get("type") == "request_requester_notify" for m in sent)
    assert any(m["data"].get("outcome") == "max_cycles_exhausted" for m in sent)


def test_all_rejected_notifies_requester(monkeypatch):
    asyncio.run(_test_all_rejected_notifies_requester(monkeypatch))


async def _test_all_rejected_notifies_requester(monkeypatch):
    _patch_testing_env(monkeypatch)
    sent = []
    monkeypatch.setattr(
        mobile_push, "_post_expo_push_http",
        lambda messages: sent.extend(messages) or {"ok": True, "sent": len(messages), "body": {}},
    )
    cycle_col = FakeCycles()
    actions = FakeActions()
    line = _live_line()

    class FakeDB:
        mobile_devices = FakeDevices([
            _testing_dev("d1", "ExponentPushToken[a]", "Dealer A", "Vanagaram"),
            _testing_dev("req", "ExponentPushToken[req]", "Requester Dealer", "Requester Branch"),
        ])
        mobile_notification_actions = actions
        mobile_request_cycles = cycle_col
        mobile_request_group_locks = FakeLocks()
        mobile_audit_logs = FakeAudit()
        order_requests = FakeOrderRequests([line])
        request_headers = FakeHeaders([{
            "request_number": "RQ1",
            "response_deadline": (datetime.now(timezone.utc) + timedelta(minutes=30)).isoformat(),
            "requesting_dealer": "Requester Dealer",
            "requesting_branch": "Requester Branch",
        }])
        mobile_push_delivery_logs = FakeLogs()

    db = FakeDB()
    await cycles.start_or_reset_cycle(db, _group_doc(), cycle_number=1)

    async def fake_scope(key, session, statuses=None):
        return [dict(line)]

    async def fake_header(rn):
        return {
            "request_number": rn or "RQ1",
            "response_deadline": (datetime.now(timezone.utc) + timedelta(minutes=30)).isoformat(),
            "requesting_dealer": "Requester Dealer",
            "requesting_branch": "Requester Branch",
        }

    monkeypatch.setattr(mobile_api, "db", db)
    monkeypatch.setattr(mobile_api, "_scope_group_lines", fake_scope)
    monkeypatch.setattr(mobile_api, "_header_for_request_number", fake_header)

    await mobile_api.reject_notification(
        mobile_api.NotificationRejectRequest(request_group_key="RQ1", reason="busy"),
        session=_session(),
    )
    cycle = await cycles.get_cycle(db, "RQ1")
    assert cycle["status"] == cycles.STATUS_ALL_REJECTED
    assert any(m["data"].get("outcome") == "all_rejected" for m in sent)
    assert any(m["to"] == "ExponentPushToken[req]" for m in sent)


def test_scheduler_survives_restart_via_db(monkeypatch):
    asyncio.run(_test_scheduler_survives_restart_via_db(monkeypatch))


async def _test_scheduler_survives_restart_via_db(monkeypatch):
    _patch_testing_env(monkeypatch)
    sent = []
    monkeypatch.setattr(
        mobile_push, "_post_expo_push_http",
        lambda messages: sent.extend(messages) or {"ok": True, "sent": len(messages), "body": {}},
    )
    cycle_col = FakeCycles()

    class FakeDB:
        mobile_devices = FakeDevices([
            _testing_dev("d1", "ExponentPushToken[a]", "Dealer A", "Vanagaram"),
        ])
        mobile_notification_actions = FakeActions()
        mobile_request_cycles = cycle_col
        mobile_request_group_locks = FakeLocks()
        mobile_push_delivery_logs = FakeLogs()
        request_headers = FakeHeaders([_group_doc()])

    db = FakeDB()
    await cycles.start_or_reset_cycle(db, _group_doc(), cycle_number=1)
    await db.mobile_request_cycles.update_one(
        {"request_group_key": "RQ1"},
        {"$set": {"ring_deadline_at": (datetime.now(timezone.utc) - timedelta(seconds=2)).isoformat()}},
    )
    # Simulate restart: new scheduler instance reading the same DB deadlines.
    tick = await cycle_sched.run_cycle_scheduler_once(db)
    assert tick["ring_due"] == 1
    cycle = await cycles.get_cycle(db, "RQ1")
    assert cycle["cycle_number"] == 2


def test_device_not_registered_clears_token(monkeypatch):
    asyncio.run(_test_device_not_registered_clears_token(monkeypatch))


async def _test_device_not_registered_clears_token(monkeypatch):
    _patch_testing_env(monkeypatch)
    devices = FakeDevices([
        _testing_dev("d1", "ExponentPushToken[dead]", "Dealer A", "Vanagaram"),
        _testing_dev("d2", "ExponentPushToken[ok]", "Dealer B", "Porur"),
    ])

    def fake_post(messages):
        tickets = []
        for msg in messages:
            if msg["to"] == "ExponentPushToken[dead]":
                tickets.append({
                    "status": "error",
                    "message": "\"ExponentPushToken[dead]\" is not a registered push notification recipient",
                    "details": {"error": "DeviceNotRegistered"},
                })
            else:
                tickets.append({"status": "ok", "id": "ticket"})
        return {"ok": True, "sent": len(messages), "body": {"data": tickets}}

    monkeypatch.setattr(mobile_push, "_post_expo_push_http", fake_post)

    class FakeDB:
        mobile_devices = devices
        mobile_push_delivery_logs = FakeLogs()
        mobile_notification_actions = FakeActions()
        mobile_request_cycles = FakeCycles()

    result = await mobile_push.notify_branch_request_push(FakeDB(), _group_doc(), "new")
    assert result.get("ok")
    dead = next(d for d in devices.rows if d["device_id"] == "d1")
    assert dead["push_enabled"] is False
    assert dead["push_token"] == ""
    assert dead.get("push_disabled_reason") == "DeviceNotRegistered"
    alive = next(d for d in devices.rows if d["device_id"] == "d2")
    assert alive["push_enabled"] is True
    assert alive["push_token"] == "ExponentPushToken[ok]"


def test_constants():
    assert cycles.RING_TTL_SECONDS == 50
    assert cycles.CONFIRM_WINDOW_SECONDS == 3000
    assert cycles.MAX_CYCLES == 3
    assert claim.CONFIRM_WINDOW_SECONDS == 3000
    assert claim.DEFAULT_CONFIRM_WINDOW_MINUTES == 50
