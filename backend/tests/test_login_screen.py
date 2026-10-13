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


def test_require_master_allows_production_master(monkeypatch):
    monkeypatch.setattr(ls.testing_runtime, "is_testing_env", lambda: False)
    ls.require_master(SimpleNamespace(role="master"))


def test_require_master_blocks_non_master(monkeypatch):
    monkeypatch.setattr(ls.testing_runtime, "is_testing_env", lambda: False)
    with pytest.raises(HTTPException) as exc:
        ls.require_master(SimpleNamespace(role="admin"))
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


def test_production_choose_active_returns_default(monkeypatch):
    monkeypatch.setattr(ls.testing_runtime, "is_testing_env", lambda: False)

    async def run():
        payload = await ls.active_payload(FakeDB([
            {"id": "d1", "type": "default", "storage_key": "dev/login-screen/default/a.png"},
        ]))
        assert payload["id"] == "d1"
        assert payload["image_url"] == "/api/login-screen/image/d1"

    import asyncio
    asyncio.get_event_loop().run_until_complete(run())


def test_production_does_not_delete_testing_keys(monkeypatch):
    monkeypatch.setattr(ls.testing_runtime, "is_testing_env", lambda: False)
    now = datetime(2026, 10, 11, 13, 0, tzinfo=IST)
    monkeypatch.setattr(ls, "_now_ist", lambda: now)
    deleted = []

    class Storage:
        def delete(self, key):
            deleted.append(key)

    monkeypatch.setattr(ls.s3_storage, "get_storage", lambda: Storage())
    db = FakeDB([
        {"id": "d1", "type": "default", "storage_key": "dev/login-screen/default/a.png"},
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
        assert deleted == []

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


def _box(typ: bytes, payload: bytes) -> bytes:
    return (8 + len(payload)).to_bytes(4, "big") + typ + payload


def _mdhd(timescale=1000, duration_ms=10000):
    payload = (
        b"\x00\x00\x00\x00"
        + b"\x00" * 8
        + int(timescale).to_bytes(4, "big")
        + int(duration_ms).to_bytes(4, "big")
        + b"\x15\xc7\x00\x00"
    )
    return _box(b"mdhd", payload)


def _hdlr(handler=b"vide"):
    payload = b"\x00\x00\x00\x00" + b"\x00" * 4 + handler + b"\x00" * 12 + b"VideoHandler\x00"
    return _box(b"hdlr", payload)


def _stsd(codec=b"avc1"):
    extra = b"\x00" * 6 + (1).to_bytes(2, "big") + b"\x00" * (70 if codec in (b"avc1", b"avc3") else 20)
    payload = b"\x00\x00\x00\x00" + (1).to_bytes(4, "big") + _box(codec, extra)
    return _box(b"stsd", payload)


def _trak(handler, codec, duration_ms=10000):
    mdia = _box(b"mdia", _mdhd(1000, duration_ms) + _hdlr(handler) + _box(b"minf", _box(b"stbl", _stsd(codec))))
    return _box(b"trak", mdia)


def build_mp4(duration_ms=10000, video_codec=b"avc1", audio_codec=b"mp4a", with_audio=True):
    ftyp = _box(b"ftyp", b"isom" + (0).to_bytes(4, "big") + b"isomiso2mp41")
    tracks = _trak(b"vide", video_codec, duration_ms)
    if with_audio:
        tracks += _trak(b"soun", audio_codec, duration_ms)
    return ftyp + _box(b"moov", tracks) + _box(b"mdat", b"\x00" * 16)


def test_manage_payload_returns_same_active_as_public(monkeypatch):
    monkeypatch.setattr(ls.testing_runtime, "is_testing_env", lambda: True)
    now = datetime(2026, 10, 13, 10, 0, tzinfo=IST)
    monkeypatch.setattr(ls, "_now_ist", lambda: now)
    db = FakeDB([
        {"id": "d1", "type": "default", "storage_key": "testing/login-screen/default/a.png"},
        {
            "id": "s1",
            "type": "scheduled",
            "storage_key": "testing/login-screen/scheduled/b.png",
            "start_at": "2026-10-13T09:00:00+05:30",
            "end_at": "2026-10-13T12:00:00+05:30",
        },
    ])

    async def run():
        manage = await ls.manage_payload(db)
        active = await ls.active_payload(db)
        assert manage["active"]["id"] == "s1"
        assert active["id"] == "s1"
        assert manage["active"]["media_url"] == active["media_url"]
        assert manage["default"]["id"] == "d1"
        assert manage["scheduled"]["id"] == "s1"

    import asyncio
    asyncio.get_event_loop().run_until_complete(run())


def test_manage_payload_survives_unparseable_scheduled_dates(monkeypatch):
    monkeypatch.setattr(ls.testing_runtime, "is_testing_env", lambda: True)
    now = datetime(2026, 10, 13, 10, 0, tzinfo=IST)
    monkeypatch.setattr(ls, "_now_ist", lambda: now)
    db = FakeDB([
        {"id": "d1", "type": "default", "storage_key": "testing/login-screen/default/a.png"},
        {"id": "s1", "type": "scheduled", "storage_key": "testing/login-screen/scheduled/b.png", "start_at": "not-a-date", "end_at": "also-bad"},
    ])

    async def run():
        manage = await ls.manage_payload(db)
        active = await ls.active_payload(db)
        assert manage["default"]["id"] == "d1"
        assert active["id"] == "d1"
        assert any(r.get("id") == "s1" for r in db.login_screen_images.rows)

    import asyncio
    asyncio.get_event_loop().run_until_complete(run())


def test_public_row_exposes_video_media_and_poster():
    payload = ls._public_row({
        "id": "v1",
        "type": "default",
        "media_kind": "video",
        "poster_key": "testing/login-screen/default/x-poster.jpg",
        "storage_key": "testing/login-screen/default/x.mp4",
        "content_type": "video/mp4",
        "display_settings": {"fit": "contain", "zoom": 1.4, "offset_x": 0.1, "offset_y": -0.2},
    })
    assert payload["media_kind"] == "video"
    assert payload["media_url"] == "/api/login-screen/image/v1"
    assert payload["poster_url"] == "/api/login-screen/poster/v1"
    assert payload["image_url"] == "/api/login-screen/poster/v1"
    assert payload["display_settings"]["zoom"] == 1.4
    assert payload["display_settings"]["fit"] == "contain"


def test_normalize_display_settings_clamps_values():
    out = ls.normalize_display_settings('{"fit":"cover","zoom":9,"offset_x":-3,"offset_y":2}')
    assert out["fit"] == "cover"
    assert out["zoom"] == 3.0
    assert out["offset_x"] == -1.0
    assert out["offset_y"] == 1.0


def test_video_validation_accepts_h264_aac_under_limits():
    data = build_mp4(duration_ms=12000)
    ext, ctype, duration = ls._validate_video_bytes(data, "video/mp4", "promo.mp4")
    assert ext == "mp4"
    assert ctype == "video/mp4"
    assert 11.9 <= duration <= 12.1


def test_video_validation_rejects_over_30_seconds():
    data = build_mp4(duration_ms=31000)
    with pytest.raises(HTTPException) as exc:
        ls._validate_video_bytes(data, "video/mp4", "long.mp4")
    assert exc.value.status_code == 400
    assert "30 seconds" in str(exc.value.detail)


def test_video_validation_rejects_over_20mb():
    data = build_mp4(duration_ms=5000) + (b"\x00" * (ls.MAX_VIDEO_BYTES + 1))
    with pytest.raises(HTTPException) as exc:
        ls._validate_video_bytes(data, "video/mp4", "huge.mp4")
    assert exc.value.status_code == 400
    assert "20 MB" in str(exc.value.detail)


def test_video_validation_rejects_hevc():
    data = build_mp4(duration_ms=5000, video_codec=b"hvc1")
    with pytest.raises(HTTPException) as exc:
        ls._validate_video_bytes(data, "video/mp4", "hevc.mp4")
    assert exc.value.status_code == 400


def test_video_validation_rejects_non_aac_audio():
    data = build_mp4(duration_ms=5000, audio_codec=b"sowt")
    with pytest.raises(HTTPException) as exc:
        ls._validate_video_bytes(data, "video/mp4", "pcm.mp4")
    assert exc.value.status_code == 400


def test_video_save_requires_poster(monkeypatch):
    monkeypatch.setattr(ls.testing_runtime, "is_testing_env", lambda: True)
    data = build_mp4(duration_ms=4000)
    with pytest.raises(HTTPException) as exc:
        import asyncio
        asyncio.get_event_loop().run_until_complete(
            ls.save_default(FakeDB(), data, "video/mp4", "promo.mp4")
        )
    assert exc.value.status_code == 400
    assert "poster" in str(exc.value.detail).lower()
