"""Login Screen Manager selection, expiry, and access guards. No live S3."""
from datetime import datetime, timedelta
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest
from fastapi import HTTPException

import login_screen as ls

IST = ZoneInfo("Asia/Kolkata")


class FakeCursor:
    def __init__(self, rows):
        self.rows = list(rows)

    async def to_list(self, n):
        return self.rows[: int(n)]


class FakeCollection:
    def __init__(self, rows=None):
        self.rows = list(rows or [])

    def find(self, query=None, projection=None):
        return FakeCursor([r for r in self.rows if _match(r, query or {})])

    async def find_one(self, query=None, projection=None):
        for r in self.rows:
            if _match(r, query or {}):
                return dict(r)
        return None

    async def insert_one(self, doc):
        self.rows.append(dict(doc))

    async def delete_one(self, query):
        before = len(self.rows)
        self.rows = [r for r in self.rows if not _match(r, query or {})]
        return SimpleNamespace(deleted_count=before - len(self.rows))


class FakeDB:
    def __init__(self, rows=None):
        self.login_screen_images = FakeCollection(rows)

    def __getitem__(self, name):
        return self.login_screen_images


def _match(row, query):
    for key, value in (query or {}).items():
        if row.get(key) != value:
            return False
    return True


def test_require_testing_master_blocks_production(monkeypatch):
    monkeypatch.setattr(ls.testing_runtime, "is_testing_env", lambda: False)
    with pytest.raises(HTTPException) as exc:
        ls.require_testing_master(SimpleNamespace(role="master"))
    assert exc.value.status_code == 404


def test_require_testing_master_blocks_non_master(monkeypatch):
    monkeypatch.setattr(ls.testing_runtime, "is_testing_env", lambda: True)
    with pytest.raises(HTTPException) as exc:
        ls.require_testing_master(SimpleNamespace(role="admin"))
    assert exc.value.status_code == 403


def test_choose_active_prefers_scheduled_window(monkeypatch):
    monkeypatch.setattr(ls.testing_runtime, "is_testing_env", lambda: True)
    now = datetime(2026, 10, 11, 10, 0, tzinfo=IST)
    monkeypatch.setattr(ls, "_now_ist", lambda: now)
    db = FakeDB([
        {"id": "d1", "type": "default", "storage_key": "testing/login-screen/default/a.png"},
        {
            "id": "s1",
            "type": "scheduled",
            "storage_key": "testing/login-screen/scheduled/b.png",
            "start_at": "2026-10-11T09:00:00+05:30",
            "end_at": "2026-10-11T12:00:00+05:30",
        },
    ])

    async def run():
        row = await ls.choose_active(db)
        assert row["id"] == "s1"

    import asyncio
    asyncio.get_event_loop().run_until_complete(run())


def test_choose_active_returns_default_after_expiry_and_deletes(monkeypatch):
    monkeypatch.setattr(ls.testing_runtime, "is_testing_env", lambda: True)
    now = datetime(2026, 10, 11, 13, 0, tzinfo=IST)
    monkeypatch.setattr(ls, "_now_ist", lambda: now)
    deleted = []

    class Storage:
        def delete(self, key):
            deleted.append(key)

    monkeypatch.setattr(ls.s3_storage, "get_storage", lambda: Storage())
    db = FakeDB([
        {"id": "d1", "type": "default", "storage_key": "testing/login-screen/default/a.png"},
        {
            "id": "s1",
            "type": "scheduled",
            "storage_key": "testing/login-screen/scheduled/b.png",
            "start_at": (now - timedelta(hours=3)).isoformat(),
            "end_at": (now - timedelta(minutes=1)).isoformat(),
        },
    ])

    async def run():
        row = await ls.choose_active(db)
        assert row["id"] == "d1"
        assert not any(r.get("type") == "scheduled" for r in db.login_screen_images.rows)
        assert deleted == ["testing/login-screen/scheduled/b.png"]

    import asyncio
    asyncio.get_event_loop().run_until_complete(run())


def test_production_active_payload_is_empty(monkeypatch):
    monkeypatch.setattr(ls.testing_runtime, "is_testing_env", lambda: False)

    async def run():
        payload = await ls.active_payload(FakeDB([{"id": "d1", "type": "default"}]))
        assert payload["image_url"] is None

    import asyncio
    asyncio.get_event_loop().run_until_complete(run())


def test_end_must_be_after_start(monkeypatch):
    monkeypatch.setattr(ls.testing_runtime, "is_testing_env", lambda: True)
    with pytest.raises(HTTPException) as exc:
        import asyncio
        asyncio.get_event_loop().run_until_complete(
            ls.save_scheduled(
                FakeDB(),
                b"x",
                "image/png",
                "a.png",
                "2026-10-11T12:00:00+05:30",
                "2026-10-11T11:00:00+05:30",
            )
        )
    assert exc.value.status_code == 400
