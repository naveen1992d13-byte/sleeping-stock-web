"""Additive in-app user alerts for Request / Notice / Query only.

Separate from legacy order-assignment `notifications` and email/WhatsApp
`notification_logs`. Does not alter those channels.
"""

from __future__ import annotations

import logging
import re
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

logger = logging.getLogger("nmts.user_alerts")

SOURCE_TYPES = frozenset({"request", "notice", "query"})

router = APIRouter(prefix="/user-alerts", tags=["User Alerts"])
_ALERT_SECURITY = HTTPBearer(auto_error=True)

db = None
_AUTH_DEP = None


def init_user_alerts(database, get_current_user_dep, user_response_model=None):
    global db, _AUTH_DEP
    db = database
    _AUTH_DEP = get_current_user_dep


async def _current_user(credentials: HTTPAuthorizationCredentials = Depends(_ALERT_SECURITY)):
    if _AUTH_DEP is None:
        raise HTTPException(status_code=500, detail="user_alerts not initialized")
    return await _AUTH_DEP(credentials)


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _text(value: Any) -> str:
    return str(value or "").strip()


async def ensure_indexes() -> None:
    if db is None:
        return
    await db.user_alerts.create_index(
        [("recipient_id", 1), ("is_read", 1), ("created_at", -1)],
        name="user_alerts_recipient_unread_created",
    )
    await db.user_alerts.create_index(
        [("recipient_id", 1), ("source_type", 1), ("source_id", 1), ("event", 1)],
        unique=True,
        name="user_alerts_dedupe",
    )


def _default_link(source_type: str) -> str:
    return {
        "request": "/requests",
        "notice": "/notice-board",
        "query": "/query",
    }.get(source_type, "/")


def _request_event_identity(req: dict, event: str) -> str:
    """Immutable per-transition identity so legitimate re-fires at different
    times are not suppressed, while exact duplicate inserts still dedupe."""
    event_l = _text(event).lower()
    token = ""
    if "accept" in event_l or "reject" in event_l:
        token = _text(req.get("decided_at") or req.get("updated_at"))
    elif "dispatch" in event_l:
        token = _text(req.get("dispatched_at") or req.get("updated_at"))
    elif "receive" in event_l:
        token = _text(req.get("received_at") or req.get("updated_at"))
    elif "complete" in event_l:
        token = _text(req.get("completed_at") or req.get("updated_at"))
    elif "cancel" in event_l:
        token = _text(req.get("cancelled_at") or req.get("updated_at") or req.get("decided_at"))
    else:
        token = _text(req.get("updated_at"))
    if not token:
        token = _utcnow_iso()
    return f"{_text(event)}@{token}"


async def create_user_alert(
    *,
    recipient_id: str,
    source_type: str,
    source_id: str,
    event: str,
    title: str,
    message: str = "",
    link_path: str = "",
    brand: str = "",
    dealer: str = "",
    branch: str = "",
) -> Optional[Dict[str, Any]]:
    """Insert one alert. Returns None on duplicate/invalid input (never raises)."""
    if db is None:
        return None
    rid = _text(recipient_id)
    st = _text(source_type).lower()
    sid = _text(source_id)
    ev = _text(event)
    if not rid or st not in SOURCE_TYPES or not sid or not ev:
        return None
    doc = {
        "id": str(uuid.uuid4()),
        "recipient_id": rid,
        "source_type": st,
        "source_id": sid,
        "event": ev,
        "title": _text(title)[:200] or ev,
        "message": _text(message)[:500],
        "link_path": _text(link_path) or _default_link(st),
        "is_read": False,
        "created_at": _utcnow_iso(),
        "brand": _text(brand),
        "dealer": _text(dealer),
        "branch": _text(branch),
    }
    try:
        await db.user_alerts.insert_one(doc)
        doc.pop("_id", None)
        return doc
    except Exception as exc:  # DuplicateKeyError or transient
        if "duplicate" in str(exc).lower() or getattr(exc, "code", None) == 11000:
            return None
        logger.warning("user_alert insert failed: %s", type(exc).__name__)
        return None


async def create_alerts_for_recipients(
    recipient_ids: List[str],
    *,
    source_type: str,
    source_id: str,
    event: str,
    title: str,
    message: str = "",
    link_path: str = "",
    brand: str = "",
    dealer: str = "",
    branch: str = "",
    exclude_ids: Optional[List[str]] = None,
) -> int:
    exclude = {_text(x) for x in (exclude_ids or []) if _text(x)}
    seen = set()
    created = 0
    for rid in recipient_ids or []:
        rid = _text(rid)
        if not rid or rid in exclude or rid in seen:
            continue
        seen.add(rid)
        doc = await create_user_alert(
            recipient_id=rid,
            source_type=source_type,
            source_id=source_id,
            event=event,
            title=title,
            message=message,
            link_path=link_path,
            brand=brand,
            dealer=dealer,
            branch=branch,
        )
        if doc:
            created += 1
    return created


async def resolve_user_ids_by_emails(emails: List[str]) -> List[str]:
    if db is None or not emails:
        return []
    cleaned = sorted({str(e).strip().lower() for e in emails if e and str(e).strip()})
    if not cleaned:
        return []
    users = await db.users.find(
        {"status": {"$regex": "^active$", "$options": "i"}},
        {"_id": 0, "id": 1, "email": 1},
    ).to_list(5000)
    wanted = set(cleaned)
    out, seen = [], set()
    for u in users:
        em = _text(u.get("email")).lower()
        uid = _text(u.get("id"))
        if em in wanted and uid and uid not in seen:
            seen.add(uid)
            out.append(uid)
    return out


async def resolve_user_id_flexible(value: str) -> Optional[str]:
    if db is None:
        return None
    raw = _text(value)
    if not raw:
        return None
    user = await db.users.find_one({"id": raw}, {"_id": 0, "id": 1})
    if user:
        return user.get("id")
    user = await db.users.find_one({"user_id": raw}, {"_id": 0, "id": 1})
    if user:
        return user.get("id")
    user = await db.users.find_one(
        {"email": {"$regex": f"^{re.escape(raw)}$", "$options": "i"}},
        {"_id": 0, "id": 1},
    )
    return (user or {}).get("id")


async def active_user_ids_for_request_scope(
    brand: str = None, dealer: str = None, branch: str = None
) -> List[str]:
    """Match server._active_recipients_for_scope: admin at supplying
    dealer+branch, plus masters for the supplying brand only."""
    if db is None:
        return []
    query: Dict[str, Any] = {
        "status": {"$regex": "^active$", "$options": "i"},
        "role": {"$in": ["admin", "master"]},
    }
    scope_or = []
    if dealer and branch:
        # Prefer legacy group/location (email/WhatsApp path) and also dealer/branch
        # field variants used by some user records.
        scope_or.append({"group": dealer, "location": branch})
        scope_or.append({"dealer": dealer, "branch": branch})
    if brand:
        scope_or.append({"brand": brand, "role": "master"})
    if scope_or:
        query["$or"] = scope_or
    else:
        # No scope → do not fan out to every admin/master
        return []
    users = await db.users.find(query, {"_id": 0, "id": 1}).to_list(200)
    return [_text(u.get("id")) for u in users if _text(u.get("id"))]


async def alert_request_event(req: dict, event: str, *, actor_id: str = "") -> int:
    """Bell recipients mirror email/WhatsApp: requester + supplying-scope
    admin/master. Actor is excluded."""
    source_id = _text(req.get("id") or req.get("request_number"))
    if not source_id:
        return 0
    recipient_ids: List[str] = []
    requester = await resolve_user_id_flexible(req.get("requested_by") or "")
    if requester:
        recipient_ids.append(requester)
    if req.get("requester_email"):
        recipient_ids.extend(await resolve_user_ids_by_emails([req.get("requester_email")]))
    recipient_ids.extend(
        await active_user_ids_for_request_scope(
            req.get("supplying_brand"),
            req.get("supplying_dealer"),
            req.get("supplying_branch"),
        )
    )
    title = _text(event) or "Request update"
    part = _text(req.get("part_number"))
    req_no = _text(req.get("request_number") or req.get("id"))
    message = f"{req_no}" + (f" · {part}" if part else "")
    event_key = _request_event_identity(req, event)
    exclude = [actor_id] if actor_id else []
    # Also exclude decided_by / *_by fields when actor_id was omitted
    for key in ("decided_by", "dispatched_by", "received_by", "completed_by", "cancelled_by"):
        if req.get(key):
            exclude.append(_text(req.get(key)))
    return await create_alerts_for_recipients(
        recipient_ids,
        source_type="request",
        source_id=source_id,
        event=event_key,
        title=title,
        message=message,
        link_path="/requests",
        brand=_text(req.get("supplying_brand") or req.get("requesting_brand")),
        dealer=_text(req.get("supplying_dealer") or req.get("requesting_dealer")),
        branch=_text(req.get("supplying_branch") or req.get("requesting_branch")),
        exclude_ids=exclude,
    )


async def eligible_notice_recipient_ids(notice: dict) -> List[str]:
    """Align with notice_board._eligible_users_query (admin/user by brand)
    plus active masters who can always view notices."""
    if db is None or not notice:
        return []
    # Eligible admin/user (same as Notice Board ack audience)
    filt: Dict[str, Any] = {
        "role": {"$in": ["admin", "user"]},
        "status": {"$regex": "^active$", "$options": "i"},
    }
    if notice.get("audience_type") == "selected_brand":
        brand = _text(notice.get("brand_name"))
        if brand:
            filt["brand"] = {"$regex": f"^{re.escape(brand)}$", "$options": "i"}
        else:
            return []
    users = await db.users.find(filt, {"_id": 0, "id": 1}).to_list(5000)
    ids = [_text(u.get("id")) for u in users if _text(u.get("id"))]
    masters = await db.users.find(
        {"role": "master", "status": {"$regex": "^active$", "$options": "i"}},
        {"_id": 0, "id": 1},
    ).to_list(200)
    ids.extend(_text(u.get("id")) for u in masters if _text(u.get("id")))
    return ids


async def alert_notice_published(notice: dict) -> int:
    if db is None or not notice:
        return 0
    notice_id = _text(notice.get("id"))
    if not notice_id:
        return 0
    ids = await eligible_notice_recipient_ids(notice)
    title = _text(notice.get("title")) or "New notice published"
    published_token = _text(notice.get("published_at") or notice.get("updated_at")) or _utcnow_iso()
    return await create_alerts_for_recipients(
        ids,
        source_type="notice",
        source_id=notice_id,
        event=f"notice_published@{published_token}",
        title=title,
        message=_text(notice.get("priority") or "Notice"),
        link_path="/notice-board",
        brand=_text(notice.get("brand_name")),
    )


async def alert_query_reply(query_doc: dict) -> int:
    """Software Team (master) reply → notify query creator only."""
    raised = (query_doc or {}).get("raised_by") or {}
    creator = await resolve_user_id_flexible(raised.get("user_id") or "")
    if not creator:
        return 0
    qid = _text(query_doc.get("id"))
    qno = _text(query_doc.get("query_no"))
    replies = query_doc.get("replies") or []
    last = replies[-1] if replies else {}
    reply_id = _text(last.get("reply_id")) or f"n{len(replies)}"
    return await create_alerts_for_recipients(
        [creator],
        source_type="query",
        source_id=f"{qid}:reply:{reply_id}",
        event="query_reply",
        title=f"Reply on {qno or 'query'}",
        message=_text(query_doc.get("subject")),
        link_path="/query",
    )


async def alert_query_follow_up(query_doc: dict, *, actor_id: str = "") -> int:
    """Creator follow-up → notify Software Team (all active masters)."""
    if db is None:
        return 0
    masters = await db.users.find(
        {"role": "master", "status": {"$regex": "^active$", "$options": "i"}},
        {"_id": 0, "id": 1},
    ).to_list(200)
    ids = [_text(u.get("id")) for u in masters if _text(u.get("id"))]
    qid = _text(query_doc.get("id"))
    qno = _text(query_doc.get("query_no"))
    follow_ups = query_doc.get("follow_ups") or []
    last = follow_ups[-1] if follow_ups else {}
    follow_id = _text(last.get("follow_up_id")) or f"n{len(follow_ups)}"
    return await create_alerts_for_recipients(
        ids,
        source_type="query",
        source_id=f"{qid}:followup:{follow_id}",
        event="query_follow_up",
        title=f"Follow-up on {qno or 'query'}",
        message=_text(query_doc.get("subject")),
        link_path="/query",
        exclude_ids=[actor_id] if actor_id else None,
    )


def _serialize(doc: dict) -> dict:
    out = dict(doc or {})
    out.pop("_id", None)
    created = out.get("created_at")
    if hasattr(created, "isoformat"):
        out["created_at"] = created.isoformat()
    return out


@router.get("")
async def list_user_alerts(
    unread_only: bool = False,
    limit: int = 50,
    current_user=Depends(_current_user),
):
    lim = max(1, min(int(limit or 50), 100))
    q: Dict[str, Any] = {
        "recipient_id": current_user.id,
        "source_type": {"$in": list(SOURCE_TYPES)},
    }
    if unread_only:
        q["is_read"] = False
    rows = await db.user_alerts.find(q, {"_id": 0}).sort("created_at", -1).to_list(lim)
    return [_serialize(r) for r in rows]


@router.get("/unread-count")
async def user_alerts_unread_count(current_user=Depends(_current_user)):
    count = await db.user_alerts.count_documents(
        {
            "recipient_id": current_user.id,
            "is_read": False,
            "source_type": {"$in": list(SOURCE_TYPES)},
        }
    )
    return {"count": int(count)}


@router.put("/{alert_id}/read")
async def mark_user_alert_read(alert_id: str, current_user=Depends(_current_user)):
    result = await db.user_alerts.update_one(
        {"id": alert_id, "recipient_id": current_user.id},
        {"$set": {"is_read": True}},
    )
    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="Alert not found")
    return {"message": "Alert marked as read"}


@router.put("/read-all")
async def mark_all_user_alerts_read(current_user=Depends(_current_user)):
    await db.user_alerts.update_many(
        {
            "recipient_id": current_user.id,
            "source_type": {"$in": list(SOURCE_TYPES)},
            "is_read": False,
        },
        {"$set": {"is_read": True}},
    )
    return {"message": "All alerts marked as read"}
