"""Testing-only data inventory and cleanup.

Never touches production DB_NAME=nmts or S3 prefixes other than testing/.
Default mode is dry-run. Live delete requires TESTING_LIVE_CLEANUP=true and a
typed confirmation phrase.
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

try:
    from . import testing_runtime
    from . import s3_storage
    from . import testing_overlay as overlay
except ImportError:
    import testing_runtime
    import s3_storage
    import testing_overlay as overlay

logger = logging.getLogger("nmts.testing_cleanup")

CLEAR_PHRASE = "CLEAR TESTING DATA"
MERGE_CLEAR_PHRASE = "MERGE AND CLEAR TEST DATA"
LIVE_CLEANUP_ENV = "TESTING_LIVE_CLEANUP"

TESTING_CREATED_COLLECTIONS = (
    "products", "upload_items", "uploads", "batch_summaries",
    "order_headers", "order_items", "order_requests", "order_activity",
    "request_headers", "stock_reservations",
    "users", "brands", "dealers", "branches",
    "user_alerts", "notices", "queries",
    "activity_logs", "notification_logs",
    "stock_verifications", "stock_verification_sessions", "stock_verification_history",
    "mobile_users", "mobile_devices", "mobile_sessions",
)

PRESERVE_COLLECTIONS = (
    overlay.AUDIT_COLLECTION,
    overlay.OPERATION_COLLECTION,
)


def _env_flag(name: str) -> bool:
    return str(os.getenv(name) or "").strip().lower() in {"1", "true", "yes", "on"}


def live_cleanup_enabled() -> bool:
    return _env_flag(LIVE_CLEANUP_ENV)


def confirm_clear_ok(text: str) -> bool:
    value = str(text or "").strip()
    return value in {CLEAR_PHRASE, MERGE_CLEAR_PHRASE}


def _testing_created_filter() -> dict:
    prefix = testing_runtime.TS_PREFIX
    return {
        "$or": [
            {"data_origin": testing_runtime.DATA_ORIGIN_TESTING},
            {"order_number": {"$regex": f"^{prefix}"}},
            {"request_number": {"$regex": f"^{prefix}"}},
            {"upload_no": {"$regex": f"^{prefix}"}},
            {"user_id": {"$regex": f"^{prefix}"}},
        ]
    }


async def _count(collection, query) -> int:
    try:
        return int(await collection.count_documents(query))
    except Exception:
        return 0


def _s3_testing_prefix() -> str:
    env_name = testing_runtime.storage_env_name()
    if env_name != testing_runtime.TESTING_STORAGE_ENV:
        raise RuntimeError(
            f"Refusing cleanup: NMTS_STORAGE_ENV={env_name!r} is not the testing prefix"
        )
    return f"{env_name}/"


def list_testing_s3_objects(limit: int = 5000) -> Dict[str, Any]:
    prefix = _s3_testing_prefix()
    storage = s3_storage.get_storage()
    if storage.env != testing_runtime.TESTING_STORAGE_ENV:
        raise RuntimeError("Storage env is not testing — S3 cleanup aborted")
    keys = []
    bytes_total = 0
    if storage.is_s3() and getattr(storage, "_client", None) and storage.bucket:
        paginator = storage._client.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=storage.bucket, Prefix=prefix):
            for obj in page.get("Contents") or []:
                key = obj["Key"]
                if not str(key).startswith(prefix):
                    raise RuntimeError(f"Ambiguous S3 key outside testing prefix: {key}")
                keys.append(key)
                bytes_total += int(obj.get("Size") or 0)
                if len(keys) >= limit:
                    break
            if len(keys) >= limit:
                break
    elif hasattr(storage, "_local"):
        root = getattr(storage._local, "root", None)
        if root:
            for path in root.rglob("*"):
                if path.is_file():
                    rel = str(path.relative_to(root)).replace("\\", "/")
                    if rel.startswith(prefix) or prefix.rstrip("/") in str(path):
                        keys.append(rel)
                        bytes_total += path.stat().st_size
    return {
        "prefix": prefix,
        "object_count": len(keys),
        "bytes": bytes_total,
        "keys_preview": keys[:50],
        "keys": keys,
    }


async def inventory(test_db) -> Dict[str, Any]:
    created_filter = _testing_created_filter()
    collections: Dict[str, Any] = {}
    for name in TESTING_CREATED_COLLECTIONS:
        col = test_db[name]
        collections[name] = {
            "testing_created": await _count(col, created_filter),
            "snapshot_copies": await _count(col, {"data_origin": testing_runtime.DATA_ORIGIN_SNAPSHOT}),
            "unlabelled": await _count(col, {"data_origin": {"$exists": False}}),
        }
    collections[overlay.OVERLAY_COLLECTION] = {
        "overlays": await _count(test_db[overlay.OVERLAY_COLLECTION], {}),
    }
    collections[overlay.TOMBSTONE_COLLECTION] = {
        "tombstones": await _count(test_db[overlay.TOMBSTONE_COLLECTION], {}),
    }
    s3_info = list_testing_s3_objects()
    s3_info.pop("keys", None)
    return {
        "db_name": testing_runtime.db_name(),
        "storage_env": testing_runtime.storage_env_name(),
        "collections": collections,
        "s3": s3_info,
        "preserve": list(PRESERVE_COLLECTIONS),
        "retain_snapshot_copies": True,
        "note": (
            "Cleanup deletes testing-created records, overlays and tombstones only. "
            "Copied snapshot rows stay until an approved migration removes them. "
            "Production nmts and non-testing S3 prefixes are never touched."
        ),
        "live_cleanup_enabled": live_cleanup_enabled(),
        "confirm_phrase": CLEAR_PHRASE,
    }


async def cleanup_testing_data(
    test_db,
    *,
    confirm_text: str,
    actor: dict,
    operation_id: str,
    dry_run: Optional[bool] = None,
    reason: str = "manual",
) -> Dict[str, Any]:
    if not testing_runtime.is_testing_env():
        return {"ok": False, "error": "not_testing_env"}
    if testing_runtime.db_name() != testing_runtime.TESTING_DB_NAME:
        return {"ok": False, "error": "unexpected_testing_db"}
    if testing_runtime.storage_env_name() != testing_runtime.TESTING_STORAGE_ENV:
        return {"ok": False, "error": "unexpected_storage_env"}
    if not confirm_clear_ok(confirm_text):
        return {"ok": False, "error": "confirm_phrase_mismatch"}

    snapshot = await inventory(test_db)
    execute = live_cleanup_enabled() if dry_run is None else (not dry_run)
    receipt = {
        "operation_id": operation_id,
        "reason": reason,
        "dry_run": not execute,
        "actor_id": actor.get("id"),
        "actor_role": actor.get("role"),
        "created_at": datetime.now(timezone.utc).isoformat(),
        "inventory": snapshot,
        "deleted": {},
        "s3_deleted": 0,
    }
    if not execute:
        receipt["status"] = "dry_run"
        receipt["message"] = (
            "Dry-run only. Live Testing cleanup is disabled until "
            f"{LIVE_CLEANUP_ENV}=true and operator approval."
        )
        await test_db[overlay.AUDIT_COLLECTION].insert_one(dict(receipt))
        return {"ok": True, "status": "dry_run", "receipt": receipt}

    created_filter = _testing_created_filter()
    deleted: Dict[str, int] = {}
    for name in TESTING_CREATED_COLLECTIONS:
        result = await test_db[name].delete_many(created_filter)
        deleted[name] = int(getattr(result, "deleted_count", 0) or 0)
    ov = await test_db[overlay.OVERLAY_COLLECTION].delete_many({})
    tb = await test_db[overlay.TOMBSTONE_COLLECTION].delete_many({})
    deleted[overlay.OVERLAY_COLLECTION] = int(getattr(ov, "deleted_count", 0) or 0)
    deleted[overlay.TOMBSTONE_COLLECTION] = int(getattr(tb, "deleted_count", 0) or 0)

    s3_info = list_testing_s3_objects()
    storage = s3_storage.get_storage()
    s3_deleted = 0
    prefix = s3_info["prefix"]
    for key in s3_info.get("keys") or []:
        if not str(key).startswith(prefix):
            raise RuntimeError(f"Ambiguous S3 key, aborting: {key}")
        storage.delete(key)
        s3_deleted += 1

    receipt["deleted"] = deleted
    receipt["s3_deleted"] = s3_deleted
    receipt["status"] = "cleaned"
    await test_db[overlay.AUDIT_COLLECTION].insert_one({
        "operation_id": operation_id,
        "pr_number": (testing_runtime.load_deployment_metadata() or {}).get("pr_number"),
        "merged_sha": (testing_runtime.load_deployment_metadata() or {}).get("commit"),
        "actor_id": actor.get("id"),
        "actor_role": actor.get("role"),
        "created_at": receipt["created_at"],
        "reason": reason,
        "status": "cleaned",
        "deleted": deleted,
        "s3_deleted": s3_deleted,
    })
    return {"ok": True, "status": "cleaned", "receipt": receipt}
