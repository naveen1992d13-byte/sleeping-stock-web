"""NMTS Query Desk — common support queries visible to all authenticated roles."""
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
from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError

router = APIRouter(prefix="/queries", tags=["Query Desk"])
QUERY_STORAGE = Path(os.getenv("QUERY_DESK_STORAGE_DIR", Path(__file__).parent / "query_attachments"))
QUERY_STORAGE.mkdir(parents=True, exist_ok=True)

_QUERY_SECURITY = HTTPBearer()
_AUTH_DEP = None
db = None

QUERY_TYPES = {"System", "General", "Guidance"}
QUERY_STATUSES = {"Open", "Answered", "Reopened", "Closed"}
ALLOWED_EXTENSIONS = {".png", ".jpg", ".jpeg", ".pdf", ".xls", ".xlsx"}
MAX_ATTACHMENT_BYTES = 5 * 1024 * 1024
IST = ZoneInfo("Asia/Kolkata")

CONTENT_TYPES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".pdf": "application/pdf",
    ".xls": "application/vnd.ms-excel",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
}


def init_query_desk(database, get_current_user, UserResponse):
    globals()["db"] = database
    globals()["_AUTH_DEP"] = get_current_user
    globals()["UserResponse"] = UserResponse


async def _current_user(credentials: HTTPAuthorizationCredentials = Depends(_QUERY_SECURITY)):
    if _AUTH_DEP is None:
        raise HTTPException(status_code=500, detail="Query Desk authentication is not initialized")
    return await _AUTH_DEP(credentials)


def _role_label(role: str) -> str:
    value = (role or "").lower()
    if value == "master":
        return "Master Admin"
    if value == "admin":
        return "Admin"
    return "User"


def _is_exact_scope(value: Optional[str]) -> bool:
    text = str(value or "").strip()
    return bool(text) and text != "N/A" and not text.lower().startswith("all ")


def _sanitize_text(value: str, max_len: Optional[int] = None) -> str:
    text = re.sub(r"<script\b[^>]*>.*?</script>", "", str(value or ""), flags=re.I | re.S).strip()
    if max_len is not None:
        text = text[:max_len]
    return text


def _parse_dt(value) -> Optional[datetime]:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).astimezone(timezone.utc)
    except ValueError:
        return None


def _serialize_query(doc: dict) -> dict:
    if not doc:
        return doc
    out = dict(doc)
    out.pop("_id", None)
    out.pop("attachment_storage_path", None)
    for reply in out.get("replies") or []:
        reply.pop("attachment_storage_path", None)
    for item in out.get("follow_ups") or []:
        item.pop("attachment_storage_path", None)
    return out


async def _lookup_master_id(collection: str, name: str, extra_filter: Optional[dict] = None) -> str:
    clean = _sanitize_text(name)
    if not clean:
        return ""
    filt = {"name": {"$regex": f"^{re.escape(clean)}$", "$options": "i"}}
    if extra_filter:
        filt.update(extra_filter)
    row = await db[collection].find_one(filt, {"_id": 0, "id": 1, "code": 1})
    if not row:
        return ""
    return str(row.get("id") or row.get("code") or "")


async def _resolve_query_scope(current_user, brand: Optional[str], dealer: Optional[str], branch: Optional[str]) -> dict:
    role = (current_user.role or "").lower()
    brand_name = current_user.brand or ""
    dealer_name = current_user.group or ""
    branch_name = current_user.location or ""

    if role == "master":
        if _is_exact_scope(brand):
            brand_name = _sanitize_text(brand)
        if _is_exact_scope(dealer):
            dealer_name = _sanitize_text(dealer)
        if _is_exact_scope(branch):
            branch_name = _sanitize_text(branch)
    elif role == "admin":
        if _is_exact_scope(branch):
            branch_name = _sanitize_text(branch)
    # Users always use assigned profile scope.

    brand_id = await _lookup_master_id("brands", brand_name)
    dealer_id = await _lookup_master_id("dealers", dealer_name, {"brand": {"$regex": f"^{re.escape(brand_name)}$", "$options": "i"}} if brand_name else None)
    branch_id = await _lookup_master_id(
        "branches",
        branch_name,
        {
            "$or": [
                {"dealer": {"$regex": f"^{re.escape(dealer_name)}$", "$options": "i"}},
                {"dealer_name": {"$regex": f"^{re.escape(dealer_name)}$", "$options": "i"}},
            ]
        }
        if dealer_name
        else None,
    )

    return {
        "brand_id": brand_id,
        "brand_name": brand_name,
        "dealer_id": dealer_id,
        "dealer_name": dealer_name,
        "branch_id": branch_id,
        "branch_name": branch_name,
    }


async def _next_query_number() -> str:
    india_now = datetime.now(IST)
    date_key = india_now.strftime("%y%m%d")
    counter_id = f"query_desk_{date_key}"
    counter = await db.counters.find_one_and_update(
        {"_id": counter_id},
        {"$inc": {"seq": 1}, "$setOnInsert": {"date_key": date_key}},
        upsert=True,
        return_document=ReturnDocument.AFTER,
    )
    seq = int(counter.get("seq", 1))
    if seq > 9999:
        raise HTTPException(status_code=500, detail="Daily query serial exhausted")
    return f"QRY{date_key}{seq:04d}"


async def _save_attachment(file: UploadFile, prefix: str) -> dict:
    filename = Path(file.filename or "").name
    ext = Path(filename).suffix.lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(status_code=400, detail="Attachment type not allowed")
    content = await file.read()
    if not content:
        raise HTTPException(status_code=400, detail="Attachment is empty")
    if len(content) > MAX_ATTACHMENT_BYTES:
        raise HTTPException(status_code=400, detail="Attachment exceeds 5 MB limit")

    file_id = str(uuid.uuid4())
    safe_name = f"{prefix}_{file_id}{ext}"
    path = QUERY_STORAGE / safe_name
    path.write_bytes(content)  # legacy local fallback retained
    stored = {}
    try:
        try:
            import file_objects
        except ImportError:
            from . import file_objects
        stored = await file_objects.store_bytes(
            module="queries",
            relative_key=f"{prefix}/{safe_name}",
            data=content,
            original_filename=filename,
            content_type=file.content_type or CONTENT_TYPES.get(ext, "application/octet-stream"),
        )
    except Exception:
        stored = {}

    return {
        "file_id": file_id,
        "file_name": filename,
        "file_url": f"/api/queries/attachments/{file_id}",
        "content_type": file.content_type or CONTENT_TYPES.get(ext, "application/octet-stream"),
        "file_size": len(content),
        "attachment_storage_path": str(path),
        "storage_provider": stored.get("storage_provider"),
        "storage_key": stored.get("storage_key"),
        "sha256": stored.get("sha256"),
        "archived_at": stored.get("archived_at"),
    }


def _list_projection() -> dict:
    return {
        "_id": 0,
        "id": 1,
        "query_no": 1,
        "query_type": 1,
        "subject": 1,
        "status": 1,
        "scope.dealer_name": 1,
        "scope.branch_name": 1,
        "raised_by.user_name": 1,
        "created_at": 1,
        "updated_at": 1,
    }


def _build_list_filter(search: Optional[str], query_type: Optional[str], status: Optional[str]) -> dict:
    filt: dict = {}
    query_type = _sanitize_text(query_type or "")
    status = _sanitize_text(status or "")
    if query_type:
        if query_type not in QUERY_TYPES:
            raise HTTPException(status_code=400, detail="Invalid query type filter")
        filt["query_type"] = query_type
    if status:
        if status not in QUERY_STATUSES:
            raise HTTPException(status_code=400, detail="Invalid status filter")
        filt["status"] = status
    search = _sanitize_text(search or "")
    if search:
        safe = re.escape(search)
        filt["$or"] = [
            {"query_no": {"$regex": safe, "$options": "i"}},
            {"subject": {"$regex": safe, "$options": "i"}},
        ]
    return filt


def _require_master(current_user):
    if (current_user.role or "").lower() != "master":
        raise HTTPException(status_code=403, detail="Only the Software Team can perform this action")


def _user_identity_ids(current_user) -> set:
    ids = set()
    for value in (getattr(current_user, "id", None), getattr(current_user, "user_id", None)):
        text = str(value or "").strip()
        if text:
            ids.add(text)
    return ids


def _is_query_creator(current_user, doc: dict) -> bool:
    raised = (doc or {}).get("raised_by") or {}
    creator_id = str(raised.get("user_id") or "").strip()
    if not creator_id:
        return False
    return creator_id in _user_identity_ids(current_user)


def _system_event(message: str, now: datetime) -> dict:
    return {
        "event_id": str(uuid.uuid4()),
        "type": "SYSTEM_STATUS",
        "message": _sanitize_text(message, 500),
        "created_at": now,
    }


def _validate_status_transition(current_status: str, new_status: str):
    if new_status not in QUERY_STATUSES:
        raise HTTPException(status_code=400, detail="Invalid status")
    if current_status == new_status:
        return
    allowed = {
        "Open": {"Answered", "Closed"},
        "Answered": {"Reopened", "Closed"},
        "Reopened": {"Answered", "Closed"},
        "Closed": {"Open"},
    }
    if new_status not in allowed.get(current_status, set()):
        raise HTTPException(status_code=400, detail=f"Cannot change status from {current_status} to {new_status}")


@router.post("")
async def create_query(
    query_type: str = Form(...),
    subject: str = Form(...),
    description: str = Form(...),
    brand: Optional[str] = Form(None),
    dealer: Optional[str] = Form(None),
    branch: Optional[str] = Form(None),
    attachment: Optional[UploadFile] = File(None),
    current_user=Depends(_current_user),
):
    clean_type = _sanitize_text(query_type)
    if clean_type not in QUERY_TYPES:
        raise HTTPException(status_code=400, detail="Invalid query type")
    clean_subject = _sanitize_text(subject, 200)
    clean_description = _sanitize_text(description, 5000)
    if not clean_subject:
        raise HTTPException(status_code=400, detail="Subject is required")
    if not clean_description:
        raise HTTPException(status_code=400, detail="Description is required")

    now = datetime.now(timezone.utc)
    scope = await _resolve_query_scope(current_user, brand, dealer, branch)

    attachment_meta = None
    if attachment and attachment.filename:
        saved = await _save_attachment(attachment, "query")
        attachment_meta = saved

    doc = None
    for _attempt in range(3):
        query_no = await _next_query_number()
        query_id = str(uuid.uuid4())
        payload = {
            "id": query_id,
            "query_no": query_no,
            "query_type": clean_type,
            "subject": clean_subject,
            "description": clean_description,
            "attachment": None,
            "raised_by": {
                "user_id": current_user.user_id or current_user.id,
                "user_name": current_user.username,
                "role": _role_label(current_user.role),
            },
            "scope": scope,
            "status": "Open",
            "replies": [],
            "follow_ups": [],
            "events": [],
            "created_at": now,
            "updated_at": now,
            "raised_at": now,
            "closed_at": None,
            "closed_by": None,
        }
        if attachment_meta:
            await db.query_attachments.insert_one(
                {
                    "file_id": attachment_meta["file_id"],
                    "query_id": query_id,
                    "reply_id": None,
                    "file_name": attachment_meta["file_name"],
                    "content_type": attachment_meta["content_type"],
                    "file_size": attachment_meta["file_size"],
                    "storage_path": attachment_meta["attachment_storage_path"],
                    "storage_provider": attachment_meta.get("storage_provider"),
                    "storage_key": attachment_meta.get("storage_key"),
                    "sha256": attachment_meta.get("sha256"),
                    "archived_at": attachment_meta.get("archived_at"),
                    "created_at": now,
                }
            )
            payload["attachment"] = {
                "file_id": attachment_meta["file_id"],
                "file_name": attachment_meta["file_name"],
                "file_url": attachment_meta["file_url"],
                "content_type": attachment_meta["content_type"],
                "file_size": attachment_meta["file_size"],
            }
        try:
            await db.queries.insert_one(payload)
            doc = payload
            break
        except DuplicateKeyError:
            if attachment_meta:
                await db.query_attachments.delete_one({"file_id": attachment_meta["file_id"]})
            continue
    if not doc:
        raise HTTPException(status_code=500, detail="Unable to allocate query number")

    return _serialize_query(doc)


@router.get("")
async def list_queries(
    page: int = 1,
    page_size: int = 20,
    search: Optional[str] = None,
    query_type: Optional[str] = None,
    status: Optional[str] = None,
    current_user=Depends(_current_user),
):
    page = max(page, 1)
    page_size = max(1, min(page_size, 100))
    filt = _build_list_filter(search, query_type, status)
    total = await db.queries.count_documents(filt)
    rows = (
        await db.queries.find(filt, _list_projection())
        .sort("created_at", -1)
        .skip((page - 1) * page_size)
        .limit(page_size)
        .to_list(page_size)
    )
    return {
        "records": rows,
        "total": total,
        "page": page,
        "page_size": page_size,
        "total_pages": max(1, (total + page_size - 1) // page_size),
    }


@router.get("/similar")
async def similar_queries(subject: str = "", current_user=Depends(_current_user)):
    clean = _sanitize_text(subject, 200)
    if len(clean) < 3:
        return {"records": []}
    safe = re.escape(clean)
    rows = (
        await db.queries.find(
            {"subject": {"$regex": safe, "$options": "i"}},
            {"_id": 0, "id": 1, "query_no": 1, "subject": 1, "query_type": 1, "status": 1},
        )
        .sort("created_at", -1)
        .limit(5)
        .to_list(5)
    )
    return {"records": rows}


@router.get("/attachments/{file_id}")
async def download_attachment(file_id: str, current_user=Depends(_current_user)):
    meta = await db.query_attachments.find_one({"file_id": file_id})
    if not meta:
        raise HTTPException(status_code=404, detail="Attachment not found")
    try:
        import file_objects
    except ImportError:
        from . import file_objects
    if meta.get("storage_path") and not meta.get("attachment_storage_path"):
        meta = {**meta, "attachment_storage_path": meta.get("storage_path")}
    if not file_objects.meta_has_readable_bytes(meta):
        raise HTTPException(status_code=404, detail="Attachment file not found")
    return file_objects.streaming_response_from_meta(
        meta,
        filename=meta.get("file_name") or "attachment.bin",
    )


@router.get("/{query_id}")
async def get_query(query_id: str, current_user=Depends(_current_user)):
    doc = await db.queries.find_one({"id": query_id}, {"_id": 0})
    if not doc:
        raise HTTPException(status_code=404, detail="Query not found")
    return _serialize_query(doc)


@router.post("/{query_id}/reply")
async def reply_to_query(
    query_id: str,
    message: str = Form(...),
    attachment: Optional[UploadFile] = File(None),
    current_user=Depends(_current_user),
):
    _require_master(current_user)
    clean_message = _sanitize_text(message, 5000)
    if not clean_message:
        raise HTTPException(status_code=400, detail="Reply message is required")

    existing = await db.queries.find_one({"id": query_id}, {"_id": 0})
    if not existing:
        raise HTTPException(status_code=404, detail="Query not found")
    status = existing.get("status") or "Open"
    if status == "Closed":
        raise HTTPException(status_code=400, detail="Cannot reply to a closed query")
    if status not in {"Open", "Reopened"}:
        raise HTTPException(status_code=400, detail="Query is not waiting for a Software Team reply")

    now = datetime.now(timezone.utc)
    reply_id = str(uuid.uuid4())
    reply_doc = {
        "reply_id": reply_id,
        "message": clean_message,
        "attachment": None,
        "replied_by_user_id": current_user.user_id or current_user.id,
        "replied_by_name": current_user.username,
        "replied_by_role": "Master Admin",
        "replied_at": now,
    }
    if attachment and attachment.filename:
        saved = await _save_attachment(attachment, "reply")
        await db.query_attachments.insert_one(
            {
                "file_id": saved["file_id"],
                "query_id": query_id,
                "reply_id": reply_id,
                "file_name": saved["file_name"],
                "content_type": saved["content_type"],
                "file_size": saved["file_size"],
                "storage_path": saved["attachment_storage_path"],
                "storage_provider": saved.get("storage_provider"),
                "storage_key": saved.get("storage_key"),
                "sha256": saved.get("sha256"),
                "archived_at": saved.get("archived_at"),
                "created_at": now,
            }
        )
        reply_doc["attachment"] = {
            "file_id": saved["file_id"],
            "file_name": saved["file_name"],
            "file_url": saved["file_url"],
            "content_type": saved["content_type"],
            "file_size": saved["file_size"],
        }

    update = {
        "$push": {"replies": reply_doc},
        "$set": {"status": "Answered", "updated_at": now},
    }
    doc = await db.queries.find_one_and_update(
        {"id": query_id, "status": {"$in": ["Open", "Reopened"]}},
        update,
        return_document=ReturnDocument.AFTER,
        projection={"_id": 0},
    )
    if not doc:
        raise HTTPException(status_code=400, detail="Query is not waiting for a Software Team reply")
    try:
        import user_alerts as ua

        await ua.alert_query_reply(doc)
    except Exception:
        pass
    return _serialize_query(doc)


@router.post("/{query_id}/follow-up")
async def add_follow_up(
    query_id: str,
    message: str = Form(...),
    attachment: Optional[UploadFile] = File(None),
    current_user=Depends(_current_user),
):
    clean_message = _sanitize_text(message, 5000)
    if not clean_message:
        raise HTTPException(status_code=400, detail="Description is required")

    existing = await db.queries.find_one({"id": query_id}, {"_id": 0})
    if not existing:
        raise HTTPException(status_code=404, detail="Query not found")
    if existing.get("status") == "Closed":
        raise HTTPException(status_code=400, detail="Cannot follow up on a closed query")
    if existing.get("status") != "Answered":
        raise HTTPException(status_code=400, detail="Follow-up is only allowed after a Software Team reply")
    if not _is_query_creator(current_user, existing):
        raise HTTPException(status_code=403, detail="Only the query creator can send a follow-up")

    now = datetime.now(timezone.utc)
    follow_id = str(uuid.uuid4())
    follow_doc = {
        "follow_up_id": follow_id,
        "message": clean_message,
        "attachment": None,
        "sender_user_id": current_user.user_id or current_user.id,
        "sender_name": current_user.username,
        "sender_role": _role_label(current_user.role),
        "created_at": now,
    }
    if attachment and attachment.filename:
        saved = await _save_attachment(attachment, "followup")
        await db.query_attachments.insert_one(
            {
                "file_id": saved["file_id"],
                "query_id": query_id,
                "reply_id": follow_id,
                "file_name": saved["file_name"],
                "content_type": saved["content_type"],
                "file_size": saved["file_size"],
                "storage_path": saved["attachment_storage_path"],
                "storage_provider": saved.get("storage_provider"),
                "storage_key": saved.get("storage_key"),
                "sha256": saved.get("sha256"),
                "archived_at": saved.get("archived_at"),
                "created_at": now,
            }
        )
        follow_doc["attachment"] = {
            "file_id": saved["file_id"],
            "file_name": saved["file_name"],
            "file_url": saved["file_url"],
            "content_type": saved["content_type"],
            "file_size": saved["file_size"],
        }

    doc = await db.queries.find_one_and_update(
        {"id": query_id, "status": "Answered"},
        {
            "$push": {"follow_ups": follow_doc},
            "$set": {"status": "Reopened", "updated_at": now},
        },
        return_document=ReturnDocument.AFTER,
        projection={"_id": 0},
    )
    if not doc:
        raise HTTPException(status_code=400, detail="Unable to send follow-up for this query state")
    try:
        import user_alerts as ua

        await ua.alert_query_follow_up(doc, actor_id=getattr(current_user, "id", "") or "")
    except Exception:
        pass
    return _serialize_query(doc)


@router.post("/{query_id}/clear")
async def mark_query_cleared(query_id: str, current_user=Depends(_current_user)):
    existing = await db.queries.find_one({"id": query_id}, {"_id": 0})
    if not existing:
        raise HTTPException(status_code=404, detail="Query not found")
    if existing.get("status") == "Closed":
        raise HTTPException(status_code=400, detail="Query is already closed")
    if existing.get("status") != "Answered":
        raise HTTPException(status_code=400, detail="Query is not waiting for your confirmation")
    if not _is_query_creator(current_user, existing):
        raise HTTPException(status_code=403, detail="Only the query creator can mark this query as cleared")

    now = datetime.now(timezone.utc)
    creator_name = _sanitize_text(current_user.username, 200)
    event = _system_event(f"Query marked as cleared by {creator_name}", now)
    doc = await db.queries.find_one_and_update(
        {"id": query_id, "status": "Answered"},
        {
            "$push": {"events": event},
            "$set": {
                "status": "Closed",
                "updated_at": now,
                "closed_at": now,
                "closed_by": {
                    "user_id": current_user.user_id or current_user.id,
                    "user_name": creator_name,
                    "role": _role_label(current_user.role),
                },
                "close_type": "creator_cleared",
            },
        },
        return_document=ReturnDocument.AFTER,
        projection={"_id": 0},
    )
    if not doc:
        raise HTTPException(status_code=400, detail="Unable to close query in its current state")
    return _serialize_query(doc)


@router.patch("/{query_id}/status")
async def update_query_status(query_id: str, payload: Dict[str, Any], current_user=Depends(_current_user)):
    _require_master(current_user)
    new_status = _sanitize_text((payload or {}).get("status"))
    if not new_status:
        raise HTTPException(status_code=400, detail="Status is required")

    existing = await db.queries.find_one({"id": query_id}, {"_id": 0, "status": 1})
    if not existing:
        raise HTTPException(status_code=404, detail="Query not found")

    current_status = existing.get("status") or "Open"
    _validate_status_transition(current_status, new_status)

    now = datetime.now(timezone.utc)
    update_fields = {"status": new_status, "updated_at": now}
    update_ops: dict = {"$set": update_fields}
    if new_status == "Closed":
        update_fields["closed_at"] = now
        update_fields["closed_by"] = {
            "user_id": current_user.user_id or current_user.id,
            "user_name": current_user.username,
            "role": "Master Admin",
        }
        update_fields["close_type"] = "master_manual"
        update_ops["$push"] = {"events": _system_event("Query closed manually by Master Admin", now)}
    elif new_status == "Open":
        update_fields["closed_at"] = None
        update_fields["closed_by"] = None
        update_fields["close_type"] = None

    doc = await db.queries.find_one_and_update(
        {"id": query_id},
        update_ops,
        return_document=ReturnDocument.AFTER,
        projection={"_id": 0},
    )
    return _serialize_query(doc)


async def ensure_indexes():
    await db.queries.create_index([("query_no", 1)], unique=True)
    await db.queries.create_index([("created_at", -1)])
    await db.queries.create_index([("status", 1), ("created_at", -1)])
    await db.queries.create_index([("query_type", 1), ("created_at", -1)])
    try:
        await db.queries.create_index([("subject", "text"), ("description", "text")])
    except Exception:
        pass
    await db.query_attachments.create_index([("file_id", 1)], unique=True)
