"""Nightly archive run ledger — frozen archive_date + per-module status."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")

STATUS_PENDING = "Pending"
STATUS_RUNNING = "Running"
STATUS_VERIFIED = "Verified"
STATUS_FAILED = "Failed"

REQUIRED_MODULES = ("uploads", "product-history", "orders", "requests")


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def ist_calendar_date_iso(now: Optional[datetime] = None) -> str:
    value = now or datetime.now(IST)
    if value.tzinfo is None:
        value = value.replace(tzinfo=IST)
    else:
        value = value.astimezone(IST)
    return value.date().isoformat()


def new_run_id() -> str:
    return str(uuid.uuid4())


def empty_module_status() -> Dict[str, Any]:
    return {
        "status": STATUS_PENDING,
        "result": None,
        "error": None,
        "retries": 0,
        "updated_at": None,
    }


async def ensure_run_indexes(db) -> None:
    await db.archive_runs.create_index("run_id", unique=True)
    await db.archive_runs.create_index([("archive_date", 1), ("started_at", -1)])
    await db.archive_runs.create_index("overall_status")


async def find_run(db, run_id: str) -> Optional[Dict[str, Any]]:
    return await db.archive_runs.find_one({"run_id": run_id}, {"_id": 0})


async def find_run_for_date(db, archive_date: str) -> Optional[Dict[str, Any]]:
    return await db.archive_runs.find_one(
        {"archive_date": archive_date},
        {"_id": 0},
        sort=[("started_at", -1)],
    )


async def latest_run(db) -> Optional[Dict[str, Any]]:
    return await db.archive_runs.find_one({}, {"_id": 0}, sort=[("started_at", -1)])


async def start_or_resume_run(
    db,
    *,
    archive_date: str,
    started_at: Optional[str] = None,
) -> Dict[str, Any]:
    """Create a new run for archive_date, or resume an incomplete one.

    archive_date is frozen at creation and never recomputed.
    """
    existing = await find_run_for_date(db, archive_date)
    if existing and str(existing.get("overall_status")) in {STATUS_PENDING, STATUS_RUNNING, STATUS_FAILED}:
        # Resume incomplete / failed run — keep frozen archive_date & run_id
        await db.archive_runs.update_one(
            {"run_id": existing["run_id"]},
            {"$set": {"overall_status": STATUS_RUNNING, "updated_at": _utcnow_iso()}},
        )
        return await find_run(db, existing["run_id"])

    if existing and str(existing.get("overall_status")) == STATUS_VERIFIED:
        return existing

    run = {
        "run_id": new_run_id(),
        "archive_date": archive_date,
        "started_at": started_at or _utcnow_iso(),
        "completed_at": None,
        "overall_status": STATUS_RUNNING,
        "modules": {m: empty_module_status() for m in REQUIRED_MODULES},
        "retry_count": 0,
        "last_error": None,
        "created_at": _utcnow_iso(),
        "updated_at": _utcnow_iso(),
    }
    await db.archive_runs.insert_one(dict(run))
    return {k: v for k, v in run.items()}


async def mark_module(
    db,
    run_id: str,
    module: str,
    *,
    status: str,
    result: Any = None,
    error: Optional[str] = None,
    increment_retry: bool = False,
) -> Optional[Dict[str, Any]]:
    updates: Dict[str, Any] = {
        f"modules.{module}.status": status,
        f"modules.{module}.result": result,
        f"modules.{module}.error": (error or None),
        f"modules.{module}.updated_at": _utcnow_iso(),
        "updated_at": _utcnow_iso(),
    }
    if error:
        updates["last_error"] = str(error)[:1000]
    inc = {"retry_count": 1, f"modules.{module}.retries": 1} if increment_retry else None
    ops: Dict[str, Any] = {"$set": updates}
    if inc:
        ops["$inc"] = inc
    await db.archive_runs.update_one({"run_id": run_id}, ops)
    return await find_run(db, run_id)


async def finalize_run(db, run_id: str, *, complete: bool, last_error: Optional[str] = None) -> Optional[Dict[str, Any]]:
    status = STATUS_VERIFIED if complete else STATUS_FAILED
    payload = {
        "overall_status": status,
        "completed_at": _utcnow_iso() if complete else None,
        "updated_at": _utcnow_iso(),
    }
    if last_error:
        payload["last_error"] = str(last_error)[:1000]
    await db.archive_runs.update_one({"run_id": run_id}, {"$set": payload})
    return await find_run(db, run_id)


def tonight_card(run: Optional[Dict[str, Any]], *, maintenance_active: bool) -> Dict[str, Any]:
    if not run:
        return {
            "label": "Tonight's Archive",
            "run_id": None,
            "archive_date": None,
            "started_at": None,
            "completed_at": None,
            "overall_status": STATUS_PENDING if maintenance_active else "Idle",
            "modules": {},
            "last_error": None,
            "maintenance_active": maintenance_active,
        }
    return {
        "label": "Tonight's Archive",
        "run_id": run.get("run_id"),
        "archive_date": run.get("archive_date"),
        "started_at": run.get("started_at"),
        "completed_at": run.get("completed_at"),
        "overall_status": run.get("overall_status"),
        "modules": run.get("modules") or {},
        "retry_count": run.get("retry_count") or 0,
        "last_error": run.get("last_error"),
        "maintenance_active": maintenance_active,
    }
