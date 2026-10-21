"""Configurable mobile confirm window (default 50 minutes) + expiry release."""
from __future__ import annotations

import asyncio
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

import mobile_request_claim as claim  # noqa: E402
import mobile_api  # noqa: E402
from fastapi import HTTPException  # noqa: E402


class _Settings:
    def __init__(self, row=None):
        self.row = row

    async def find_one(self, query):
        if self.row and self.row.get("_id") == query.get("_id"):
            return dict(self.row)
        return None


class _FakeDB:
    def __init__(self, settings_row=None):
        self.mobile_settings = _Settings(settings_row)


def test_default_confirm_window_is_50_minutes():
    assert claim.DEFAULT_CONFIRM_WINDOW_MINUTES == 50
    assert claim.CONFIRM_WINDOW_SECONDS == 3000
    deadline = datetime.fromisoformat(claim.confirm_deadline_iso())
    delta = deadline - datetime.now(timezone.utc)
    assert 2990 <= delta.total_seconds() <= 3010


def test_resolve_confirm_window_from_settings_minutes():
    async def _run():
        db = _FakeDB({"_id": "mobile_confirm_window_minutes", "minutes": 10})
        assert await claim.resolve_confirm_window_seconds(db) == 600
        db2 = _FakeDB(None)
        assert await claim.resolve_confirm_window_seconds(db2) == 3000
    asyncio.run(_run())


def test_existing_lock_keeps_stored_confirm_window_seconds():
    lock = {
        "lock_status": "picked",
        "confirm_window_seconds": 50,
        "confirm_deadline_at": (datetime.now(timezone.utc) + timedelta(seconds=20)).isoformat(),
    }
    assert not claim.is_confirm_expired(lock)
    assert lock["confirm_window_seconds"] == 50


def test_expiry_releases_ownership():
    async def _run():
        class Locks:
            def __init__(self):
                self.docs = [{
                    "request_group_key": "RQ-EXP",
                    "lock_status": "picked",
                    "device_id": "d1",
                    "mobile_user_id": "u1",
                    "dealer_name": "Dealer A",
                    "branch": "Koyambedu",
                    "confirm_deadline_at": (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat(),
                    "confirm_window_seconds": 50,
                }]
                self.events = []

            async def find_one_and_delete(self, query):
                for i, d in enumerate(self.docs):
                    if d.get("request_group_key") == query.get("request_group_key"):
                        return self.docs.pop(i)
                return None

        class Events:
            def __init__(self, locks):
                self.locks = locks

            async def insert_one(self, doc):
                self.locks.events.append(doc)

        locks = Locks()

        class DB:
            mobile_request_group_locks = locks
            mobile_request_claim_events = Events(locks)

        lock = dict(locks.docs[0])
        assert claim.is_confirm_expired(lock)
        released = await claim.release_expired_claim(DB(), lock)
        assert released is not None
        assert locks.docs == []
        assert locks.events and locks.events[0]["event"] == "confirm_timeout_release"

    asyncio.run(_run())


def test_accept_pick_deadline_about_50_minutes(monkeypatch):
    """accept_notification returns ~50 min confirm_deadline_at with default settings."""
    from tests.test_mobile_request_claim_harness import (
        FakeActions,
        FakeAudit,
        FakeHeaders,
        FakeLocks,
        FakeOrderRequests,
        _live_line,
        _session,
    )
    import mobile_push

    async def _run():
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
            mobile_settings = _Settings(None)

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
        assert first.get("confirm_window_seconds") == 3000
        deadline = datetime.fromisoformat(str(first["confirm_deadline_at"]).replace("Z", "+00:00"))
        if deadline.tzinfo is None:
            deadline = deadline.replace(tzinfo=timezone.utc)
        delta = (deadline - datetime.now(timezone.utc)).total_seconds()
        assert 2900 <= delta <= 3100, f"expected ~50 min ahead, got {delta}s"

    asyncio.run(_run())
