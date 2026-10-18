#!/usr/bin/env python3
"""One-time / idempotent Testing fresh-start reset.

SAFE BY DESIGN:
  - Aborts unless DB_NAME == nmts_testing and NMTS_STORAGE_ENV == testing
  - Never connects to Production writes; never touches S3 prefix dev/
  - Dry-run first: prints collection counts + S3 object count/size
  - Asserts every S3 key starts with testing/ (abort otherwise)
  - Deletes Testing collection data EXCEPT protected Testing Master Admin
  - Tombstones Production hub entities (brands/dealers/branches/users) so
    User Hub counts become testing-only (0 after wipe)
  - Upserts testing.master@sleepingstock.in (role canonical); password from
    NMTS_TESTING_MASTER_PASSWORD only when creating; never logs the secret

Usage:
  python deploy/testing/reset_testing_fresh_start.py --dry-run
  python deploy/testing/reset_testing_fresh_start.py --execute
  python deploy/testing/reset_testing_fresh_start.py --execute --force-password-from-env
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

BACKEND = Path(os.environ.get("NMTS_TESTING_BACKEND", "/opt/nmts-testing/backend"))
ENV_FILE = BACKEND / ".env"
TESTING_EMAIL = "testing.master@sleepingstock.in"
HUB_COLLECTIONS = ("brands", "dealers", "branches", "users", "states")

# Collections wiped entirely (except users, handled specially).
WIPE_COLLECTIONS = (
    "products", "upload_items", "uploads", "batch_summaries",
    "order_headers", "order_items", "order_requests", "order_activity", "orders",
    "request_headers", "stock_reservations",
    "brands", "dealers", "branches", "states", "groups",
    "user_alerts", "notices", "notice_attachments", "notice_audit_logs", "notice_user_status",
    "queries", "query_attachments",
    "activity_logs", "notification_logs",
    "stock_verifications", "stock_verification_sessions", "stock_verification_history",
    "mobile_users", "mobile_devices", "mobile_sessions", "mobile_audit_logs",
    "mobile_push_delivery_logs", "mobile_notification_actions", "mobile_ownership_events",
    "mobile_pairing_codes", "mobile_request_cycles", "mobile_request_group_locks",
    "mobile_request_push_claims", "mobile_user_attendance", "mobile_app_versions",
    "analytics_daily_aggs", "analytics_rollup_status", "analytics_stock_daily_snapshots",
    "auto_perpetual_assignments", "auto_perpetual_branch_planner", "auto_perpetual_daily_runs",
    "auto_perpetual_pool", "auto_perpetual_suggestions",
    "archive_job_locks", "archive_manifests", "archive_outbox", "archive_prune_receipts",
    "archive_runs", "order_archive_index", "request_archive_index",
    "order_cancellation_requests", "order_sourcing_events",
    "old_report_downloads", "old_report_requests",
    "login_repair_canaries", "login_screen_images",
    "source_rejection_freezes", "storage_usage_daily", "storage_usage_events",
    "templates", "counters",
    "testing_overlays", "testing_tombstones", "testing_snapshot_meta",
)


def _load_env() -> None:
    if not ENV_FILE.is_file():
        raise SystemExit(f"Testing backend .env not found: {ENV_FILE}")
    load_dotenv(ENV_FILE, override=True)


def _assert_isolation() -> None:
    db_name = (os.environ.get("DB_NAME") or "").strip()
    storage = (os.environ.get("NMTS_STORAGE_ENV") or "").strip()
    app_env = (os.environ.get("APP_ENV") or "").strip().lower()
    if app_env != "testing":
        raise SystemExit(f"ABORT: APP_ENV={app_env!r} (expected testing)")
    if db_name != "nmts_testing":
        raise SystemExit(f"ABORT: DB_NAME={db_name!r} (expected nmts_testing) — refusing to touch Production")
    if storage != "testing":
        raise SystemExit(f"ABORT: NMTS_STORAGE_ENV={storage!r} (expected testing) — refusing to touch dev/")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


async def _inventory(test_db, prod_db) -> dict:
    sys.path.insert(0, str(BACKEND))
    import testing_cleanup as tc  # type: ignore

    counts = {}
    names = sorted(await test_db.list_collection_names())
    for name in names:
        try:
            counts[name] = int(await test_db[name].count_documents({}))
        except Exception:
            counts[name] = -1
    s3_info = tc.list_testing_s3_objects(limit=100_000)
    keys = s3_info.get("keys") or []
    prefix = s3_info["prefix"]
    for key in keys:
        if not str(key).startswith(prefix):
            raise SystemExit(f"ABORT: S3 key outside testing/ prefix: {key}")
    hub_prod = {}
    for name in HUB_COLLECTIONS:
        hub_prod[name] = int(await prod_db[name].count_documents({}))
    master = await test_db.users.find_one(
        {"email": {"$regex": f"^{TESTING_EMAIL}$", "$options": "i"}},
        {"_id": 0, "id": 1, "email": 1, "role": 1, "user_id": 1, "is_testing_master": 1},
    )
    return {
        "db_name": test_db.name,
        "collection_counts": counts,
        "s3": {
            "prefix": prefix,
            "object_count": s3_info.get("object_count"),
            "bytes": s3_info.get("bytes"),
        },
        "production_hub_counts_readonly": hub_prod,
        "testing_master": master,
    }


async def _tombstone_production_hub(test_db, prod_db) -> dict:
    """Hide Production hub entities from Testing overlay so Hub shows testing-only."""
    tombs = test_db["testing_tombstones"]
    now = _now()
    out = {}
    for name in HUB_COLLECTIONS:
        ids = []
        cursor = prod_db[name].find({}, {"_id": 0, "id": 1})
        async for row in cursor:
            ident = str(row.get("id") or "").strip()
            if ident:
                ids.append(ident)
        n = 0
        for ident in ids:
            await tombs.update_one(
                {"collection": name, "production_id": ident},
                {
                    "$set": {
                        "collection": name,
                        "production_id": ident,
                        "op": "tombstone",
                        "reason": "fresh_start_reset",
                        "updated_at": now,
                    },
                    "$setOnInsert": {"created_at": now},
                },
                upsert=True,
            )
            n += 1
        out[name] = n
    return out


async def _ensure_master(test_db, *, force_password: bool) -> dict:
    sys.path.insert(0, str(BACKEND))
    import roles as role_defs  # type: ignore
    from passlib.context import CryptContext
    from testing_snapshot import testing_master_parity_fields  # type: ignore

    pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
    parity = testing_master_parity_fields()
    parity["role"] = role_defs.ROLE_MASTER_ADMIN
    parity["user_id"] = parity.get("user_id") or "TSTMST001"

    existing = await test_db.users.find_one(
        {"email": {"$regex": f"^{TESTING_EMAIL}$", "$options": "i"}},
    )
    secret = (os.environ.get("NMTS_TESTING_MASTER_PASSWORD") or "").strip()
    if existing:
        updates = dict(parity)
        action = "parity"
        if force_password:
            if not secret:
                raise SystemExit("ABORT: --force-password-from-env requires NMTS_TESTING_MASTER_PASSWORD")
            updates["password"] = pwd_context.hash(secret)
            action = "parity+password_from_env"
        await test_db.users.update_one({"id": existing["id"]}, {"$set": updates})
        return {"action": action, "id": existing.get("id"), "email": TESTING_EMAIL}

    if not secret:
        raise SystemExit(
            "ABORT: Testing Master Admin missing and NMTS_TESTING_MASTER_PASSWORD is unset"
        )
    doc = {
        "id": str(uuid.uuid4()),
        "email": TESTING_EMAIL,
        "password": pwd_context.hash(secret),
        "phone": "",
        "last_login": None,
        "created_at": _now(),
        **parity,
    }
    await test_db.users.insert_one(doc)
    return {"action": "created", "id": doc["id"], "email": TESTING_EMAIL}


async def _execute(test_db, prod_db, *, force_password: bool) -> dict:
    sys.path.insert(0, str(BACKEND))
    import roles as role_defs  # type: ignore
    import testing_cleanup as tc  # type: ignore
    import s3_storage  # type: ignore

    deleted = {}
    existing_names = set(await test_db.list_collection_names())
    # Wipe collections.
    for name in WIPE_COLLECTIONS:
        if name not in existing_names:
            deleted[name] = 0
            continue
        # testing_tombstones cleared then re-filled with production hub tombs below.
        result = await test_db[name].delete_many({})
        deleted[name] = int(getattr(result, "deleted_count", 0) or 0)

    # Users: delete everyone except protected Testing Master Admin.
    removed_users = 0
    cursor = test_db.users.find({}, {"_id": 0})
    async for row in cursor:
        if role_defs.is_protected_master_admin_doc(row) and str(row.get("email") or "").strip().lower() == TESTING_EMAIL:
            continue
        if bool(row.get("is_testing_master")) and str(row.get("email") or "").strip().lower() == TESTING_EMAIL:
            continue
        if str(row.get("email") or "").strip().lower() == TESTING_EMAIL:
            continue
        ident = row.get("id")
        if ident:
            result = await test_db.users.delete_one({"id": ident})
            removed_users += int(getattr(result, "deleted_count", 0) or 0)
    deleted["users_non_master"] = removed_users

    master_info = await _ensure_master(test_db, force_password=force_password)
    tombstoned = await _tombstone_production_hub(test_db, prod_db)

    # Hide Production admin@ explicitly (credential isolation).
    hide = await role_defs.hide_mirrored_production_master_admin(test_db, raw_db=test_db)

    s3_info = tc.list_testing_s3_objects(limit=100_000)
    storage = s3_storage.get_storage()
    prefix = s3_info["prefix"]
    if storage.env != "testing" or not prefix.startswith("testing/"):
        raise SystemExit("ABORT: storage env is not testing/")
    s3_deleted = 0
    for key in s3_info.get("keys") or []:
        if not str(key).startswith(prefix):
            raise SystemExit(f"ABORT: S3 key outside testing/: {key}")
        storage.delete(key)
        s3_deleted += 1

    return {
        "deleted": deleted,
        "master": master_info,
        "production_hub_tombstoned": tombstoned,
        "hide_mirrored": hide,
        "s3_deleted": s3_deleted,
    }


async def main_async(args: argparse.Namespace) -> int:
    _load_env()
    _assert_isolation()
    sys.path.insert(0, str(BACKEND))
    from motor.motor_asyncio import AsyncIOMotorClient
    from mongo_connection import build_mongo_client_args, resolve_mongo_url
    import testing_overlay as overlay  # type: ignore

    write_url, write_kwargs = build_mongo_client_args(resolve_mongo_url())
    client = AsyncIOMotorClient(write_url, **write_kwargs)
    test_db = client["nmts_testing"]
    if test_db.name != "nmts_testing":
        raise SystemExit("ABORT: refused non-testing database handle")

    ro_url = overlay.resolve_production_readonly_url()
    ro_url, ro_kwargs = build_mongo_client_args(ro_url)
    ro_client = AsyncIOMotorClient(ro_url, **ro_kwargs)
    prod_db = ro_client["nmts"]

    inv = await _inventory(test_db, prod_db)
    print("=== DRY-RUN INVENTORY ===")
    print(f"db={inv['db_name']}")
    print(f"testing_master={inv['testing_master']}")
    print(f"s3={inv['s3']}")
    print(f"production_hub_readonly={inv['production_hub_counts_readonly']}")
    nonzero = {k: v for k, v in inv["collection_counts"].items() if v}
    print(f"non_empty_collections={len(nonzero)}")
    for name, count in sorted(nonzero.items(), key=lambda kv: (-kv[1], kv[0]))[:40]:
        print(f"  {name}: {count}")

    if args.dry_run or not args.execute:
        print("Dry-run complete. Re-run with --execute to apply.")
        client.close()
        ro_client.close()
        return 0

    print("=== EXECUTE RESET ===")
    result = await _execute(test_db, prod_db, force_password=args.force_password_from_env)
    # Do not print password / hashes.
    safe_master = {k: v for k, v in (result.get("master") or {}).items() if k != "password"}
    print(f"master={safe_master}")
    print(f"s3_deleted={result.get('s3_deleted')}")
    print(f"production_hub_tombstoned={result.get('production_hub_tombstoned')}")
    print(f"hide_mirrored={result.get('hide_mirrored')}")
    deleted = result.get("deleted") or {}
    top = sorted(deleted.items(), key=lambda kv: (-kv[1], kv[0]))[:25]
    print("deleted_top:")
    for name, count in top:
        if count:
            print(f"  {name}: {count}")

    after = await _inventory(test_db, prod_db)
    print("=== AFTER ===")
    print(f"testing_master={after['testing_master']}")
    print(f"s3={after['s3']}")
    print(f"users_count={after['collection_counts'].get('users')}")
    print(f"brands/dealers/branches raw={after['collection_counts'].get('brands')}/{after['collection_counts'].get('dealers')}/{after['collection_counts'].get('branches')}")
    client.close()
    ro_client.close()
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="Inventory only (default if --execute omitted)")
    parser.add_argument("--execute", action="store_true", help="Perform the reset")
    parser.add_argument(
        "--force-password-from-env",
        action="store_true",
        help="Rotate Testing Master password from NMTS_TESTING_MASTER_PASSWORD (never logged)",
    )
    args = parser.parse_args()
    return asyncio.run(main_async(args))


if __name__ == "__main__":
    raise SystemExit(main())
