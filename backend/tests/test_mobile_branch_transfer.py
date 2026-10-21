"""Branch-transfer session revocation and mobile API rejection."""
import asyncio
import hashlib
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

import mobile_api as ma
import mobile_push


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


class FakeColl:
    def __init__(self, rows=None):
        self.rows = [dict(r) for r in (rows or [])]
        self.updates = []

    async def find_one(self, query, proj=None):
        for row in self.rows:
            if all(row.get(key) == value for key, value in (query or {}).items()):
                return dict(row)
        return None

    async def update_one(self, query, update):
        self.updates.append((query, update))
        for row in self.rows:
            if all(row.get(key) == value for key, value in (query or {}).items()):
                row.update((update or {}).get("$set") or {})
                return SimpleNamespace(matched_count=1, modified_count=1)
        return SimpleNamespace(matched_count=0, modified_count=0)

    async def update_many(self, query, update):
        n = 0
        for row in self.rows:
            if all(row.get(key) == value for key, value in (query or {}).items()):
                row.update((update or {}).get("$set") or {})
                n += 1
        return SimpleNamespace(matched_count=n, modified_count=n)

    async def delete_many(self, query):
        before = len(self.rows)
        self.rows = [row for row in self.rows if not all(row.get(key) == value for key, value in (query or {}).items())]
        return SimpleNamespace(deleted_count=before - len(self.rows))

    def find(self, query, proj=None):
        outer = self

        class _Cursor:
            async def to_list(self, n):
                return [
                    dict(row)
                    for row in outer.rows
                    if all(row.get(key) == value for key, value in (query or {}).items() if key != "push_token")
                    and (not query or "push_token" not in query or row.get("push_token"))
                ]

        return _Cursor()


def test_mobile_app_allows_only_testing_and_production_urls():
    env = Path(__file__).resolve().parents[2] / "mobile" / "src" / "config" / "env.js"
    src = env.read_text()
    assert "https://testing.sleepingstock.in/api" in src
    assert "https://api.sleepingstock.in/api" in src
    assert "ALLOWED_API_BASE_URLS" in src
    assert "PRODUCTION_API_BASE_URL" in src
    app = Path(__file__).resolve().parents[2] / "mobile" / "App.js"
    app_src = app.read_text()
    assert "PRODUCTION_API_BASE_URL" in app_src
    assert "pairWithManualCode" in app_src
    assert "login-background.jpg" in app_src
    assert "sleeping-stock-logo-transparent.png" in app_src
    settings = Path(__file__).resolve().parents[2] / "frontend" / "src" / "pages" / "NMTSMobile.js"
    settings_src = settings.read_text()
    assert "Notification Repeat Interval" not in settings_src
    assert "notification-interval" not in settings_src


def test_branch_transferred_helpers():
    assert ma.BRANCH_TRANSFERRED_MESSAGE == "Your branch has been transferred. Please re-pair your device to continue."
    detail = ma.branch_transferred_detail()
    assert detail["code"] == "BRANCH_TRANSFERRED"
    assert detail["re_pair_required"] is True
    assert detail["message"] == ma.BRANCH_TRANSFERRED_MESSAGE
    assert ma.is_branch_changed_device({"inactive_reason": "branch_changed"})
    assert not ma.is_branch_changed_device({"inactive_reason": "repaired"})
    assert not ma.is_branch_changed_device(None)
    assert ma.session_predates_branch_change(
        {"created_at": "2026-10-01T10:00:00"},
        {"branch_changed_at": "2026-10-11T12:00:00"},
    )
    assert not ma.session_predates_branch_change(
        {"created_at": "2026-10-11T13:00:00"},
        {"branch_changed_at": "2026-10-11T12:00:00"},
    )
    with pytest.raises(HTTPException) as exc:
        ma.raise_branch_transferred()
    assert exc.value.status_code == 401
    assert exc.value.detail["code"] == "BRANCH_TRANSFERRED"


def test_branch_transferred_push_payload():
    message = mobile_push.build_branch_transferred_push_message(
        "ExponentPushToken[abc]",
        from_branch="Vanagaram",
        to_branch="Koyambedu",
        mobile_user_id="MUVAN1",
    )
    assert message["body"] == ma.BRANCH_TRANSFERRED_MESSAGE
    assert message["data"]["type"] == "branch_transferred"
    assert message["data"]["code"] == "BRANCH_TRANSFERRED"
    assert message["data"]["re_pair_required"] is True
    assert message["data"]["screen"] == "pair"
    assert message["data"]["from_branch"] == "Vanagaram"
    assert message["data"]["to_branch"] == "Koyambedu"


def test_get_device_session_rejects_deleted_session_after_transfer():
    asyncio.run(_test_get_device_session_rejects_deleted_session_after_transfer())


async def _test_get_device_session_rejects_deleted_session_after_transfer():
    token = "old-session-token"
    token_hash = _hash(token)
    devices = FakeColl([{
        "device_id": "dev-1",
        "mobile_user_id": "MU1",
        "session_token_hash": token_hash,
        "status": "inactive",
        "session_active": False,
        "inactive_reason": "branch_changed",
        "brand_name": "Hyundai",
        "dealer_name": "KUN",
        "branch": "Vanagaram",
    }])
    sessions = FakeColl([])
    users = FakeColl([{
        "mobile_user_id": "MU1",
        "status": "active",
        "brand_name": "Hyundai",
        "dealer_name": "KUN",
        "branch": "Koyambedu",
        "branch_changed_at": "2026-10-11T12:00:00",
    }])
    ma.db = SimpleNamespace(mobile_sessions=sessions, mobile_devices=devices, mobile_users=users)
    with pytest.raises(HTTPException) as exc:
        await ma.get_device_session(authorization=f"Bearer {token}")
    assert exc.value.status_code == 401
    assert exc.value.detail["code"] == "BRANCH_TRANSFERRED"
    assert exc.value.detail["message"] == ma.BRANCH_TRANSFERRED_MESSAGE
    assert devices.updates == []


def test_get_device_session_rejects_leftover_session_and_does_not_reactivate():
    asyncio.run(_test_get_device_session_rejects_leftover_session_and_does_not_reactivate())


async def _test_get_device_session_rejects_leftover_session_and_does_not_reactivate():
    token = "leftover-token"
    token_hash = _hash(token)
    devices = FakeColl([{
        "device_id": "dev-2",
        "mobile_user_id": "MU2",
        "session_token_hash": token_hash,
        "status": "inactive",
        "session_active": False,
        "inactive_reason": "branch_changed",
    }])
    sessions = FakeColl([{
        "session_token_hash": token_hash,
        "device_id": "dev-2",
        "mobile_user_id": "MU2",
        "created_at": "2026-10-01T10:00:00",
    }])
    users = FakeColl([{
        "mobile_user_id": "MU2",
        "status": "active",
        "branch_changed_at": "2026-10-11T12:00:00",
    }])
    ma.db = SimpleNamespace(mobile_sessions=sessions, mobile_devices=devices, mobile_users=users)
    with pytest.raises(HTTPException) as exc:
        await ma.get_device_session(authorization=f"Bearer {token}")
    assert exc.value.detail["code"] == "BRANCH_TRANSFERRED"
    assert devices.updates == []
    assert devices.rows[0]["session_active"] is False


def test_get_device_session_rejects_session_older_than_branch_change():
    asyncio.run(_test_get_device_session_rejects_session_older_than_branch_change())


async def _test_get_device_session_rejects_session_older_than_branch_change():
    token = "stale-token"
    token_hash = _hash(token)
    devices = FakeColl([{
        "device_id": "dev-3",
        "mobile_user_id": "MU3",
        "session_token_hash": token_hash,
        "status": "active",
        "session_active": True,
        "inactive_reason": None,
    }])
    sessions = FakeColl([{
        "session_token_hash": token_hash,
        "device_id": "dev-3",
        "mobile_user_id": "MU3",
        "created_at": "2026-10-01T10:00:00",
    }])
    users = FakeColl([{
        "mobile_user_id": "MU3",
        "status": "active",
        "branch": "Koyambedu",
        "branch_changed_at": "2026-10-11T12:00:00",
    }])
    ma.db = SimpleNamespace(mobile_sessions=sessions, mobile_devices=devices, mobile_users=users)
    with pytest.raises(HTTPException) as exc:
        await ma.get_device_session(authorization=f"Bearer {token}")
    assert exc.value.detail["code"] == "BRANCH_TRANSFERRED"
    assert devices.updates == []


def test_notify_branch_transferred_is_best_effort(monkeypatch):
    asyncio.run(_test_notify_branch_transferred_is_best_effort(monkeypatch))


async def _test_notify_branch_transferred_is_best_effort(monkeypatch):
    sent = []
    logs = []

    class Devices:
        def find(self, query, proj=None):
            class _Cursor:
                async def to_list(self, n):
                    return [{
                        "device_id": "dev-1",
                        "push_token": "ExponentPushToken[ok]",
                        "mobile_user_id": "MU1",
                    }]
            return _Cursor()

    class Logs:
        async def insert_one(self, doc):
            logs.append(doc)

    monkeypatch.setattr(mobile_push, "send_expo_push_messages", lambda messages: sent.extend(messages) or {"ok": True, "sent": len(messages)})
    result = await mobile_push.notify_branch_transferred(
        SimpleNamespace(mobile_devices=Devices(), mobile_push_delivery_logs=Logs()),
        mobile_user_id="MU1",
        from_branch="Vanagaram",
        to_branch="Koyambedu",
    )
    assert result["sent"] == 1
    assert sent[0]["data"]["type"] == "branch_transferred"
    assert sent[0]["body"] == ma.BRANCH_TRANSFERRED_MESSAGE
    assert logs and logs[0]["status"] == "sent"


def test_transfer_source_revokes_sessions_and_does_not_switch_old_device_branch():
    src = __import__("pathlib").Path(ma.__file__).read_text()
    start = src.index("async def change_mobile_user_branch")
    chunk = src[start:start + 9000]
    assert "notify_branch_transferred" in chunk
    assert "mobile_sessions.delete_many" in chunk
    assert '"inactive_reason": "branch_changed"' in chunk
    assert '"session_active": False' in chunk
    assert '"session_revoked_at": now' in chunk
    assert "raise_branch_transferred" in src
    session_fn = src[src.index("async def get_device_session"):src.index("async def get_device_session") + 2800]
    assert "session_token_hash" in session_fn
    assert "is_branch_changed_device" in session_fn
