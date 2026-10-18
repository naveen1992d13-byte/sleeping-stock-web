"""Login Screen Default/Scheduled image and video manager.

Testing writes testing/ keys into nmts_testing.
Production writes dev/ keys into nmts.
Never copies Testing records or Testing S3 keys into Production.
"""
from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime
from io import BytesIO
from typing import Any, Dict, Optional, Tuple
from zoneinfo import ZoneInfo

from fastapi import HTTPException
from fastapi.responses import StreamingResponse

try:
    from . import s3_storage
    from . import testing_runtime
except ImportError:
    import s3_storage
    import testing_runtime

logger = logging.getLogger("nmts.login_screen")
IST = ZoneInfo("Asia/Kolkata")
COLLECTION = "login_screen_images"
TYPE_DEFAULT = "default"
TYPE_SCHEDULED = "scheduled"
KIND_IMAGE = "image"
KIND_VIDEO = "video"
ALLOWED_IMAGE_CONTENT = {
    "image/png": "png",
    "image/jpeg": "jpg",
    "image/jpg": "jpg",
    "image/webp": "webp",
}
ALLOWED_VIDEO_CONTENT = {
    "video/mp4": "mp4",
    "application/mp4": "mp4",
}
ALLOWED_CONTENT = {**ALLOWED_IMAGE_CONTENT, **ALLOWED_VIDEO_CONTENT}
MAX_IMAGE_BYTES = 6 * 1024 * 1024
MAX_VIDEO_BYTES = 20 * 1024 * 1024
MAX_VIDEO_SECONDS = 30
MAX_BYTES = MAX_IMAGE_BYTES
H264_CODECS = {b"avc1", b"avc3"}
AAC_CODECS = {b"mp4a"}
REJECT_VIDEO_CODECS = {b"hvc1", b"hev1", b"vp09", b"av01", b"mp4v", b"av01"}
_MP4_CONTAINERS = frozenset({
    b"moov", b"trak", b"mdia", b"minf", b"stbl", b"edts", b"dinf",
    b"mvex", b"moof", b"traf",
})


def require_master(current_user) -> None:
    try:
        from . import roles as role_defs
    except ImportError:
        import roles as role_defs  # type: ignore
    if not role_defs.is_master_level(getattr(current_user, "role", None)):
        raise HTTPException(status_code=403, detail="Master Admin or Master only")


def require_testing_master(current_user) -> None:
    require_master(current_user)


def _data_origin() -> str:
    return "testing" if testing_runtime.is_testing_env() else "production"


def _key_allowed(key: str) -> bool:
    text = str(key or "")
    if testing_runtime.is_testing_env():
        return text.startswith("testing/")
    return text.startswith("dev/") and not text.startswith("testing/")


def _parse_ist(value: Optional[str]) -> Optional[datetime]:
    text = str(value or "").strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(text)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid date/time. Use IST.") from exc
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=IST)
    return dt.astimezone(IST)


def _try_parse_ist(value: Optional[str]) -> Optional[datetime]:
    try:
        return _parse_ist(value)
    except HTTPException:
        return None


def _now_ist() -> datetime:
    return datetime.now(IST)


def _iter_boxes(data: bytes, start: int = 0, end: Optional[int] = None):
    if end is None:
        end = len(data)
    pos = start
    while pos + 8 <= end:
        size = int.from_bytes(data[pos:pos + 4], "big")
        typ = data[pos + 4:pos + 8]
        header = 8
        if size == 1:
            if pos + 16 > end:
                break
            size = int.from_bytes(data[pos + 8:pos + 16], "big")
            header = 16
        elif size == 0:
            size = end - pos
        if size < header or pos + size > end:
            break
        yield typ, pos + header, pos + size
        pos += size


def _walk_boxes(data: bytes, start: int = 0, end: Optional[int] = None):
    if end is None:
        end = len(data)
    for typ, inner_start, inner_end in _iter_boxes(data, start, end):
        yield typ, inner_start, inner_end
        if typ in _MP4_CONTAINERS:
            yield from _walk_boxes(data, inner_start, inner_end)
        elif typ == b"stsd" and inner_end - inner_start >= 8:
            yield from _walk_boxes(data, inner_start + 8, inner_end)


def _mdhd_seconds(payload: bytes) -> Optional[float]:
    if len(payload) < 20:
        return None
    version = payload[0]
    try:
        if version == 1:
            if len(payload) < 32:
                return None
            timescale = int.from_bytes(payload[20:24], "big")
            duration = int.from_bytes(payload[24:32], "big")
        else:
            timescale = int.from_bytes(payload[12:16], "big")
            duration = int.from_bytes(payload[16:20], "big")
    except Exception:
        return None
    if timescale <= 0:
        return None
    return duration / float(timescale)


def _parse_mp4(data: bytes) -> Tuple[float, bool, bool, bool]:
    if len(data) < 16:
        raise HTTPException(status_code=400, detail="Upload an MP4 video with H.264 video and AAC audio")
    has_ftyp = False
    durations = []
    video_ok = False
    audio_ok = False
    audio_present = False
    unsupported = False
    for typ, inner_start, inner_end in _walk_boxes(data):
        if typ == b"ftyp":
            has_ftyp = True
        elif typ == b"mdhd":
            seconds = _mdhd_seconds(data[inner_start:inner_end])
            if seconds is not None:
                durations.append(seconds)
        elif typ == b"hdlr":
            payload = data[inner_start:inner_end]
            if len(payload) >= 12 and payload[8:12] == b"soun":
                audio_present = True
        elif typ in H264_CODECS:
            video_ok = True
        elif typ in AAC_CODECS:
            audio_ok = True
        elif typ in REJECT_VIDEO_CODECS:
            unsupported = True
    if not has_ftyp:
        raise HTTPException(status_code=400, detail="Upload an MP4 video with H.264 video and AAC audio")
    if unsupported or not video_ok:
        raise HTTPException(status_code=400, detail="Video must be MP4 with H.264 video")
    if audio_present and not audio_ok:
        raise HTTPException(status_code=400, detail="Video audio must be AAC")
    duration = max(durations) if durations else 0.0
    if duration <= 0:
        raise HTTPException(status_code=400, detail="Video duration could not be read")
    if duration > MAX_VIDEO_SECONDS + 0.05:
        raise HTTPException(status_code=400, detail="Video must be 30 seconds or shorter")
    return duration, video_ok, audio_ok, audio_present


def _guess_kind(content_type: Optional[str], filename: Optional[str]) -> str:
    ctype = str(content_type or "").split(";")[0].strip().lower()
    name = str(filename or "").lower()
    if ctype in ALLOWED_VIDEO_CONTENT or name.endswith(".mp4"):
        return KIND_VIDEO
    return KIND_IMAGE


def _ext_for(content_type: Optional[str], filename: Optional[str]) -> str:
    ctype = str(content_type or "").split(";")[0].strip().lower()
    if ctype in ALLOWED_CONTENT:
        return ALLOWED_CONTENT[ctype]
    name = str(filename or "").lower()
    for ext in ("png", "jpg", "jpeg", "webp", "mp4"):
        if name.endswith("." + ext):
            return "jpg" if ext == "jpeg" else ext
    raise HTTPException(status_code=400, detail="Upload a JPG, PNG, WebP image or an MP4 video")


def _validate_image_bytes(data: bytes, content_type: Optional[str], filename: Optional[str]) -> tuple[str, str]:
    if not data:
        raise HTTPException(status_code=400, detail="Image file is required")
    if len(data) > MAX_IMAGE_BYTES:
        raise HTTPException(status_code=400, detail="Image must be 6 MB or smaller")
    ext = _ext_for(content_type, filename)
    if ext == "mp4":
        raise HTTPException(status_code=400, detail="Upload a PNG, JPG, or WEBP image")
    ctype = str(content_type or "").split(";")[0].strip().lower() or f"image/{'jpeg' if ext == 'jpg' else ext}"
    if ctype in ALLOWED_VIDEO_CONTENT:
        raise HTTPException(status_code=400, detail="Upload a PNG, JPG, or WEBP image")
    if ctype not in ALLOWED_IMAGE_CONTENT and not ctype.startswith("image/"):
        raise HTTPException(status_code=400, detail="Upload a PNG, JPG, or WEBP image")
    return ext, (ctype if ctype in ALLOWED_IMAGE_CONTENT else f"image/{'jpeg' if ext == 'jpg' else ext}")


def _validate_bytes(data: bytes, content_type: Optional[str], filename: Optional[str]) -> tuple[str, str]:
    kind = _guess_kind(content_type, filename)
    if kind == KIND_VIDEO:
        ext, ctype, _duration = _validate_video_bytes(data, content_type, filename)
        return ext, ctype
    return _validate_image_bytes(data, content_type, filename)


def _validate_video_bytes(data: bytes, content_type: Optional[str], filename: Optional[str]) -> tuple[str, str, float]:
    if not data:
        raise HTTPException(status_code=400, detail="Video file is required")
    if len(data) > MAX_VIDEO_BYTES:
        raise HTTPException(status_code=400, detail="Video must be 20 MB or smaller")
    ext = _ext_for(content_type, filename)
    if ext != "mp4":
        raise HTTPException(status_code=400, detail="Upload an MP4 video with H.264 video and AAC audio")
    duration, _video_ok, _audio_ok, _audio_present = _parse_mp4(data)
    return ext, "video/mp4", duration


def _validate_media(data: bytes, content_type: Optional[str], filename: Optional[str]) -> tuple[str, str, str, Optional[float]]:
    kind = _guess_kind(content_type, filename)
    if kind == KIND_VIDEO:
        ext, ctype, duration = _validate_video_bytes(data, content_type, filename)
        return ext, ctype, KIND_VIDEO, duration
    ext, ctype = _validate_image_bytes(data, content_type, filename)
    return ext, ctype, KIND_IMAGE, None


def _storage_key(kind: str, ext: str, suffix: str = "") -> str:
    storage = s3_storage.get_storage()
    name = f"{uuid.uuid4().hex}{suffix}.{ext}"
    key = storage.key("login-screen", kind, name)
    if not _key_allowed(key):
        raise HTTPException(status_code=500, detail="Refusing unexpected storage key")
    return key


async def _delete_stored_key(key: str) -> None:
    if _key_allowed(key):
        try:
            s3_storage.get_storage().delete(key)
        except Exception:
            pass


async def _delete_row(db, row: Optional[dict]) -> None:
    if not row:
        return
    await _delete_stored_key(str(row.get("storage_key") or ""))
    await _delete_stored_key(str(row.get("poster_key") or ""))
    await db[COLLECTION].delete_one({"id": row.get("id")})


async def purge_expired_scheduled(db) -> int:
    now = _now_ist()
    rows = await db[COLLECTION].find({"type": TYPE_SCHEDULED}, {"_id": 0}).to_list(20)
    removed = 0
    for row in rows:
        end = _try_parse_ist(row.get("end_at"))
        if end is None:
            continue
        if now >= end:
            await _delete_row(db, row)
            removed += 1
    return removed


def _row_kind(row: Optional[dict]) -> str:
    if not row:
        return KIND_IMAGE
    kind = str(row.get("media_kind") or "").lower()
    if kind in {KIND_IMAGE, KIND_VIDEO}:
        return kind
    ctype = str(row.get("content_type") or "").lower()
    if ctype.startswith("video/"):
        return KIND_VIDEO
    return KIND_IMAGE


def normalize_display_settings(raw: Any) -> Dict[str, Any]:
    payload = raw
    if isinstance(payload, str) and payload.strip():
        try:
            payload = json.loads(payload)
        except json.JSONDecodeError:
            payload = {}
    if not isinstance(payload, dict):
        payload = {}
    fit = str(payload.get("fit") or "contain").strip().lower()
    if fit not in {"contain", "cover"}:
        fit = "contain"
    try:
        zoom = float(payload.get("zoom") or 1)
    except (TypeError, ValueError):
        zoom = 1.0
    zoom = min(3.0, max(1.0, zoom))
    try:
        offset_x = float(payload.get("offset_x") or 0)
    except (TypeError, ValueError):
        offset_x = 0.0
    try:
        offset_y = float(payload.get("offset_y") or 0)
    except (TypeError, ValueError):
        offset_y = 0.0
    return {
        "fit": fit,
        "zoom": zoom,
        "offset_x": max(-1.0, min(1.0, offset_x)),
        "offset_y": max(-1.0, min(1.0, offset_y)),
    }


def _public_row(row: Optional[dict]) -> Dict[str, Any]:
    if not row:
        return {
            "image_url": None,
            "media_url": None,
            "poster_url": None,
            "media_kind": None,
            "type": None,
            "start_at": None,
            "end_at": None,
            "display_settings": None,
        }
    media_id = row.get("id")
    kind = _row_kind(row)
    media_url = f"/api/login-screen/image/{media_id}"
    poster_url = f"/api/login-screen/poster/{media_id}" if row.get("poster_key") else None
    image_url = poster_url or media_url
    return {
        "id": media_id,
        "type": row.get("type"),
        "image_url": image_url,
        "media_url": media_url,
        "poster_url": poster_url or (media_url if kind == KIND_IMAGE else None),
        "media_kind": kind,
        "start_at": row.get("start_at"),
        "end_at": row.get("end_at"),
        "display_settings": normalize_display_settings(row.get("display_settings")),
    }


async def choose_active(db) -> Optional[dict]:
    await purge_expired_scheduled(db)
    now = _now_ist()
    scheduled = await db[COLLECTION].find_one({"type": TYPE_SCHEDULED}, {"_id": 0})
    if scheduled:
        start = _try_parse_ist(scheduled.get("start_at"))
        end = _try_parse_ist(scheduled.get("end_at"))
        if start and end and start <= now < end:
            return scheduled
    return await db[COLLECTION].find_one({"type": TYPE_DEFAULT}, {"_id": 0})


async def active_payload(db) -> Dict[str, Any]:
    try:
        return _public_row(await choose_active(db))
    except Exception:
        logger.exception("login-screen active lookup failed")
        return {
            "image_url": None,
            "media_url": None,
            "poster_url": None,
            "media_kind": None,
            "type": None,
            "start_at": None,
            "end_at": None,
            "display_settings": None,
        }


def _row_visible_for_stream(row: dict) -> bool:
    if row.get("type") != TYPE_SCHEDULED:
        return True
    start = _try_parse_ist(row.get("start_at"))
    end = _try_parse_ist(row.get("end_at"))
    now = _now_ist()
    if start and end and start <= now < end:
        return True
    if end and now >= end:
        return False
    return True


async def stream_image(db, image_id: str):
    await purge_expired_scheduled(db)
    row = await db[COLLECTION].find_one({"id": str(image_id)}, {"_id": 0})
    if not row or not _row_visible_for_stream(row):
        raise HTTPException(status_code=404, detail="Image not found")
    key = str(row.get("storage_key") or "")
    if not _key_allowed(key):
        raise HTTPException(status_code=404, detail="Image not found")
    try:
        data, ctype = s3_storage.get_storage().download_bytes(key)
    except Exception:
        raise HTTPException(status_code=404, detail="Image not found")
    return StreamingResponse(
        BytesIO(data),
        media_type=str(row.get("content_type") or ctype or "image/png"),
        headers={"Cache-Control": "no-store"},
    )


async def stream_poster(db, image_id: str):
    await purge_expired_scheduled(db)
    row = await db[COLLECTION].find_one({"id": str(image_id)}, {"_id": 0})
    if not row or not _row_visible_for_stream(row):
        raise HTTPException(status_code=404, detail="Image not found")
    key = str(row.get("poster_key") or "")
    if not key:
        return await stream_image(db, image_id)
    if not _key_allowed(key):
        raise HTTPException(status_code=404, detail="Image not found")
    try:
        data, ctype = s3_storage.get_storage().download_bytes(key)
    except Exception:
        raise HTTPException(status_code=404, detail="Image not found")
    return StreamingResponse(
        BytesIO(data),
        media_type=str(row.get("poster_content_type") or ctype or "image/jpeg"),
        headers={"Cache-Control": "no-store"},
    )


async def manage_payload(db) -> Dict[str, Any]:
    await purge_expired_scheduled(db)
    default = await db[COLLECTION].find_one({"type": TYPE_DEFAULT}, {"_id": 0})
    scheduled = await db[COLLECTION].find_one({"type": TYPE_SCHEDULED}, {"_id": 0})
    active = await choose_active(db)
    return {
        "default": _public_row(default),
        "scheduled": _public_row(scheduled),
        "active": _public_row(active),
    }


def _store_media(kind: str, data: bytes, ctype: str, ext: str) -> str:
    key = _storage_key(kind, ext)
    stored = s3_storage.get_storage().upload_bytes(
        key,
        data,
        content_type=ctype,
        metadata={"login_screen": kind, "data_origin": _data_origin()},
        allow_replace=True,
    )
    return stored.storage_key


def _store_poster(kind: str, poster_data: Optional[bytes], poster_content_type: Optional[str], poster_filename: Optional[str]) -> tuple[Optional[str], Optional[str]]:
    if not poster_data:
        return None, None
    ext, ctype = _validate_image_bytes(poster_data, poster_content_type, poster_filename or "poster.jpg")
    key = _storage_key(kind, ext, suffix="-poster")
    stored = s3_storage.get_storage().upload_bytes(
        key,
        poster_data,
        content_type=ctype,
        metadata={"login_screen": f"{kind}-poster", "data_origin": _data_origin()},
        allow_replace=True,
    )
    return stored.storage_key, ctype


def _build_doc(
    slot: str,
    storage_key: str,
    ctype: str,
    media_kind: str,
    start_at: Optional[str],
    end_at: Optional[str],
    poster_key: Optional[str],
    poster_content_type: Optional[str],
    duration: Optional[float],
    display_settings: Optional[dict] = None,
) -> Dict[str, Any]:
    return {
        "id": str(uuid.uuid4()),
        "type": slot,
        "storage_key": storage_key,
        "content_type": ctype,
        "media_kind": media_kind,
        "poster_key": poster_key,
        "poster_content_type": poster_content_type,
        "duration_seconds": duration,
        "display_settings": normalize_display_settings(display_settings),
        "start_at": start_at,
        "end_at": end_at,
        "data_origin": _data_origin(),
        "updated_at": _now_ist().isoformat(),
    }


async def save_default(
    db,
    data: bytes,
    content_type: Optional[str],
    filename: Optional[str],
    poster_data: Optional[bytes] = None,
    poster_content_type: Optional[str] = None,
    poster_filename: Optional[str] = None,
    display_settings: Optional[Any] = None,
) -> Dict[str, Any]:
    ext, ctype, media_kind, duration = _validate_media(data, content_type, filename)
    if media_kind == KIND_VIDEO and not poster_data:
        raise HTTPException(status_code=400, detail="Video poster is required")
    old = await db[COLLECTION].find_one({"type": TYPE_DEFAULT}, {"_id": 0})
    key = _store_media(TYPE_DEFAULT, data, ctype, ext)
    poster_key, poster_ctype = (None, None)
    if media_kind == KIND_VIDEO:
        poster_key, poster_ctype = _store_poster(TYPE_DEFAULT, poster_data, poster_content_type, poster_filename)
    doc = _build_doc(
        TYPE_DEFAULT,
        key,
        ctype,
        media_kind,
        None,
        None,
        poster_key,
        poster_ctype,
        duration,
        display_settings=display_settings,
    )
    await db[COLLECTION].insert_one(dict(doc))
    if old:
        await _delete_row(db, old)
    return _public_row(doc)


async def save_scheduled(
    db,
    data: bytes,
    content_type: Optional[str],
    filename: Optional[str],
    start_at: str,
    end_at: str,
    poster_data: Optional[bytes] = None,
    poster_content_type: Optional[str] = None,
    poster_filename: Optional[str] = None,
    display_settings: Optional[Any] = None,
) -> Dict[str, Any]:
    ext, ctype, media_kind, duration = _validate_media(data, content_type, filename)
    if media_kind == KIND_VIDEO and not poster_data:
        raise HTTPException(status_code=400, detail="Video poster is required")
    start = _parse_ist(start_at)
    end = _parse_ist(end_at)
    if not start or not end:
        raise HTTPException(status_code=400, detail="Start and End date/time are required")
    if end <= start:
        raise HTTPException(status_code=400, detail="End date/time must be after Start date/time")
    old = await db[COLLECTION].find_one({"type": TYPE_SCHEDULED}, {"_id": 0})
    key = _store_media(TYPE_SCHEDULED, data, ctype, ext)
    poster_key, poster_ctype = (None, None)
    if media_kind == KIND_VIDEO:
        poster_key, poster_ctype = _store_poster(TYPE_SCHEDULED, poster_data, poster_content_type, poster_filename)
    doc = _build_doc(
        TYPE_SCHEDULED,
        key,
        ctype,
        media_kind,
        start.isoformat(),
        end.isoformat(),
        poster_key,
        poster_ctype,
        duration,
        display_settings=display_settings,
    )
    await db[COLLECTION].insert_one(dict(doc))
    if old:
        await _delete_row(db, old)
    return _public_row(doc)
