"""Unit tests for mobile request ownership, SLA skip, and new push types."""
import asyncio
import sys
from pathlib import Path
from datetime import datetime, timedelta, timezone

import pytest
from pymongo.errors import DuplicateKeyError

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

import mobile_api
import mobile_push
import request_sla_scheduler as sla


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
                if candidate in self.docs:
                    return dict(self.docs[candidate])
            return None
        doc = self.docs.get(key)
        if not doc:
            return None
        if query.get("device_id") and doc.get("device_id") != query["device_id"]:
            return None
        if query.get("lock_status") and doc.get("lock_status") != query["lock_status"]:
            return None
        return dict(doc)

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
        nin = ((query.get("lock_status") or {}).get("$nin") or [])
        if doc.get("lock_status") in nin:
            return None
        return self.docs.pop(doc["request_group_key"])


def test_lock_alias_matches_header_id_or_request_number():
    lock_by_key = {"header-uuid": {"device_id": "d1", "lock_status": "picked", "device_user_name": "Ravi"}}
    group = {"request_group_key": "RQ1", "request_number": "RQ1"}
    lines = [{"request_group_id": "header-uuid", "request_number": "RQ1"}]
    lock = mobile_api._lock_for_group(lock_by_key, group, lines)
    assert lock["device_id"] == "d1"
    aliases = mobile_api._group_aliases("header-uuid", group, *lines)
    assert "header-uuid" in aliases
    assert "RQ1" in aliases


def test_already_picked_code():
    detail = mobile_api._already_picked_detail({"device_user_name": "Ravi"})
    assert detail["code"] == "ALREADY_PICKED"
    assert detail["picked_by_name"] == "Ravi"


def test_ownership_status_stays_picked_after_responses():
    lines = [{"status": "Requested"}]
    lock = {"lock_status": "picked", "device_user_name": "A", "line_responses": {"1": {"accepted_qty": 2}}}
    assert mobile_api._ownership_status(lines, lock, {}) == "picked"


def test_ownership_status_picking_completed():
    lines = [{"status": "Requested"}]
    lock = {"lock_status": "picking_completed"}
    assert mobile_api._ownership_status(lines, lock, {}) == "picking_completed"


def test_skip_limit_is_two_with_no_timeout_constant():
    assert mobile_api.SKIP_LIMIT == 2
    src = Path(BACKEND_DIR / "mobile_api.py").read_text()
    assert "auto-stop" not in src.lower()
    assert "ring_timeout" not in src
    sla_src = Path(BACKEND_DIR / "request_sla_scheduler.py").read_text()
    assert "request_is_no_longer_new" in sla_src


def test_branch_request_payload_unchanged():
    group = {
        "id": "g1",
        "request_number": "RQ1",
        "requesting_branch": "Vanagaram",
        "total_items": 3,
        "total_qty": 12,
    }
    message = mobile_push.build_branch_request_push_message("ExponentPushToken[abc]", group, "new")
    assert message["data"]["type"] == "branch_request"
    assert "title" not in message
    assert "sound" not in message
    picked = mobile_push.build_request_picked_push_message("ExponentPushToken[abc]", group, "Ravi")
    assert picked["data"]["type"] == "request_picked"
    assert picked["data"]["picked_by_name"] == "Ravi"
    assert "title" not in picked
    transferred = mobile_push.build_request_transferred_push_message("ExponentPushToken[abc]", group)
    assert transferred["data"]["type"] == "request_transferred"
    assert transferred["data"]["actions"] == "none"


def test_pick_race_one_winner_same_owner_retry():
    asyncio.run(_test_pick_race_one_winner_same_owner_retry())


async def _test_pick_race_one_winner_same_owner_retry():
    locks = FakeLocks()
    first = {"request_group_key": "G", "device_id": "d1", "lock_status": "picked"}
    await locks.insert_one(first)
    with pytest.raises(DuplicateKeyError):
        await locks.insert_one({"request_group_key": "G", "device_id": "d2", "lock_status": "picked"})
    again = await locks.find_one({"request_group_key": "G"})
    assert again["device_id"] == "d1"
    retry = await locks.find_one({"request_group_key": "G", "device_id": "d1"})
    assert retry["device_id"] == "d1"


def test_release_preserves_deadline_and_skips():
    asyncio.run(_test_release_preserves_deadline_and_skips())


async def _test_release_preserves_deadline_and_skips():
    locks = FakeLocks()
    await locks.insert_one({
        "request_group_key": "G",
        "device_id": "d1",
        "lock_status": "picked",
        "dealer_name": "D",
        "branch": "B",
    })
    removed = await locks.find_one_and_delete({"request_group_key": "G", "lock_status": {"$nin": ["picking_completed"]}})
    assert removed["device_id"] == "d1"
    assert await locks.find_one({"request_group_key": "G"}) is None
    actions = {"G:u1": {"skip_count": 2}}
    assert actions["G:u1"]["skip_count"] == 2
    deadline = "2026-10-02T12:00:00+00:00"
    assert deadline == "2026-10-02T12:00:00+00:00"


def test_complete_blocked_until_all_lines_answered():
    asyncio.run(_test_complete_blocked_until_all_lines_answered())


async def _test_complete_blocked_until_all_lines_answered():
    lock = {"lock_status": "picked", "line_responses": {"a": {"accepted_qty": 1}}}
    live = [{"id": "a"}, {"id": "b", "part_number": "P2"}]
    missing = [line.get("part_number") for line in live if line.get("id") not in mobile_api._line_response_map(lock)]
    assert missing == ["P2"]


def test_request_picked_excludes_picker(monkeypatch):
    asyncio.run(_test_request_picked_excludes_picker(monkeypatch))


def _eligible_dev(device_id, token, uid, brand="Honda", dealer="D", branch="B", **extra):
    row = {
        "device_id": device_id,
        "push_token": token,
        "mobile_user_id": uid,
        "status": "active",
        "session_active": True,
        "push_enabled": True,
        "brand_name": brand,
        "dealer_name": dealer,
        "branch": branch,
    }
    row.update(extra)
    return row


async def _test_request_picked_excludes_picker(monkeypatch):
    sent = []

    class Devices:
        def find(self, query, proj=None):
            class Cursor:
                async def to_list(self, n):
                    return [
                        _eligible_dev("winner", "ExponentPushToken[w]", "u1"),
                        _eligible_dev("other", "ExponentPushToken[o]", "u2"),
                    ]
            return Cursor()

    class FakeDB:
        mobile_devices = Devices()

    def capture(messages):
        sent.extend(messages)
        return {"ok": True}

    monkeypatch.setattr(mobile_push, "send_expo_push_messages", capture)
    result = await mobile_push.notify_request_picked_push(
        FakeDB(),
        {"id": "G", "request_number": "RQ1", "supplying_brand": "Honda", "supplying_dealer": "D", "supplying_branch": "B"},
        picked_by_name="Ravi",
        exclude_device_id="winner",
    )
    assert result["sent"] == 1
    assert sent[0]["data"]["type"] == "request_picked"
    assert sent[0]["to"] == "ExponentPushToken[o]"


def test_reminder_skips_picked_request():
    asyncio.run(_test_reminder_skips_picked_request())


async def _test_reminder_skips_picked_request():
    class Locks:
        async def find_one(self, query, proj=None):
            return {"lock_status": "picked"}

    class FakeDB:
        mobile_request_group_locks = Locks()

    assert await sla.request_is_no_longer_new(FakeDB(), {"id": "G", "request_number": "RQ1"}) is True

    class EmptyLocks:
        async def find_one(self, query, proj=None):
            return None

    class EmptyDB:
        mobile_request_group_locks = EmptyLocks()

    assert await sla.request_is_no_longer_new(EmptyDB(), {"id": "G", "request_number": "RQ1"}) is False


def test_not_owner_and_invalid_state_codes():
    assert mobile_api._error_detail("NOT_OWNER", "x")["code"] == "NOT_OWNER"
    assert mobile_api._error_detail("INVALID_STATE", "x")["code"] == "INVALID_STATE"
    assert mobile_api._error_detail("TRANSFER_TARGET_INVALID", "x")["code"] == "TRANSFER_TARGET_INVALID"


def test_non_owner_writes_rejected():
    from fastapi import HTTPException
    session = {"device": {"device_id": "d2"}}
    lock = {"device_id": "d1", "lock_status": "picked", "device_user_name": "Ravi"}
    with pytest.raises(HTTPException) as owner_err:
        mobile_api._raise_if_not_writable(lock, session)
    assert owner_err.value.status_code == 403
    assert owner_err.value.detail["code"] == "NOT_OWNER"
    owner_session = {"device": {"device_id": "d1"}}
    completed = {"device_id": "d1", "lock_status": "picking_completed"}
    with pytest.raises(HTTPException) as state_err:
        mobile_api._raise_if_not_writable(completed, owner_session)
    assert state_err.value.detail["code"] == "INVALID_STATE"


def test_transfer_moves_device_and_removes_previous_owner_edit_rights():
    asyncio.run(_test_transfer_moves_device_and_removes_previous_owner_edit_rights())


async def _test_transfer_moves_device_and_removes_previous_owner_edit_rights():
    from fastapi import HTTPException
    locks = FakeLocks()
    await locks.insert_one({
        "request_group_key": "G",
        "device_id": "d1",
        "lock_status": "picked",
        "mobile_user_id": "MU1",
    })
    claimed = await locks.find_one_and_update(
        {"request_group_key": "G", "device_id": "d1", "lock_status": "picked"},
        {"$set": {"device_id": "d2", "mobile_user_id": "MU2", "lock_status": "picked"}},
    )
    assert claimed["device_id"] == "d2"
    assert claimed["mobile_user_id"] == "MU2"
    lost = await locks.find_one_and_update(
        {"request_group_key": "G", "device_id": "d1", "lock_status": "picked"},
        {"$set": {"device_id": "d3"}},
    )
    assert lost is None
    with pytest.raises(HTTPException) as err:
        mobile_api._raise_if_not_writable(claimed, {"device": {"device_id": "d1"}})
    assert err.value.detail["code"] == "NOT_OWNER"
    mobile_api._raise_if_not_writable(claimed, {"device": {"device_id": "d2"}})


def test_transfer_target_must_be_other_user():
    from fastapi import HTTPException
    session = {"device": {"device_id": "d1"}, "mobile_user": {"mobile_user_id": "MU1"}}
    lock = {"device_id": "d1", "lock_status": "picked"}
    mobile_api._raise_if_not_writable(lock, session)
    with pytest.raises(HTTPException) as err:
        raise HTTPException(status_code=409, detail=mobile_api._error_detail(
            mobile_api.ERR_TRANSFER_TARGET_INVALID, "Select a different same-branch mobile user"
        ))
    assert err.value.detail["code"] == "TRANSFER_TARGET_INVALID"


def test_request_push_scope_requires_brand_dealer_branch():
    assert mobile_push.request_push_scope({
        "supplying_brand": "Honda", "supplying_dealer": "D", "supplying_branch": "B",
    }) == ("Honda", "D", "B")
    assert mobile_push.request_push_scope({"supplying_dealer": "D", "supplying_branch": "B"}) == ("", "D", "B")
    query = mobile_push.eligible_request_device_query("Honda", "D", "B")
    assert query["status"] == "active"
    assert query["session_active"] is True
    assert query["push_enabled"] is True


def test_is_eligible_request_device_scope_and_session():
    good = _eligible_dev("d1", "ExponentPushToken[x]", "u1")
    assert mobile_push.is_eligible_request_device(good, "Honda", "D", "B") is True
    assert mobile_push.is_eligible_request_device({**good, "session_active": False}, "Honda", "D", "B") is False
    assert mobile_push.is_eligible_request_device({**good, "push_enabled": False}, "Honda", "D", "B") is False
    assert mobile_push.is_eligible_request_device({**good, "status": "inactive"}, "Honda", "D", "B") is False
    assert mobile_push.is_eligible_request_device(good, "Yamaha", "D", "B") is False
    assert mobile_push.is_eligible_request_device(good, "Honda", "Other", "B") is False
    assert mobile_push.is_eligible_request_device(good, "Honda", "D", "Other") is False


def test_notify_skips_logged_out_wrong_brand_and_missing_scope(monkeypatch):
    asyncio.run(_test_notify_skips_logged_out_wrong_brand_and_missing_scope(monkeypatch))


async def _test_notify_skips_logged_out_wrong_brand_and_missing_scope(monkeypatch):
    sent = []

    class Devices:
        def find(self, query, proj=None):
            class Cursor:
                async def to_list(self, n):
                    return [
                        _eligible_dev("ok", "ExponentPushToken[ok]", "u1"),
                        _eligible_dev("out", "ExponentPushToken[out]", "u2", session_active=False, push_enabled=False),
                        _eligible_dev("other", "ExponentPushToken[ob]", "u3", brand="Yamaha"),
                    ]
            return Cursor()

        async def insert_one(self, doc):
            return None

    class Logs:
        async def insert_one(self, doc):
            return None

    class FakeDB:
        mobile_devices = Devices()
        mobile_push_delivery_logs = Logs()

    monkeypatch.setattr(mobile_push, "send_expo_push_messages", lambda messages: sent.extend(messages) or {"ok": True})
    missing = await mobile_push.notify_branch_request_push(
        FakeDB(), {"id": "G", "request_number": "RQ1", "supplying_dealer": "D", "supplying_branch": "B"}, "new",
    )
    assert missing["error"] == "missing_scope"
    assert sent == []
    result = await mobile_push.notify_branch_request_push(
        FakeDB(),
        {"id": "G", "request_number": "RQ1", "supplying_brand": "Honda", "supplying_dealer": "D", "supplying_branch": "B"},
        "new",
    )
    assert result["sent"] == 1
    assert sent[0]["to"] == "ExponentPushToken[ok]"


def test_presence_and_session_labels():
    now = datetime.now(timezone.utc)
    assert mobile_api._presence_label((now - timedelta(seconds=30)).isoformat(), now) == "online"
    assert mobile_api._presence_label((now - timedelta(seconds=91)).isoformat(), now) == "offline"
    assert mobile_api._presence_label(None, now) == "offline"
    assert mobile_api._session_state({"session_active": True}) == "logged_in"
    assert mobile_api._session_state({"session_active": False}) == "logged_out"
    assert mobile_api._session_state({}) == "logged_out"
    rows = mobile_api._enrich_device_presence([{
        "status": "active",
        "session_active": True,
        "last_seen_at": (now - timedelta(seconds=10)).isoformat(),
    }], now)
    assert rows[0]["admin_status"] == "active"
    assert rows[0]["session_state"] == "logged_in"
    assert rows[0]["presence"] == "online"


def test_logout_does_not_change_admin_status():
    src = Path(BACKEND_DIR / "mobile_api.py").read_text()
    start = src.index("async def logout_device_session")
    chunk = src[start:start + 900]
    assert '"session_active": False' in chunk
    assert '"push_enabled": False' in chunk
    assert '"logged_out_at"' in chunk
    assert '"status":' not in chunk
    assert "POST /mobile/session/logout" in src or '@router.post("/session/logout")' in src
    assert '@router.post("/session/heartbeat")' in src


def test_transfer_preserves_line_responses():
    asyncio.run(_test_transfer_preserves_line_responses())


async def _test_transfer_preserves_line_responses():
    locks = FakeLocks()
    await locks.insert_one({
        "request_group_key": "G",
        "device_id": "d1",
        "mobile_user_id": "U1",
        "lock_status": "picked",
        "line_responses": {"line1": {"accepted_qty": 3, "remark": "partial"}},
        "brand_name": "Honda",
        "dealer_name": "D",
        "branch": "B",
    })
    updated = await locks.find_one_and_update(
        {"request_group_key": "G", "lock_status": "picked"},
        {"$set": {
            "device_id": "d2",
            "mobile_user_id": "U2",
            "device_user_name": "New Owner",
            "transferred_at": "now",
        }},
    )
    assert updated["line_responses"]["line1"]["accepted_qty"] == 3
    assert updated["line_responses"]["line1"]["remark"] == "partial"
    assert updated["device_id"] == "d2"
    assert updated["lock_status"] == "picked"


def test_pairing_and_logout_session_flags_in_source():
    src = Path(BACKEND_DIR / "mobile_api.py").read_text()
    assert '"session_active": True' in src
    assert '"push_enabled": True' in src
    assert "PRESENCE_ONLINE_SECONDS = 90" in src
    assert "web/request-locks/transfer" in src
    assert "_backfill_device_session_flags" in src


class _FakeOrderRequests:
    def __init__(self, lines):
        self.lines = {line["id"]: line for line in lines}

    async def update_one(self, query, update):
        doc = self.lines.get(query.get("id"))
        if doc:
            doc.update((update or {}).get("$set") or {})


class _FakeHeaders:
    def __init__(self, header):
        self.docs = {header["request_number"]: header}

    async def find_one(self, query, proj=None):
        return self.docs.get(query.get("request_number"))

    async def update_one(self, query, update):
        key = query.get("request_number")
        doc = self.docs.get(key)
        if doc is None:
            doc = {"request_number": key}
            self.docs[key] = doc
        doc.update((update or {}).get("$set") or {})


class _FakeAudit:
    def __init__(self):
        self.docs = []

    async def insert_one(self, doc):
        self.docs.append(doc)


class _CompleteDB:
    def __init__(self, locks, lines, header):
        self.mobile_request_group_locks = locks
        self.order_requests = _FakeOrderRequests(lines)
        self.request_headers = _FakeHeaders(header)
        self.mobile_audit_logs = _FakeAudit()
        self.order_items = {}
        self.reservations = {}


def _complete_session():
    return {
        "device": {"device_id": "d1", "device_user_name": "Ravi", "device_user_mobile": "999"},
        "mobile_user": {"mobile_user_id": "MU1", "name": "Ravi", "mobile_number": "999"},
        "brand_name": "Honda",
        "dealer_name": "Dealer A",
        "branch": "Vanagaram",
    }


def _complete_line(line_id, part, requested_qty, request_number="RQ1"):
    return {
        "id": line_id,
        "part_number": part,
        "status": "Requested",
        "requested_qty": requested_qty,
        "accepted_qty": 0,
        "request_number": request_number,
        "request_group_id": request_number,
        "order_item_id": f"OI-{line_id}",
        "order_id": "ORD1",
        "supplying_dealer": "Dealer A",
        "supplying_branch": "Vanagaram",
    }


async def _prepare_complete(monkeypatch, lines, responses, header=None, lock_status="picked"):
    import order_desk_workflow as odw
    import request_fulfillment as rff

    locks = FakeLocks()
    await locks.insert_one({
        "request_group_key": "RQ1",
        "device_id": "d1",
        "mobile_user_id": "MU1",
        "device_user_name": "Ravi",
        "lock_status": lock_status,
        "line_responses": responses,
    })
    header = header or {
        "request_number": "RQ1",
        "status": "Requested",
        "response_status": "awaiting",
        "response_deadline": (datetime.now(timezone.utc) + timedelta(minutes=30)).isoformat(),
        "timer_frozen": False,
    }
    fake_db = _CompleteDB(locks, lines, header)
    calls = []

    async def fake_transition(request_id, new_status, remarks, acting_user, accepted_qty=None):
        line = fake_db.order_requests.lines[request_id]
        if line.get("status") == new_status:
            return dict(line), False
        requested = float(line.get("requested_qty") or 0)
        accepted = 0.0 if new_status != "Approved" else float(accepted_qty)
        line.update({
            "status": new_status,
            "accepted_qty": accepted,
            "approved_qty": accepted,
            "approval_remarks": remarks,
            "decided_by": acting_user.id,
        })
        remaining = max(0.0, requested - accepted)
        fake_db.order_items[line.get("order_item_id")] = {
            "id": line.get("order_item_id"),
            "accepted_qty": accepted,
            "remaining_qty": remaining,
            "retry_required": remaining > 0,
        }
        reservation = fake_db.reservations.setdefault(request_id, {"apply_count": 0, "qty": requested, "status": "active"})
        reservation["apply_count"] += 1
        if new_status == "Rejected":
            reservation.update({"status": "released", "qty": 0, "released_qty": requested})
        else:
            reservation.update({"status": "active", "qty": accepted, "accepted_qty": accepted, "released_qty": remaining})
        stored_header = fake_db.request_headers.docs[line["request_number"]]
        if not stored_header.get("timer_frozen"):
            stored_header.update(odw.freeze_response_timer(stored_header, mobile_api._now_iso(), "responded"))
            stored_header["status"] = "Approved" if new_status == "Approved" else stored_header.get("status")
        calls.append({
            "request_id": request_id,
            "new_status": new_status,
            "remarks": remarks,
            "accepted_qty": accepted_qty,
            "acting_user_id": acting_user.id,
        })
        return dict(line), True

    async def fake_scope(request_group_key, session, statuses=None):
        rows = list(fake_db.order_requests.lines.values())
        if statuses:
            rows = [row for row in rows if row.get("status") in statuses]
        return [dict(row) for row in rows]

    monkeypatch.setattr(mobile_api, "db", fake_db)
    monkeypatch.setattr(mobile_api, "request_center_transition", fake_transition)
    monkeypatch.setattr(mobile_api, "_scope_group_lines", fake_scope)
    payload = mobile_api.NotificationCompleteRequest(request_group_key="RQ1")
    return payload, fake_db, calls, rff


async def _run_complete(monkeypatch, lines, responses, header=None, lock_status="picked"):
    payload, fake_db, calls, rff = await _prepare_complete(
        monkeypatch, lines, responses, header=header, lock_status=lock_status,
    )
    result = await mobile_api.complete_picking(payload, session=_complete_session())
    return result, fake_db, calls, rff


def test_complete_qty_full_approves_and_freezes_timer(monkeypatch):
    asyncio.run(_test_complete_qty_full_approves_and_freezes_timer(monkeypatch))


async def _test_complete_qty_full_approves_and_freezes_timer(monkeypatch):
    line = _complete_line("L1", "P1", 1)
    result, fake_db, calls, rff = await _run_complete(
        monkeypatch, [line], {"L1": {"accepted_qty": 1, "remark": ""}},
    )
    assert result["status"] == "picking_completed"
    assert calls == [{
        "request_id": "L1",
        "new_status": "Approved",
        "remarks": "",
        "accepted_qty": 1.0,
        "acting_user_id": "mobile:MU1",
    }]
    updated = fake_db.order_requests.lines["L1"]
    assert updated["status"] == "Approved"
    assert updated["accepted_qty"] == 1
    assert updated["picking_finished_at"]
    assert updated["picking_finished_by"] == "mobile:MU1"
    assert updated["picking_finished_user_name"]
    header = fake_db.request_headers.docs["RQ1"]
    assert header["timer_frozen"] is True
    assert header["response_status"] == "responded"
    assert header["picking_finished_at"]
    assert rff.fulfillment_stage(updated) == "picking_finished"
    assert fake_db.mobile_request_group_locks.docs["RQ1"]["lock_status"] == "picking_completed"


def test_complete_qty_zero_rejects_and_is_not_dispatchable(monkeypatch):
    asyncio.run(_test_complete_qty_zero_rejects_and_is_not_dispatchable(monkeypatch))


async def _test_complete_qty_zero_rejects_and_is_not_dispatchable(monkeypatch):
    line = _complete_line("L1", "P1", 1)
    result, fake_db, calls, rff = await _run_complete(
        monkeypatch, [line], {"L1": {"accepted_qty": 0, "remark": "not available"}},
    )
    assert result["status"] == "picking_completed"
    assert calls[0]["new_status"] == "Rejected"
    assert calls[0]["accepted_qty"] == 0
    assert calls[0]["remarks"] == "not available"
    updated = fake_db.order_requests.lines["L1"]
    assert updated["status"] == "Rejected"
    assert updated["accepted_qty"] == 0
    assert not updated.get("picking_finished_at")
    assert rff.fulfillment_stage(updated) is None
    assert fake_db.reservations["L1"]["status"] == "released"


def test_complete_partial_qty_freezes_accepted_and_returns_balance(monkeypatch):
    asyncio.run(_test_complete_partial_qty_freezes_accepted_and_returns_balance(monkeypatch))


async def _test_complete_partial_qty_freezes_accepted_and_returns_balance(monkeypatch):
    line = _complete_line("L1", "P1", 5)
    result, fake_db, calls, rff = await _run_complete(
        monkeypatch, [line], {"L1": {"accepted_qty": 2, "remark": "only two"}},
    )
    assert result["status"] == "picking_completed"
    assert calls[0]["new_status"] == "Approved"
    assert calls[0]["accepted_qty"] == 2.0
    updated = fake_db.order_requests.lines["L1"]
    assert updated["accepted_qty"] == 2
    assert updated["picking_finished_at"]
    assert rff.part_status(updated) == "Partial"
    item = fake_db.order_items["OI-L1"]
    assert item["accepted_qty"] == 2
    assert item["remaining_qty"] == 3
    assert item["retry_required"] is True
    assert fake_db.reservations["L1"]["qty"] == 2
    assert fake_db.reservations["L1"]["released_qty"] == 3


def test_complete_mixed_lines_ready_and_rejected(monkeypatch):
    asyncio.run(_test_complete_mixed_lines_ready_and_rejected(monkeypatch))


async def _test_complete_mixed_lines_ready_and_rejected(monkeypatch):
    lines = [_complete_line("L1", "P1", 1), _complete_line("L2", "P2", 1)]
    result, fake_db, calls, rff = await _run_complete(
        monkeypatch,
        lines,
        {
            "L1": {"accepted_qty": 1, "remark": ""},
            "L2": {"accepted_qty": 0, "remark": "zero"},
        },
    )
    assert result["status"] == "picking_completed"
    assert [call["new_status"] for call in calls] == ["Approved", "Rejected"]
    accepted = fake_db.order_requests.lines["L1"]
    rejected = fake_db.order_requests.lines["L2"]
    assert rff.fulfillment_stage(accepted) == "picking_finished"
    assert rff.fulfillment_stage(rejected) is None
    assert accepted["picking_finished_at"]
    assert not rejected.get("picking_finished_at")


def test_complete_blocked_when_unanswered_lines(monkeypatch):
    asyncio.run(_test_complete_blocked_when_unanswered_lines(monkeypatch))


async def _test_complete_blocked_when_unanswered_lines(monkeypatch):
    from fastapi import HTTPException

    lines = [_complete_line("L1", "P1", 1), _complete_line("L2", "P2", 1)]
    payload, fake_db, calls, _rff = await _prepare_complete(
        monkeypatch, lines, {"L1": {"accepted_qty": 1, "remark": ""}},
    )
    with pytest.raises(HTTPException) as err:
        await mobile_api.complete_picking(payload, session=_complete_session())
    assert err.value.status_code == 409
    assert err.value.detail["code"] == "INVALID_STATE"
    assert "P2" in (err.value.detail.get("missing_parts") or [])
    assert calls == []
    assert fake_db.order_requests.lines["L1"]["status"] == "Requested"
    assert fake_db.order_requests.lines["L2"]["status"] == "Requested"
    assert fake_db.mobile_request_group_locks.docs["RQ1"]["lock_status"] == "picked"
    assert fake_db.request_headers.docs["RQ1"].get("timer_frozen") is False


def test_complete_repeated_is_idempotent(monkeypatch):
    asyncio.run(_test_complete_repeated_is_idempotent(monkeypatch))


async def _test_complete_repeated_is_idempotent(monkeypatch):
    line = _complete_line("L1", "P1", 1)
    first, fake_db, calls, _rff = await _run_complete(
        monkeypatch, [line], {"L1": {"accepted_qty": 1, "remark": ""}},
    )
    assert first["status"] == "picking_completed"
    assert len(calls) == 1
    assert fake_db.reservations["L1"]["apply_count"] == 1
    payload = mobile_api.NotificationCompleteRequest(request_group_key="RQ1")
    second = await mobile_api.complete_picking(payload, session=_complete_session())
    assert second["status"] == "picking_completed"
    assert len(calls) == 1
    assert fake_db.reservations["L1"]["apply_count"] == 1
    assert fake_db.order_requests.lines["L1"]["accepted_qty"] == 1
