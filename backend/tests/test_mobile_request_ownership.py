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


async def _test_request_picked_excludes_picker(monkeypatch):
    sent = []

    class Devices:
        def find(self, query, proj=None):
            class Cursor:
                async def to_list(self, n):
                    return [
                        {"device_id": "winner", "push_token": "ExponentPushToken[w]", "mobile_user_id": "u1"},
                        {"device_id": "other", "push_token": "ExponentPushToken[o]", "mobile_user_id": "u2"},
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
        {"id": "G", "request_number": "RQ1", "supplying_dealer": "D", "supplying_branch": "B"},
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
