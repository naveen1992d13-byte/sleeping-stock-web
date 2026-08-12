"""NMTS Notice Board — brand-scoped notices with read/ack tracking and PDF attachments."""
from __future__ import annotations

import os
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel, Field
from pymongo import ReturnDocument

router = APIRouter(prefix="/notice-board", tags=["Notice Board"])
NOTICE_STORAGE = Path(os.getenv("NOTICE_BOARD_STORAGE_DIR", Path(__file__).parent / "notice_files"))
NOTICE_STORAGE.mkdir(parents=True, exist_ok=True)

_AUTH = HTTPBearer()
_AUTH_DEP = None
db = None

IST = ZoneInfo("Asia/Kolkata")
MAX_PDF_BYTES = int(os.getenv("NOTICE_BOARD_MAX_PDF_BYTES", str(10 * 1024 * 1024)))

NOTICE_TYPES = {
    "General Notice",
    "Important Alert",
    "Appreciation",
    "System Update",
    "Policy / Process Update",
    "Action Required",
}
PRIORITIES = {"Normal", "Important", "Urgent"}
STATUSES = {"Draft", "Published", "Cancelled", "Expired"}
AUDIENCE_TYPES = {"all_brands", "selected_brand"}
PRIORITY_RANK = {"Urgent": 0, "Important": 1, "Normal": 2}


def init_notice_board(database, get_current_user, UserResponse):
    globals()["db"] = database
    globals()["_AUTH_DEP"] = get_current_user
    globals()["UserResponse"] = UserResponse


async def _current_user(credentials: HTTPAuthorizationCredentials = Depends(_AUTH)):
    if _AUTH_DEP is None:
        raise HTTPException(status_code=500, detail="Notice Board authentication is not initialized")
    return await _AUTH_DEP(credentials)


def _require_master(user):
    if (user.role or "").lower() != "master":
        raise HTTPException(status_code=403, detail="Master Admin only")


def _role_label(role: str) -> str:
    r = (role or "").lower()
    if r == "master":
        return "Master Admin"
    if r == "admin":
        return "Admin"
    return "User"


def _sanitize_text(value: str, max_len: Optional[int] = None) -> str:
    text = re.sub(r"<script\b[^>]*>.*?</script>", "", str(value or ""), flags=re.I | re.S).strip()
    if max_len:
        text = text[:max_len]
    return text


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _parse_dt(value) -> Optional[datetime]:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).astimezone(timezone.utc)
    except ValueError:
        for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
            try:
                dt = datetime.strptime(text[:19] if " " in fmt else text[:10], fmt)
                return dt.replace(tzinfo=timezone.utc)
            except ValueError:
                continue
    return None


def _ist_day_key(dt: Optional[datetime] = None) -> str:
    ref = (dt or _utcnow()).astimezone(IST)
    return ref.strftime("%Y-%m-%d")


async def _lookup_brand(name: str) -> dict:
    clean = _sanitize_text(name)
    if not clean:
        return {"brand_id": "", "brand_name": ""}
    row = await db.brands.find_one(
        {"name": {"$regex": f"^{re.escape(clean)}$", "$options": "i"}},
        {"_id": 0, "id": 1, "code": 1, "name": 1},
    )
    if not row:
        return {"brand_id": "", "brand_name": clean}
    return {
        "brand_id": str(row.get("id") or row.get("code") or ""),
        "brand_name": row.get("name") or clean,
    }


def _serialize_notice(doc: dict, include_internal: bool = False) -> dict:
    if not doc:
        return doc
    out = dict(doc)
    out.pop("_id", None)
    if not include_internal:
        out.pop("attachment_storage_path", None)
    out["notice_id"] = out.get("id")
    return out


async def _audit(notice_id: str, user, action: str, meta: Optional[dict] = None):
    await db.notice_audit_logs.insert_one(
        {
            "id": str(uuid.uuid4()),
            "notice_id": notice_id,
            "action": action,
            "user_id": user.id,
            "user_name": user.username,
            "user_role": _role_label(user.role),
            "metadata": meta or {},
            "created_at": _utcnow(),
        }
    )


async def _auto_expire_notices():
    now = _utcnow()
    await db.notices.update_many(
        {
            "status": "Published",
            "expiry_date": {"$ne": None, "$lte": now},
        },
        {"$set": {"status": "Expired", "expired_at": now, "updated_at": now}},
    )


def _publish_moment(notice: dict) -> Optional[datetime]:
    return _parse_dt(notice.get("publish_date")) or _parse_dt(notice.get("published_at"))


def _is_published_active(notice: dict, now: Optional[datetime] = None) -> bool:
    now = now or _utcnow()
    if notice.get("status") != "Published":
        return False
    pub = _publish_moment(notice)
    if pub and pub > now:
        return False
    exp = _parse_dt(notice.get("expiry_date"))
    if exp and exp <= now:
        return False
    return True


def _user_brand(user) -> str:
    return _sanitize_text(user.brand or "")


def _notice_brand_match(notice: dict, user) -> bool:
    if (user.role or "").lower() == "master":
        return True
    aud = notice.get("audience_type") or "selected_brand"
    if aud == "all_brands":
        return True
    nb = _sanitize_text(notice.get("brand_name") or "")
    ub = _user_brand(user)
    return bool(nb and ub and nb.casefold() == ub.casefold())


def _user_can_view_notice(notice: dict, user) -> bool:
    if (user.role or "").lower() == "master":
        return True
    if not _notice_brand_match(notice, user):
        return False
    return _is_published_active(notice)


def _eligible_users_query(notice: dict) -> dict:
    filt = {"role": {"$in": ["admin", "user"]}, "status": {"$regex": "^active$", "$options": "i"}}
    if notice.get("audience_type") == "selected_brand":
        brand = _sanitize_text(notice.get("brand_name") or "")
        if brand:
            filt["brand"] = {"$regex": f"^{re.escape(brand)}$", "$options": "i"}
    return filt


async def _get_user_status(notice_id: str, user) -> Optional[dict]:
    return await db.notice_user_status.find_one(
        {"notice_id": notice_id, "user_id": user.id},
        {"_id": 0},
    )


async def _ensure_user_status(notice_id: str, user) -> dict:
    existing = await _get_user_status(notice_id, user)
    if existing:
        return existing
    brand = await _lookup_brand(_user_brand(user))
    doc = {
        "id": str(uuid.uuid4()),
        "notice_id": notice_id,
        "user_id": user.id,
        "user_role": _role_label(user.role),
        "brand_id": brand.get("brand_id", ""),
        "brand_name": brand.get("brand_name", _user_brand(user)),
        "read_status": "Unread",
        "read_at": None,
        "acknowledgement_status": "Pending",
        "acknowledged_at": None,
        "last_popup_shown_date": None,
        "popup_show_count": 0,
        "created_at": _utcnow(),
        "updated_at": _utcnow(),
    }
    await db.notice_user_status.insert_one(doc)
    return doc


async def _save_pdf(file: UploadFile, notice_id: str) -> dict:
    filename = Path(file.filename or "").name
    if Path(filename).suffix.lower() != ".pdf":
        raise HTTPException(status_code=400, detail="Only PDF files are allowed")
    content = await file.read()
    if not content.startswith(b"%PDF"):
        raise HTTPException(status_code=400, detail="Invalid PDF file content")
    if len(content) > MAX_PDF_BYTES:
        raise HTTPException(status_code=400, detail=f"PDF exceeds {MAX_PDF_BYTES // (1024 * 1024)} MB limit")
    file_id = str(uuid.uuid4())
    safe = f"notice_{notice_id}_{file_id}.pdf"
    path = NOTICE_STORAGE / safe
    path.write_bytes(content)  # legacy local fallback retained
    stored = {}
    try:
        try:
            import file_objects
        except ImportError:
            from . import file_objects
        stored = await file_objects.store_bytes(
            module="notices",
            relative_key=f"{notice_id}/{safe}",
            data=content,
            original_filename=filename,
            content_type="application/pdf",
        )
    except Exception:
        stored = {}
    return {
        "file_id": file_id,
        "file_name": filename,
        "file_url": f"/api/notice-board/attachments/{file_id}",
        "content_type": "application/pdf",
        "file_size": len(content),
        "attachment_storage_path": str(path),
        "storage_provider": stored.get("storage_provider"),
        "storage_key": stored.get("storage_key"),
        "sha256": stored.get("sha256"),
        "archived_at": stored.get("archived_at"),
    }


class NoticeCreateBody(BaseModel):
    subject: str
    content: str
    notice_type: str
    priority: str
    audience_type: str
    brand_name: Optional[str] = None
    popup_required: bool = False
    acknowledgement_required: bool = False
    publish_date: Optional[str] = None
    expiry_date: Optional[str] = None
    revision_of_notice_id: Optional[str] = None


class NoticeCancelBody(BaseModel):
    reason: str = ""


@router.post("/notices")
async def create_notice(body: NoticeCreateBody, current_user=Depends(_current_user)):
    _require_master(current_user)
    subject = _sanitize_text(body.subject, 300)
    content = _sanitize_text(body.content, 10000)
    if not subject or not content:
        raise HTTPException(status_code=400, detail="Subject and content are required")
    if body.notice_type not in NOTICE_TYPES:
        raise HTTPException(status_code=400, detail="Invalid notice type")
    if body.priority not in PRIORITIES:
        raise HTTPException(status_code=400, detail="Invalid priority")
    if body.audience_type not in AUDIENCE_TYPES:
        raise HTTPException(status_code=400, detail="Invalid audience type")
    brand_id, brand_name = "", ""
    if body.audience_type == "selected_brand":
        if not _sanitize_text(body.brand_name or ""):
            raise HTTPException(status_code=400, detail="Brand is required for selected brand audience")
        brand = await _lookup_brand(body.brand_name)
        if not brand.get("brand_id"):
            raise HTTPException(status_code=400, detail="Brand not found in system")
        brand_id, brand_name = brand["brand_id"], brand["brand_name"]
    now = _utcnow()
    notice_id = str(uuid.uuid4())
    doc = {
        "id": notice_id,
        "subject": subject,
        "content": content,
        "notice_type": body.notice_type,
        "priority": body.priority,
        "audience_type": body.audience_type,
        "brand_id": brand_id,
        "brand_name": brand_name,
        "attachment": None,
        "popup_required": bool(body.popup_required),
        "acknowledgement_required": bool(body.acknowledgement_required),
        "publish_date": _parse_dt(body.publish_date),
        "expiry_date": _parse_dt(body.expiry_date),
        "status": "Draft",
        "created_by_user_id": current_user.id,
        "created_by_name": current_user.username,
        "created_by_role": _role_label(current_user.role),
        "created_at": now,
        "updated_at": now,
        "published_at": None,
        "cancelled_at": None,
        "expired_at": None,
        "cancellation_reason": "",
        "revision_of_notice_id": body.revision_of_notice_id,
        "version": 1,
    }
    await db.notices.insert_one(doc)
    await _audit(notice_id, current_user, "notice_created", {"status": "Draft"})
    return _serialize_notice(doc)


@router.post("/notices/{notice_id}/pdf")
async def upload_notice_pdf(
    notice_id: str,
    file: UploadFile = File(...),
    current_user=Depends(_current_user),
):
    _require_master(current_user)
    notice = await db.notices.find_one({"id": notice_id})
    if not notice:
        raise HTTPException(status_code=404, detail="Notice not found")
    if notice.get("status") == "Published":
        raise HTTPException(status_code=409, detail="Published notices cannot be modified; cancel and create a revision")
    saved = await _save_pdf(file, notice_id)
    await db.notice_attachments.insert_one(
        {
            "file_id": saved["file_id"],
            "notice_id": notice_id,
            "file_name": saved["file_name"],
            "content_type": saved["content_type"],
            "file_size": saved["file_size"],
            "storage_path": saved["attachment_storage_path"],
            "storage_provider": saved.get("storage_provider"),
            "storage_key": saved.get("storage_key"),
            "sha256": saved.get("sha256"),
            "archived_at": saved.get("archived_at"),
            "created_at": _utcnow(),
        }
    )
    attachment = {k: saved[k] for k in ("file_id", "file_name", "file_url", "content_type", "file_size")}
    doc = await db.notices.find_one_and_update(
        {"id": notice_id},
        {"$set": {"attachment": attachment, "updated_at": _utcnow()}},
        return_document=ReturnDocument.AFTER,
        projection={"_id": 0},
    )
    await _audit(notice_id, current_user, "pdf_uploaded", {"file_name": saved["file_name"]})
    return _serialize_notice(doc)


@router.get("/notices")
async def list_notices_master(
    page: int = 1,
    page_size: int = 20,
    status: Optional[str] = None,
    search: Optional[str] = None,
    read_filter: Optional[str] = None,
    notice_type: Optional[str] = None,
    from_date: Optional[str] = None,
    to_date: Optional[str] = None,
    current_user=Depends(_current_user),
):
    await _auto_expire_notices()
    page = max(1, page)
    page_size = max(1, min(page_size, 100))
    filt: dict = {}
    if (current_user.role or "").lower() == "master":
        if status and status in STATUSES:
            filt["status"] = status
    else:
        filt["status"] = "Published"
        brand = _user_brand(current_user)
        filt["$or"] = [{"audience_type": "all_brands"}, {"brand_name": {"$regex": f"^{re.escape(brand)}$", "$options": "i"}}]
    search = _sanitize_text(search or "")
    if search:
        safe = re.escape(search)
        filt["$and"] = filt.get("$and", []) + [{"$or": [{"subject": {"$regex": safe, "$options": "i"}}, {"content": {"$regex": safe, "$options": "i"}}]}]
    if notice_type and notice_type in NOTICE_TYPES:
        filt["notice_type"] = notice_type
    fd = _parse_dt(from_date) if from_date else None
    td = _parse_dt(to_date) if to_date else None
    if fd or td:
        pub_range = {}
        if fd:
            pub_range["$gte"] = fd
        if td:
            pub_range["$lte"] = td
        filt["publish_date"] = pub_range
    if (current_user.role or "").lower() != "master":
        now = _utcnow()
        filt["$and"] = filt.get("$and", []) + [
            {
                "$or": [
                    {"publish_date": None},
                    {"publish_date": {"$lte": now}},
                ]
            },
            {
                "$or": [
                    {"expiry_date": None},
                    {"expiry_date": {"$gt": now}},
                ]
            },
        ]
    total = await db.notices.count_documents(filt)
    rows = (
        await db.notices.find(filt, {"_id": 0, "attachment_storage_path": 0})
        .sort("publish_date", -1)
        .skip((page - 1) * page_size)
        .limit(page_size)
        .to_list(page_size)
    )
    if (current_user.role or "").lower() == "master":
        enriched = []
        for row in rows:
            item = _serialize_notice(row)
            item["tracking_summary"] = await _tracking_summary(row["id"])
            enriched.append(item)
        rows = enriched
    else:
        enriched = []
        for row in rows:
            if not _is_published_active(row):
                continue
            st = await _get_user_status(row["id"], current_user)
            rs = (st or {}).get("read_status", "Unread")
            if read_filter == "unread" and rs == "Read":
                continue
            item = _serialize_notice(row)
            item["user_read_status"] = rs
            item["user_ack_status"] = (st or {}).get("acknowledgement_status", "Pending")
            enriched.append(item)
        rows = enriched
        total = len(rows) if read_filter else total
    return {"records": rows, "total": total, "page": page, "page_size": page_size, "total_pages": max(1, (total + page_size - 1) // page_size)}


async def _tracking_summary(notice_id: str) -> dict:
    notice = await db.notices.find_one({"id": notice_id}, {"_id": 0})
    if not notice:
        return {}
    eligible = await db.users.count_documents(_eligible_users_query(notice))
    read = await db.notice_user_status.count_documents({"notice_id": notice_id, "read_status": "Read"})
    ack = await db.notice_user_status.count_documents(
        {"notice_id": notice_id, "acknowledgement_status": "Acknowledged"}
    )
    pending_ack = 0
    if notice.get("acknowledgement_required"):
        pending_ack = max(eligible - ack, 0)
    return {
        "eligible_users": eligible,
        "read_users": read,
        "unread_users": max(eligible - read, 0),
        "acknowledged_users": ack,
        "pending_acknowledgement_users": pending_ack,
    }


@router.get("/notices/{notice_id}")
async def get_notice(notice_id: str, current_user=Depends(_current_user)):
    await _auto_expire_notices()
    notice = await db.notices.find_one({"id": notice_id}, {"_id": 0})
    if not notice:
        raise HTTPException(status_code=404, detail="Notice not found")
    role = (current_user.role or "").lower()
    if role != "master":
        if not _user_can_view_notice(notice, current_user):
            raise HTTPException(status_code=403, detail="Notice not available for your brand")
    out = _serialize_notice(notice)
    if role == "master":
        out["tracking_summary"] = await _tracking_summary(notice_id)
    else:
        st = await _get_user_status(notice_id, current_user)
        out["user_read_status"] = (st or {}).get("read_status", "Unread")
        out["user_ack_status"] = (st or {}).get("acknowledgement_status", "Pending")
    return out


@router.post("/notices/{notice_id}/publish")
async def publish_notice(notice_id: str, current_user=Depends(_current_user)):
    _require_master(current_user)
    notice = await db.notices.find_one({"id": notice_id})
    if not notice:
        raise HTTPException(status_code=404, detail="Notice not found")
    if notice.get("status") != "Draft":
        raise HTTPException(status_code=409, detail="Only draft notices can be published")
    now = _utcnow()
    pub = _parse_dt(notice.get("publish_date")) or now
    doc = await db.notices.find_one_and_update(
        {"id": notice_id},
        {"$set": {"status": "Published", "published_at": now, "publish_date": pub, "updated_at": now}},
        return_document=ReturnDocument.AFTER,
        projection={"_id": 0},
    )
    await _audit(notice_id, current_user, "notice_published", {"previous_status": "Draft", "new_status": "Published"})
    # Additive in-app bell alert for eligible users (email/WhatsApp unaffected)
    try:
        import user_alerts as ua

        await ua.alert_notice_published(doc or {})
    except Exception:
        pass
    return _serialize_notice(doc)


@router.post("/notices/{notice_id}/cancel")
async def cancel_notice(notice_id: str, body: NoticeCancelBody, current_user=Depends(_current_user)):
    _require_master(current_user)
    notice = await db.notices.find_one({"id": notice_id})
    if not notice:
        raise HTTPException(status_code=404, detail="Notice not found")
    if notice.get("status") in {"Cancelled", "Expired"}:
        raise HTTPException(status_code=409, detail="Notice already inactive")
    now = _utcnow()
    doc = await db.notices.find_one_and_update(
        {"id": notice_id},
        {
            "$set": {
                "status": "Cancelled",
                "cancelled_at": now,
                "cancellation_reason": _sanitize_text(body.reason, 500),
                "updated_at": now,
            }
        },
        return_document=ReturnDocument.AFTER,
        projection={"_id": 0},
    )
    await _audit(notice_id, current_user, "notice_cancelled", {"reason": body.reason})
    return _serialize_notice(doc)


@router.post("/notices/{notice_id}/expire")
async def expire_notice(notice_id: str, current_user=Depends(_current_user)):
    _require_master(current_user)
    notice = await db.notices.find_one({"id": notice_id})
    if not notice:
        raise HTTPException(status_code=404, detail="Notice not found")
    if notice.get("status") != "Published":
        raise HTTPException(status_code=409, detail="Only published notices can be expired")
    now = _utcnow()
    doc = await db.notices.find_one_and_update(
        {"id": notice_id},
        {"$set": {"status": "Expired", "expired_at": now, "updated_at": now}},
        return_document=ReturnDocument.AFTER,
        projection={"_id": 0},
    )
    await _audit(notice_id, current_user, "notice_expired", {})
    return _serialize_notice(doc)


@router.get("/notices/{notice_id}/tracking")
async def notice_tracking(notice_id: str, page: int = 1, page_size: int = 50, current_user=Depends(_current_user)):
    _require_master(current_user)
    notice = await db.notices.find_one({"id": notice_id}, {"_id": 0})
    if not notice:
        raise HTTPException(status_code=404, detail="Notice not found")
    summary = await _tracking_summary(notice_id)
    page = max(1, page)
    page_size = max(1, min(page_size, 200))
    users = (
        await db.users.find(
            _eligible_users_query(notice),
            {"_id": 0, "id": 1, "username": 1, "email": 1, "role": 1, "brand": 1},
        )
        .sort("username", 1)
        .skip((page - 1) * page_size)
        .limit(page_size)
        .to_list(page_size)
    )
    total_users = await db.users.count_documents(_eligible_users_query(notice))
    rows = []
    for u in users:
        st = await db.notice_user_status.find_one({"notice_id": notice_id, "user_id": u["id"]}, {"_id": 0})
        rows.append(
            {
                "user_id": u["id"],
                "user_name": u.get("username"),
                "email": u.get("email"),
                "role": u.get("role"),
                "brand": u.get("brand"),
                "read_status": (st or {}).get("read_status", "Unread"),
                "read_at": (st or {}).get("read_at"),
                "acknowledgement_status": (st or {}).get("acknowledgement_status", "Pending"),
                "acknowledged_at": (st or {}).get("acknowledged_at"),
            }
        )
    return {"summary": summary, "records": rows, "total": total_users, "page": page, "page_size": page_size}


@router.get("/attachments/{file_id}")
async def download_attachment(file_id: str, current_user=Depends(_current_user)):
    meta = await db.notice_attachments.find_one({"file_id": file_id})
    if not meta:
        raise HTTPException(status_code=404, detail="Attachment not found")
    notice = await db.notices.find_one({"id": meta.get("notice_id")})
    if not notice:
        raise HTTPException(status_code=404, detail="Notice not found")
    if (current_user.role or "").lower() != "master":
        if not _user_can_view_notice(notice, current_user):
            raise HTTPException(status_code=403, detail="Not authorized to access this PDF")
        await _mark_read(meta["notice_id"], current_user, source="pdf")
    try:
        import file_objects
    except ImportError:
        from . import file_objects
    # Normalize legacy field name
    if meta.get("storage_path") and not meta.get("attachment_storage_path"):
        meta = {**meta, "attachment_storage_path": meta.get("storage_path")}
    if not file_objects.meta_has_readable_bytes(meta):
        raise HTTPException(status_code=404, detail="File not found")
    return file_objects.streaming_response_from_meta(meta, filename=meta.get("file_name") or "notice.pdf")


async def _mark_read(notice_id: str, user, source: str = "detail"):
    notice = await db.notices.find_one({"id": notice_id})
    if not notice or not _user_can_view_notice(notice, user):
        raise HTTPException(status_code=403, detail="Notice not available")
    now = _utcnow()
    existing = await _get_user_status(notice_id, user)
    if existing and existing.get("read_status") == "Read":
        return existing
    if existing:
        await db.notice_user_status.update_one(
            {"notice_id": notice_id, "user_id": user.id},
            {"$set": {"read_status": "Read", "read_at": now, "updated_at": now}},
        )
    else:
        brand = await _lookup_brand(_user_brand(user))
        await db.notice_user_status.insert_one(
            {
                "id": str(uuid.uuid4()),
                "notice_id": notice_id,
                "user_id": user.id,
                "user_role": _role_label(user.role),
                "brand_id": brand.get("brand_id", ""),
                "brand_name": brand.get("brand_name", _user_brand(user)),
                "read_status": "Read",
                "read_at": now,
                "acknowledgement_status": "Pending",
                "acknowledged_at": None,
                "last_popup_shown_date": None,
                "popup_show_count": 0,
                "created_at": now,
                "updated_at": now,
            }
        )
    await _audit(notice_id, user, "notice_read", {"source": source})
    return await _get_user_status(notice_id, user)


@router.post("/notices/{notice_id}/read")
async def mark_read(notice_id: str, current_user=Depends(_current_user)):
    if (current_user.role or "").lower() == "master":
        raise HTTPException(status_code=403, detail="Read tracking applies to Admin and User only")
    await _mark_read(notice_id, current_user, source="detail")
    return {"message": "Marked as read"}


@router.post("/notices/{notice_id}/acknowledge")
async def acknowledge_notice(notice_id: str, current_user=Depends(_current_user)):
    if (current_user.role or "").lower() == "master":
        raise HTTPException(status_code=403, detail="Acknowledgement applies to Admin and User only")
    notice = await db.notices.find_one({"id": notice_id})
    if not notice or not _user_can_view_notice(notice, current_user):
        raise HTTPException(status_code=403, detail="Notice not available")
    if not notice.get("acknowledgement_required"):
        raise HTTPException(status_code=400, detail="Acknowledgement not required for this notice")
    await _mark_read(notice_id, current_user, source="acknowledge")
    now = _utcnow()
    st = await _get_user_status(notice_id, current_user)
    if st and st.get("acknowledgement_status") == "Acknowledged":
        return {"message": "Already acknowledged"}
    await db.notice_user_status.update_one(
        {"notice_id": notice_id, "user_id": current_user.id},
        {"$set": {"acknowledgement_status": "Acknowledged", "acknowledged_at": now, "updated_at": now}},
        upsert=False,
    )
    await _audit(notice_id, current_user, "notice_acknowledged", {})
    return {"message": "Acknowledged"}


def _should_popup(notice: dict, st: Optional[dict], today: str) -> bool:
    if not notice.get("popup_required"):
        return False
    if not _is_published_active(notice):
        return False
    priority = notice.get("priority") or "Normal"
    read_status = (st or {}).get("read_status", "Unread")
    ack_required = bool(notice.get("acknowledgement_required"))
    ack_status = (st or {}).get("acknowledgement_status", "Pending")
    last_day = (st or {}).get("last_popup_shown_date")
    if priority == "Urgent":
        if ack_required and ack_status != "Acknowledged":
            return True
        if read_status != "Read":
            return True
        return last_day != today
    if last_day == today:
        return False
    return True


@router.get("/popups")
async def login_popups(current_user=Depends(_current_user)):
    role = (current_user.role or "").lower()
    if role == "master":
        return {"primary": None, "notices": []}
    await _auto_expire_notices()
    today = _ist_day_key()
    brand = _user_brand(current_user)
    filt = {
        "status": "Published",
        "popup_required": True,
        "$or": [{"audience_type": "all_brands"}, {"brand_name": {"$regex": f"^{re.escape(brand)}$", "$options": "i"}}],
    }
    now = _utcnow()
    filt["$and"] = [
        {"$or": [{"publish_date": None}, {"publish_date": {"$lte": now}}]},
        {"$or": [{"expiry_date": None}, {"expiry_date": {"$gt": now}}]},
    ]
    notices = await db.notices.find(filt, {"_id": 0}).sort("publish_date", -1).to_list(200)
    candidates = []
    for n in notices:
        if not _is_published_active(n):
            continue
        st = await _get_user_status(n["id"], current_user)
        if _should_popup(n, st, today):
            candidates.append(_serialize_notice(n))
    candidates.sort(key=lambda x: (PRIORITY_RANK.get(x.get("priority"), 9), x.get("publish_date") or ""))
    primary = candidates[0] if candidates else None
    return {"primary": primary, "notices": candidates[:10]}


class PopupDismissBody(BaseModel):
    notice_id: str


@router.post("/popups/dismiss")
async def dismiss_popup(body: PopupDismissBody, current_user=Depends(_current_user)):
    if (current_user.role or "").lower() == "master":
        return {"message": "ok"}
    notice = await db.notices.find_one({"id": body.notice_id})
    if not notice or not _user_can_view_notice(notice, current_user):
        raise HTTPException(status_code=403, detail="Notice not available")
    today = _ist_day_key()
    st = await _ensure_user_status(body.notice_id, current_user)
    await db.notice_user_status.update_one(
        {"notice_id": body.notice_id, "user_id": current_user.id},
        {
            "$set": {"last_popup_shown_date": today, "updated_at": _utcnow()},
            "$inc": {"popup_show_count": 1},
        },
    )
    return {"message": "Dismiss recorded", "last_popup_shown_date": today}


async def ensure_indexes():
    await db.notices.create_index([("id", 1)], unique=True)
    await db.notices.create_index([("status", 1)])
    await db.notices.create_index([("brand_name", 1)])
    await db.notices.create_index([("publish_date", -1)])
    await db.notices.create_index([("expiry_date", 1)])
    await db.notices.create_index([("status", 1), ("brand_name", 1), ("publish_date", -1)])
    await db.notice_user_status.create_index([("notice_id", 1), ("user_id", 1)], unique=True)
    await db.notice_user_status.create_index([("user_id", 1), ("notice_id", 1)])
    await db.notice_user_status.create_index([("user_id", 1), ("last_popup_shown_date", 1)])
    await db.notice_attachments.create_index([("file_id", 1)], unique=True)
    await db.notice_audit_logs.create_index([("notice_id", 1), ("created_at", -1)])
