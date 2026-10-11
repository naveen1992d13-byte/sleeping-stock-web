"""Login Screen Default/Scheduled image manager.

Testing writes testing/ keys into nmts_testing.
Production writes dev/ keys into nmts.
Never copies Testing records or Testing S3 keys into Production.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from io import BytesIO
from typing import Any, Dict, Optional
from zoneinfo import ZoneInfo

from fastapi import HTTPException
from fastapi.responses import StreamingResponse

try:
    from . import s3_storage
    from . import testing_runtime
except ImportError:
    import s3_storage
    import testing_runtime

IST = ZoneInfo("Asia/Kolkata")
COLLECTION = "login_screen_images"
TYPE_DEFAULT = "default"
TYPE_SCHEDULED = "scheduled"
ALLOWED_CONTENT = {
    "image/png": "png",
    "image/jpeg": "jpg",
    "image/jpg": "jpg",
    "image/webp": "webp",
}
MAX_BYTES = 6 * 1024 * 1024


def require_master(current_user) -> None:
    if str(getattr(current_user, "role", "") or "").lower() != "master":
        raise HTTPException(status_code=403, detail="Master Admin only")


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


def _now_ist() -> datetime:
    return datetime.now(IST)


def _ext_for(content_type: Optional[str], filename: Optional[str]) -> str:
    ctype = str(content_type or "").split(";")[0].strip().lower()
    if ctype in ALLOWED_CONTENT:
        return ALLOWED_CONTENT[ctype]
    name = str(filename or "").lower()
    for ext in ("png", "jpg", "jpeg", "webp"):
        if name.endswith("." + ext):
            return "jpg" if ext == "jpeg" else ext
    raise HTTPException(status_code=400, detail="Upload a PNG, JPG, or WEBP image")


def _validate_bytes(data: bytes, content_type: Optional[str], filename: Optional[str]) -> tuple[str, str]:
    if not data:
        raise HTTPException(status_code=400, detail="Image file is required")
    if len(data) > MAX_BYTES:
        raise HTTPException(status_code=400, detail="Image must be 6 MB or smaller")
    ext = _ext_for(content_type, filename)
    ctype = str(content_type or "").split(";")[0].strip().lower() or f"image/{'jpeg' if ext == 'jpg' else ext}"
    if ctype not in ALLOWED_CONTENT and not ctype.startswith("image/"):
        raise HTTPException(status_code=400, detail="Upload a PNG, JPG, or WEBP image")
    return ext, (ctype if ctype in ALLOWED_CONTENT else f"image/{'jpeg' if ext == 'jpg' else ext}")


def _storage_key(kind: str, ext: str) -> str:
    storage = s3_storage.get_storage()
    key = storage.key("login-screen", kind, f"{uuid.uuid4().hex}.{ext}")
    if not _key_allowed(key):
        raise HTTPException(status_code=500, detail="Refusing unexpected storage key")
    return key


async def _delete_row(db, row: Optional[dict]) -> None:
    if not row:
        return
    key = str(row.get("storage_key") or "")
    if _key_allowed(key):
        try:
            s3_storage.get_storage().delete(key)
        except Exception:
            pass
    await db[COLLECTION].delete_one({"id": row.get("id")})


async def purge_expired_scheduled(db) -> int:
    now = _now_ist()
    rows = await db[COLLECTION].find({"type": TYPE_SCHEDULED}, {"_id": 0}).to_list(20)
    removed = 0
    for row in rows:
        end = _parse_ist(row.get("end_at"))
        if end is None or now >= end:
            await _delete_row(db, row)
            removed += 1
    return removed


def _public_row(row: Optional[dict]) -> Dict[str, Any]:
    if not row:
        return {"image_url": None, "type": None, "start_at": None, "end_at": None}
    return {
        "id": row.get("id"),
        "type": row.get("type"),
        "image_url": f"/api/login-screen/image/{row.get('id')}",
        "start_at": row.get("start_at"),
        "end_at": row.get("end_at"),
    }


async def choose_active(db) -> Optional[dict]:
    await purge_expired_scheduled(db)
    now = _now_ist()
    scheduled = await db[COLLECTION].find_one({"type": TYPE_SCHEDULED}, {"_id": 0})
    if scheduled:
        start = _parse_ist(scheduled.get("start_at"))
        end = _parse_ist(scheduled.get("end_at"))
        if start and end and start <= now < end:
            return scheduled
    return await db[COLLECTION].find_one({"type": TYPE_DEFAULT}, {"_id": 0})


async def active_payload(db) -> Dict[str, Any]:
    try:
        return _public_row(await choose_active(db))
    except Exception:
        return {"image_url": None, "type": None, "start_at": None, "end_at": None}


async def stream_image(db, image_id: str):
    await purge_expired_scheduled(db)
    row = await db[COLLECTION].find_one({"id": str(image_id)}, {"_id": 0})
    if not row:
        raise HTTPException(status_code=404, detail="Image not found")
    if row.get("type") == TYPE_SCHEDULED:
        start = _parse_ist(row.get("start_at"))
        end = _parse_ist(row.get("end_at"))
        now = _now_ist()
        if not (start and end and start <= now < end):
            # Manager preview still needs the scheduled file before start.
            # Public login uses choose_active; this endpoint is used for both.
            # Allow fetch if the record still exists (not expired/deleted).
            if end and now >= end:
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


async def manage_payload(db) -> Dict[str, Any]:
    await purge_expired_scheduled(db)
    default = await db[COLLECTION].find_one({"type": TYPE_DEFAULT}, {"_id": 0})
    scheduled = await db[COLLECTION].find_one({"type": TYPE_SCHEDULED}, {"_id": 0})
    return {"default": _public_row(default), "scheduled": _public_row(scheduled)}


async def save_default(db, data: bytes, content_type: Optional[str], filename: Optional[str]) -> Dict[str, Any]:
    ext, ctype = _validate_bytes(data, content_type, filename)
    old = await db[COLLECTION].find_one({"type": TYPE_DEFAULT}, {"_id": 0})
    key = _storage_key(TYPE_DEFAULT, ext)
    stored = s3_storage.get_storage().upload_bytes(
        key,
        data,
        content_type=ctype,
        metadata={"login_screen": TYPE_DEFAULT, "data_origin": _data_origin()},
        allow_replace=True,
    )
    doc = {
        "id": str(uuid.uuid4()),
        "type": TYPE_DEFAULT,
        "storage_key": stored.storage_key,
        "content_type": ctype,
        "start_at": None,
        "end_at": None,
        "data_origin": _data_origin(),
        "updated_at": _now_ist().isoformat(),
    }
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
) -> Dict[str, Any]:
    ext, ctype = _validate_bytes(data, content_type, filename)
    start = _parse_ist(start_at)
    end = _parse_ist(end_at)
    if not start or not end:
        raise HTTPException(status_code=400, detail="Start and End date/time are required")
    if end <= start:
        raise HTTPException(status_code=400, detail="End date/time must be after Start date/time")
    old = await db[COLLECTION].find_one({"type": TYPE_SCHEDULED}, {"_id": 0})
    key = _storage_key(TYPE_SCHEDULED, ext)
    stored = s3_storage.get_storage().upload_bytes(
        key,
        data,
        content_type=ctype,
        metadata={"login_screen": TYPE_SCHEDULED, "data_origin": _data_origin()},
        allow_replace=True,
    )
    doc = {
        "id": str(uuid.uuid4()),
        "type": TYPE_SCHEDULED,
        "storage_key": stored.storage_key,
        "content_type": ctype,
        "start_at": start.isoformat(),
        "end_at": end.isoformat(),
        "data_origin": _data_origin(),
        "updated_at": _now_ist().isoformat(),
    }
    await db[COLLECTION].insert_one(dict(doc))
    if old:
        await _delete_row(db, old)
    return _public_row(doc)
