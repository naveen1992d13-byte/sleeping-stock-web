"""Async Production deployment verification after a Testing GitHub merge.

Polling is independent of the original HTTP request and is persisted so it
can resume after a backend restart. Live cleanup is never implied here.
"""
from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, timezone
from typing import Any, Dict, Optional

try:
    from . import testing_github as tg
    from . import testing_overlay as ov
    from . import testing_cleanup as tc
except ImportError:
    import testing_github as tg
    import testing_overlay as ov
    import testing_cleanup as tc

logger = logging.getLogger("nmts.testing_verify")

STATUS_MERGE_SUBMITTED = "merge_submitted"
STATUS_WAITING = "waiting_for_production"
STATUS_VERIFIED = "production_verified"
STATUS_FAILED = "production_failed"
STATUS_TIMEOUT = "production_timeout"
STATUS_CLEANUP_COMPLETED = "cleanup_completed"
STATUS_CLEANUP_DEFERRED = "cleanup_deferred"
STATUS_CLEANUP_RETAINED = "cleanup_retained"

RETRYABLE = {STATUS_WAITING, STATUS_FAILED, STATUS_TIMEOUT, STATUS_CLEANUP_RETAINED}

_running: set[str] = set()


def _poll_seconds() -> int:
    try:
        return max(1, int(os.getenv("TESTING_VERIFY_POLL_SECONDS", "15")))
    except (TypeError, ValueError):
        return 15


def _timeout_seconds() -> int:
    try:
        return max(30, int(os.getenv("TESTING_VERIFY_TIMEOUT_SECONDS", "900")))
    except (TypeError, ValueError):
        return 900


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def public_label(status: Optional[str]) -> str:
    return {
        STATUS_MERGE_SUBMITTED: "Merge submitted",
        STATUS_WAITING: "Waiting for Production deployment",
        STATUS_VERIFIED: "Production verification passed",
        STATUS_FAILED: "Production verification failed",
        STATUS_TIMEOUT: "Production verification timed out",
        STATUS_CLEANUP_COMPLETED: "Testing cleanup completed",
        STATUS_CLEANUP_DEFERRED: "Testing cleanup deferred",
        STATUS_CLEANUP_RETAINED: "Testing cleanup retained",
        "dry_run": "Merge dry-run (live merge disabled)",
        "idempotent_replay": "Existing operation replayed",
    }.get(str(status or ""), str(status or "none"))


async def load_operation(test_db, operation_id: str) -> Optional[dict]:
    return await test_db[ov.OPERATION_COLLECTION].find_one(
        {"operation_id": operation_id}, {"_id": 0}
    )


async def latest_operation(test_db) -> Optional[dict]:
    cursor = test_db[ov.OPERATION_COLLECTION].find({}, {"_id": 0}).sort("created_at", -1).limit(1)
    rows = await cursor.to_list(1)
    return rows[0] if rows else None


async def save_operation(test_db, operation_id: str, fields: Dict[str, Any]) -> dict:
    payload = dict(fields)
    payload["operation_id"] = operation_id
    payload["updated_at"] = _now()
    await test_db[ov.OPERATION_COLLECTION].update_one(
        {"operation_id": operation_id},
        {"$set": payload, "$setOnInsert": {"created_at": payload.get("created_at") or _now()}},
        upsert=True,
    )
    return await load_operation(test_db, operation_id) or payload


async def _maybe_cleanup(test_db, operation_id: str, confirm_text: str, actor: dict) -> dict:
    if not tc.live_cleanup_enabled():
        return {
            "ok": True,
            "status": STATUS_CLEANUP_DEFERRED,
            "message": "Production verified. Live Testing cleanup is disabled until TESTING_LIVE_CLEANUP=true and operator approval.",
        }
    result = await tc.cleanup_testing_data(
        test_db,
        confirm_text=confirm_text,
        actor=actor,
        operation_id=f"{operation_id}:cleanup",
        reason="post_merge",
    )
    if result.get("status") == "cleaned":
        result["status"] = STATUS_CLEANUP_COMPLETED
    elif result.get("status") == "dry_run":
        result["status"] = STATUS_CLEANUP_DEFERRED
    else:
        result["status"] = STATUS_CLEANUP_RETAINED
    return result


async def run_verification_loop(
    test_db,
    operation_id: str,
    expected_sha: str,
    actor: dict,
    confirm_text: str,
) -> dict:
    if operation_id in _running:
        existing = await load_operation(test_db, operation_id)
        return existing or {"operation_id": operation_id, "result_status": STATUS_WAITING}
    _running.add(operation_id)
    interval = _poll_seconds()
    timeout = _timeout_seconds()
    started = datetime.now(timezone.utc).timestamp()
    attempts = 0
    last = {}
    try:
        await save_operation(test_db, operation_id, {
            "kind": "github_merge",
            "result_status": STATUS_WAITING,
            "expected_sha": expected_sha,
            "actor": actor,
            "confirm_text_present": bool(confirm_text),
        })
        while True:
            attempts += 1
            last = await tg.verify_production_deployment(expected_sha)
            elapsed = datetime.now(timezone.utc).timestamp() - started
            await save_operation(test_db, operation_id, {
                "result_status": STATUS_WAITING,
                "production_verification": last,
                "verify_attempts": attempts,
                "elapsed_seconds": int(elapsed),
            })
            if last.get("ok"):
                cleanup = await _maybe_cleanup(test_db, operation_id, confirm_text, actor)
                status = cleanup.get("status") or STATUS_VERIFIED
                if status == STATUS_CLEANUP_DEFERRED:
                    final_status = STATUS_CLEANUP_DEFERRED
                elif status == STATUS_CLEANUP_COMPLETED:
                    final_status = STATUS_CLEANUP_COMPLETED
                else:
                    final_status = STATUS_VERIFIED
                saved = await save_operation(test_db, operation_id, {
                    "result_status": final_status,
                    "production_verification": last,
                    "cleanup": cleanup,
                    "verify_attempts": attempts,
                })
                return saved
            if elapsed >= timeout:
                saved = await save_operation(test_db, operation_id, {
                    "result_status": STATUS_TIMEOUT,
                    "production_verification": last,
                    "cleanup": {
                        "ok": False,
                        "status": STATUS_CLEANUP_RETAINED,
                        "message": "Production verification timed out. All Testing data was retained.",
                    },
                    "verify_attempts": attempts,
                })
                return saved
            await asyncio.sleep(interval)
    except Exception as exc:
        logger.warning("Production verification loop failed: %s", exc)
        saved = await save_operation(test_db, operation_id, {
            "result_status": STATUS_FAILED,
            "cleanup": {
                "ok": False,
                "status": STATUS_CLEANUP_RETAINED,
                "message": "Production verification failed. All Testing data was retained.",
            },
            "error": str(exc)[:200],
        })
        return saved
    finally:
        _running.discard(operation_id)


def start_verification(test_db, operation_id: str, expected_sha: str, actor: dict, confirm_text: str) -> None:
    asyncio.create_task(
        run_verification_loop(test_db, operation_id, expected_sha, actor, confirm_text)
    )


async def retry_verification(test_db, operation_id: str, actor: dict) -> dict:
    existing = await load_operation(test_db, operation_id)
    if not existing:
        return {"ok": False, "error": "operation_not_found"}
    if existing.get("result_status") not in RETRYABLE and existing.get("result_status") != STATUS_WAITING:
        if existing.get("result_status") in {STATUS_VERIFIED, STATUS_CLEANUP_DEFERRED, STATUS_CLEANUP_COMPLETED}:
            return {"ok": True, "status": "idempotent_replay", "operation": existing}
    expected = str(existing.get("expected_sha") or (existing.get("receipt") or {}).get("head_sha") or "")
    confirm_text = str(existing.get("confirm_text") or tg.MERGE_PHRASE)
    saved = await save_operation(test_db, operation_id, {
        "result_status": STATUS_WAITING,
        "retry_actor": actor,
        "retried_at": _now(),
    })
    start_verification(test_db, operation_id, expected, actor, confirm_text)
    return {
        "ok": True,
        "status": STATUS_WAITING,
        "operation_label": public_label(STATUS_WAITING),
        "operation": saved,
    }


async def resume_pending(test_db) -> int:
    resumed = 0
    cursor = test_db[ov.OPERATION_COLLECTION].find(
        {"result_status": STATUS_WAITING},
        {"_id": 0},
    )
    rows = await cursor.to_list(100)
    for row in rows:
        operation_id = str(row.get("operation_id") or "")
        expected = str(row.get("expected_sha") or "")
        if not operation_id or not expected:
            continue
        start_verification(
            test_db,
            operation_id,
            expected,
            row.get("actor") or {},
            tg.MERGE_PHRASE,
        )
        resumed += 1
    return resumed
