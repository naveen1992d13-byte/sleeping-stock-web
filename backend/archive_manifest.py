"""Archive manifest collection helpers for NMTS hybrid storage."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Optional

STATUS_CREATING = "CREATING"
STATUS_UPLOADED = "UPLOADED"
STATUS_VERIFIED = "VERIFIED"
STATUS_FAILED = "FAILED"
STATUS_PRUNED = "PRUNED"

VALID_STATUSES = {
    STATUS_CREATING,
    STATUS_UPLOADED,
    STATUS_VERIFIED,
    STATUS_FAILED,
    STATUS_PRUNED,
}


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def new_archive_id() -> str:
    return str(uuid.uuid4())


def base_manifest(
    *,
    module: str,
    archive_date: Optional[str] = None,
    archive_month: Optional[str] = None,
    storage_key: str = "",
    format: str = "jsonl.gz",
    source_collection: str = "",
) -> Dict[str, Any]:
    return {
        "archive_id": new_archive_id(),
        "module": module,
        "archive_date": archive_date,
        "archive_month": archive_month,
        "storage_key": storage_key,
        "format": format,
        "record_count": 0,
        "file_size": 0,
        "sha256": "",
        "status": STATUS_CREATING,
        "created_at": _utcnow().isoformat(),
        "verified_at": None,
        "source_collection": source_collection,
        "min_date": None,
        "max_date": None,
        "brand_count": 0,
        "dealer_count": 0,
        "branch_count": 0,
        "error": None,
        "eligible_for_prune": False,
    }


async def ensure_archive_indexes(db) -> None:
    await db.archive_manifests.create_index("archive_id", unique=True)
    await db.archive_manifests.create_index(
        [("module", 1), ("archive_date", 1), ("status", 1)],
        name="idx_archive_module_date_status",
    )
    await db.archive_manifests.create_index(
        [("module", 1), ("archive_month", 1), ("status", 1)],
        name="idx_archive_module_month_status",
    )
    await db.archive_manifests.create_index("storage_key")
    await db.archive_job_locks.create_index("lock_key", unique=True)
    await db.order_archive_index.create_index([("archive_month", 1), ("number", 1)])
    await db.request_archive_index.create_index([("archive_month", 1), ("number", 1)])


async def find_verified(db, module: str, archive_date: Optional[str] = None, archive_month: Optional[str] = None):
    q: Dict[str, Any] = {"module": module, "status": STATUS_VERIFIED}
    if archive_date:
        q["archive_date"] = archive_date
    if archive_month:
        q["archive_month"] = archive_month
    return await db.archive_manifests.find_one(q, {"_id": 0})


async def find_any(db, module: str, archive_date: Optional[str] = None, archive_month: Optional[str] = None):
    q: Dict[str, Any] = {"module": module}
    if archive_date:
        q["archive_date"] = archive_date
    if archive_month:
        q["archive_month"] = archive_month
    return await db.archive_manifests.find_one(q, {"_id": 0}, sort=[("created_at", -1)])


async def upsert_manifest(db, doc: Dict[str, Any]) -> Dict[str, Any]:
    archive_id = doc.get("archive_id") or new_archive_id()
    doc["archive_id"] = archive_id
    await db.archive_manifests.update_one(
        {"archive_id": archive_id},
        {"$set": doc},
        upsert=True,
    )
    return await db.archive_manifests.find_one({"archive_id": archive_id}, {"_id": 0})


async def mark_status(db, archive_id: str, status: str, **fields) -> Optional[Dict[str, Any]]:
    if status not in VALID_STATUSES:
        raise ValueError(f"Invalid archive status: {status}")
    update = {"status": status, **fields}
    if status == STATUS_VERIFIED:
        update.setdefault("verified_at", _utcnow().isoformat())
        update.setdefault("eligible_for_prune", True)
    await db.archive_manifests.update_one({"archive_id": archive_id}, {"$set": update})
    return await db.archive_manifests.find_one({"archive_id": archive_id}, {"_id": 0})


async def acquire_job_lock(db, lock_key: str, owner: str, ttl_seconds: int = 3600) -> bool:
    """Atomically acquire a Mongo-backed job lock (CAS).

    Rules:
    - Only one owner may hold an unexpired lock.
    - Expired locks may be reclaimed.
    - The same owner may refresh its own lock.
    """
    now = _utcnow()
    now_ts = now.timestamp()
    expires = now_ts + float(ttl_seconds)
    acquired_at = now.isoformat()
    payload = {
        "lock_key": lock_key,
        "owner": owner,
        "acquired_at": acquired_at,
        "expires_at": expires,
    }

    # 1) Atomically take lock if expired or already owned by us
    claimed = await db.archive_job_locks.find_one_and_update(
        {
            "lock_key": lock_key,
            "$or": [
                {"owner": owner},
                {"expires_at": {"$lte": now_ts}},
                {"expires_at": None},
            ],
        },
        {"$set": payload},
        return_document=True,
    )
    if claimed and claimed.get("owner") == owner:
        return True

    # 2) Insert if missing (unique lock_key prevents double-insert race)
    existing = await db.archive_job_locks.find_one({"lock_key": lock_key})
    if existing is None:
        try:
            await db.archive_job_locks.insert_one(dict(payload))
            row = await db.archive_job_locks.find_one({"lock_key": lock_key})
            return bool(row and row.get("owner") == owner)
        except Exception:
            # DuplicateKeyError / race — fall through to ownership check
            pass

    # 3) One more reclaim attempt if the holder expired during the race
    claimed = await db.archive_job_locks.find_one_and_update(
        {"lock_key": lock_key, "expires_at": {"$lte": now_ts}},
        {"$set": payload},
        return_document=True,
    )
    if claimed and claimed.get("owner") == owner:
        return True

    row = await db.archive_job_locks.find_one({"lock_key": lock_key})
    return bool(
        row
        and row.get("owner") == owner
        and float(row.get("expires_at") or 0) > now_ts
    )


async def release_job_lock(db, lock_key: str, owner: str) -> None:
    """Release only if we still own the lock."""
    await db.archive_job_locks.delete_one({"lock_key": lock_key, "owner": owner})
