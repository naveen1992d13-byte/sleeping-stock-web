"""Durable Mongo outbox for event-driven REAL S3 archive jobs.

Business HTTP handlers enqueue here and return immediately. The worker writes
to S3, physically verifies, then marks VERIFIED. S3 outages leave Mongo intact.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

from pymongo.errors import DuplicateKeyError

logger = logging.getLogger(__name__)

STATUS_PENDING = "PENDING"
STATUS_RUNNING = "RUNNING"
STATUS_VERIFIED = "VERIFIED"
STATUS_FAILED = "FAILED"

EVENT_UPLOAD_STORED = "upload_stored"
EVENT_UPLOAD_CANCELLED = "upload_cancelled"
EVENT_PUBLISH_COMPLETED = "publish_completed"
EVENT_PUBLISH_SUPERSEDED = "publish_superseded"
EVENT_ORDER_TERMINAL = "order_terminal"
EVENT_REQUEST_TERMINAL = "request_terminal"

MAX_BACKOFF_SECONDS = 3600
CLAIM_TTL_SECONDS = 15 * 60
DRAIN_BATCH = 20

_worker_task: Optional[asyncio.Task] = None
_OWNER = f"archive-outbox-{uuid.uuid4().hex[:8]}"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: Optional[datetime] = None) -> str:
    return (dt or _utcnow()).isoformat()


def backoff_seconds(attempts: int) -> int:
    n = max(0, int(attempts or 0))
    return min(MAX_BACKOFF_SECONDS, 30 * (2 ** min(n, 10)))


def idempotency_key(event_type: str, payload: Dict[str, Any]) -> str:
    explicit = str(payload.get("idempotency_key") or "").strip()
    if explicit:
        return explicit
    if event_type == EVENT_UPLOAD_STORED:
        return f"upload_stored:{payload.get('upload_id')}"
    if event_type == EVENT_UPLOAD_CANCELLED:
        return f"upload_cancelled:{payload.get('upload_id')}"
    if event_type == EVENT_PUBLISH_COMPLETED:
        return f"publish:{payload.get('upload_id')}"
    if event_type == EVENT_PUBLISH_SUPERSEDED:
        return f"supersede:{payload.get('upload_id')}"
    if event_type == EVENT_ORDER_TERMINAL:
        return f"order_terminal:{payload.get('order_id')}:{payload.get('status') or 'terminal'}"
    if event_type == EVENT_REQUEST_TERMINAL:
        return f"request_terminal:{payload.get('request_id')}:{payload.get('status') or 'terminal'}"
    return f"{event_type}:{payload.get('entity_id') or uuid.uuid4()}"


async def enqueue(
    db,
    event_type: str,
    payload: Optional[Dict[str, Any]] = None,
    *,
    idempotency: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """Insert a pending job. Duplicate idempotency_key returns the existing row."""
    payload = dict(payload or {})
    key = idempotency or idempotency_key(event_type, payload)
    existing = await db.archive_outbox.find_one({"idempotency_key": key}, {"_id": 0})
    if existing:
        return existing
    now = _iso()
    doc = {
        "job_id": str(uuid.uuid4()),
        "event_type": event_type,
        "idempotency_key": key,
        "payload": payload,
        "status": STATUS_PENDING,
        "attempts": 0,
        "next_retry_at": now,
        "error": None,
        "created_at": now,
        "updated_at": now,
        "locked_by": None,
        "locked_at": None,
    }
    try:
        await db.archive_outbox.insert_one(dict(doc))
        return {k: v for k, v in doc.items() if k != "_id"}
    except DuplicateKeyError:
        return await db.archive_outbox.find_one({"idempotency_key": key}, {"_id": 0})
    except Exception as exc:
        logger.warning("archive outbox insert failed (%s): %s", event_type, type(exc).__name__)
        return None


async def enqueue_safe(db, event_type: str, payload: Optional[Dict[str, Any]] = None, **kwargs) -> Optional[Dict[str, Any]]:
    """Never raise into HTTP handlers."""
    try:
        return await enqueue(db, event_type, payload, **kwargs)
    except Exception as exc:
        logger.warning("archive outbox enqueue_safe failed (%s): %s", event_type, type(exc).__name__)
        return None


async def claim_next(db, owner: str = _OWNER) -> Optional[Dict[str, Any]]:
    now = _iso()
    claimed = await db.archive_outbox.find_one_and_update(
        {
            "status": {"$in": [STATUS_PENDING, STATUS_FAILED]},
            "next_retry_at": {"$lte": now},
        },
        {
            "$set": {
                "status": STATUS_RUNNING,
                "locked_by": owner,
                "locked_at": now,
                "updated_at": now,
            }
        },
        sort=[("created_at", 1)],
        return_document=True,
    )
    if claimed:
        claimed.pop("_id", None)
    return claimed


async def mark_verified(db, job_id: str, result: Optional[Dict[str, Any]] = None) -> None:
    await db.archive_outbox.update_one(
        {"job_id": job_id},
        {
            "$set": {
                "status": STATUS_VERIFIED,
                "error": None,
                "result": result or {},
                "verified_at": _iso(),
                "updated_at": _iso(),
                "locked_by": None,
                "locked_at": None,
            }
        },
    )


async def mark_failed(db, job_id: str, error: str, attempts: int) -> None:
    nxt = _utcnow() + timedelta(seconds=backoff_seconds(attempts))
    await db.archive_outbox.update_one(
        {"job_id": job_id},
        {
            "$set": {
                "status": STATUS_FAILED,
                "error": str(error or "archive failed")[:1000],
                "attempts": int(attempts),
                "next_retry_at": nxt.isoformat(),
                "updated_at": _iso(),
                "locked_by": None,
                "locked_at": None,
            }
        },
    )


async def process_one(db, job: Dict[str, Any]) -> Dict[str, Any]:
    import event_archive as ea

    event_type = str(job.get("event_type") or "")
    payload = job.get("payload") or {}
    handler = ea.HANDLER_MAP.get(event_type)
    if not handler:
        raise RuntimeError(f"unknown archive event_type: {event_type}")
    return await handler(db, payload)


async def drain_once(db, *, limit: int = DRAIN_BATCH, owner: str = _OWNER) -> Dict[str, Any]:
    processed = 0
    verified = 0
    failed = 0
    for _ in range(max(1, int(limit))):
        job = await claim_next(db, owner=owner)
        if not job:
            break
        processed += 1
        attempts = int(job.get("attempts") or 0) + 1
        await db.archive_outbox.update_one({"job_id": job["job_id"]}, {"$set": {"attempts": attempts}})
        try:
            result = await process_one(db, job)
            status = str((result or {}).get("status") or "")
            if status in {"verified", "already_verified", "already_archived"}:
                await mark_verified(db, job["job_id"], result)
                verified += 1
            else:
                await mark_failed(db, job["job_id"], str((result or {}).get("error") or status or "failed"), attempts)
                failed += 1
        except Exception as exc:
            logger.warning("archive outbox job %s failed: %s", job.get("job_id"), type(exc).__name__)
            await mark_failed(db, job["job_id"], str(exc)[:1000], attempts)
            failed += 1
    return {"processed": processed, "verified": verified, "failed": failed}


async def drain_until_idle(db, *, max_batches: int = 50) -> Dict[str, Any]:
    totals = {"processed": 0, "verified": 0, "failed": 0, "batches": 0}
    for _ in range(max_batches):
        batch = await drain_once(db)
        totals["batches"] += 1
        totals["processed"] += batch["processed"]
        totals["verified"] += batch["verified"]
        totals["failed"] += batch["failed"]
        if batch["processed"] == 0:
            break
    return totals


async def _drain_guarded(db) -> None:
    try:
        await drain_once(db)
    except Exception as exc:
        logger.warning("archive outbox kick drain failed: %s", type(exc).__name__)


def schedule_drain(db) -> None:
    """Background drain so publish/cancel archive immediately without waiting on the scheduler."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    loop.create_task(_drain_guarded(db))
