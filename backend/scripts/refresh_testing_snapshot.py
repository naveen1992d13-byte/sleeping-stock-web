#!/usr/bin/env python3
"""Copy current production nmts data into nmts_testing as a read-only mirror.

Never writes to production. Never copies S3 `dev/` objects. DocumentDB-compatible:
no collection rename, no transactions, no change streams, no $unionWith.

All production collections are mirrored. Testing Master Admin, testing-created
rows (`data_origin=testing`), and the testing JWT/S3 prefix stay local to
nmts_testing.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from dotenv import load_dotenv
from pymongo import MongoClient, ReplaceOne

BACKEND = Path(os.environ.get("NMTS_TESTING_BACKEND", "/opt/nmts-testing/backend"))
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))
REPO_BACKEND = Path(__file__).resolve().parents[1]
if str(REPO_BACKEND) not in sys.path:
    sys.path.insert(0, str(REPO_BACKEND))

import testing_runtime
from mongo_connection import build_mongo_client_args, resolve_mongo_url
from testing_snapshot import (
    REFERENCE_COLLECTIONS,
    PRESERVE_TARGET_COLLECTIONS,
    TESTING_MASTER_EMAIL,
    ReadOnlyDatabase,
    SourceWriteBlocked,
    activation_filter,
    activation_identity,
    discover_mirror_collections,
    duplicate_identity_keys,
    ingest_collection_name,
    is_protected_user,
    keeps_original_id,
    mapping_errors,
    missing_required_fields,
    stamp_snapshot_doc,
    testing_master_parity_fields,
)

IST = ZoneInfo("Asia/Kolkata")
BATCH = 500
TARGET_DB = "nmts_testing"
SOURCE_DB = "nmts"
PROGRESS_EVERY = 5000


def _log(message: str) -> None:
    print(message, flush=True)


def _load_testing_env() -> None:
    env_file = BACKEND / ".env"
    if env_file.is_file():
        load_dotenv(env_file, override=True)


def _connect(secret_id: str | None = None):
    env = dict(os.environ)
    if secret_id:
        env["DOCDB_SECRET_ID"] = secret_id
    url = resolve_mongo_url(env)
    mongo_url, kwargs = build_mongo_client_args(url, env)
    kwargs = dict(kwargs)
    if "readPreference" in kwargs:
        kwargs["readPreference"] = "primaryPreferred"
    return MongoClient(mongo_url, **kwargs)


def _business_date(source_db) -> str:
    keys = [
        str(k).replace("-", "")
        for k in source_db.products.distinct(
            "active_date_key",
            {"publish_status": "Published", "is_active_today": True},
        )
        if k
    ]
    digits = [k for k in keys if k.isdigit()]
    if digits:
        return max(digits)
    return datetime.now(IST).strftime("%Y%m%d")


def _cursor_docs(collection, query: dict[str, Any]):
    cursor = collection.find(query, no_cursor_timeout=False).batch_size(BATCH)
    for doc in cursor:
        yield doc


def _validate_ingest(target_db, version: str, collections: list[str], expected_counts: dict[str, int]) -> None:
    brands = {str(d.get("name") or "").strip() for d in target_db[ingest_collection_name("brands", version)].find({}, {"name": 1})}
    dealers = {str(d.get("name") or "").strip() for d in target_db[ingest_collection_name("dealers", version)].find({}, {"name": 1})}
    branches = {str(d.get("name") or "").strip() for d in target_db[ingest_collection_name("branches", version)].find({}, {"name": 1})}
    brands.discard("")
    dealers.discard("")
    branches.discard("")

    if not brands or not dealers or not branches:
        raise RuntimeError("Snapshot validation failed: brand/dealer/branch master data missing")

    for name in collections:
        ingest = target_db[ingest_collection_name(name, version)]
        count = ingest.count_documents({})
        expected = expected_counts.get(name, 0)
        if count != expected:
            raise RuntimeError(f"{name} ingest count {count} != source {expected}")
        if name not in REFERENCE_COLLECTIONS:
            continue
        if name == "products":
            sample = list(ingest.find(
                {"publish_status": "Published", "is_active_today": True},
                {"_id": 0},
            ).limit(5000))
            if not sample:
                sample = list(ingest.find({}, {"_id": 0}).limit(200))
        else:
            sample = list(ingest.find({}, {"_id": 0}).limit(5000))
        missing = missing_required_fields(name, sample if len(sample) <= 5000 else sample[:200])
        if missing:
            raise RuntimeError(f"{name} missing required fields: {missing[:8]}")
        if name != "products":
            dupes = duplicate_identity_keys(name, sample)
            if dupes:
                raise RuntimeError(f"{name} duplicate identity keys: {dupes[:8]}")
        if name in {"products", "batch_summaries", "dealers", "branches"}:
            errors = mapping_errors(sample[:2000], brands, dealers, branches, collection=name)
            if errors:
                raise RuntimeError(f"{name} mapping errors: {errors[:8]}")
        if name == "products" and count:
            qty = 0.0
            value = 0.0
            for row in ingest.find({}, {"available_qty_number": 1, "quantity": 1, "total_value_number": 1, "total_value": 1}):
                qty += float(row.get("available_qty_number", row.get("quantity", 0)) or 0)
                value += float(row.get("total_value_number", row.get("total_value", 0)) or 0)
            if qty < 0 or value < 0:
                raise RuntimeError("Product totals are negative")


def _flush_ops(live, ops) -> None:
    if ops:
        live.bulk_write(ops, ordered=False)


def _activate(target_db, version: str, business_date: str, copied_at: str, collections: list[str]) -> dict[str, int]:
    live_counts: dict[str, int] = {}
    for name in collections:
        ingest = target_db[ingest_collection_name(name, version)]
        live = target_db[name]
        ops: list[ReplaceOne] = []
        ingest_count = 0
        skipped_protected = 0
        for doc in ingest.find():
            keep_id = keeps_original_id(name, doc) or ("_id" in activation_filter(name, doc))
            payload = {k: v for k, v in dict(doc).items() if keep_id or k != "_id"}
            payload["snapshot_version"] = version
            payload["snapshot_business_date"] = business_date
            payload["snapshot_copied_at"] = copied_at
            payload["data_origin"] = "snapshot"
            payload["is_snapshot_reference"] = True
            if name == "users" and is_protected_user(payload):
                skipped_protected += 1
                continue
            if name == "users":
                existing = live.find_one(
                    {"$or": [{"id": payload.get("id")}, {"email": payload.get("email")}]},
                    {"email": 1, "is_testing_master": 1},
                )
                if is_protected_user(existing):
                    skipped_protected += 1
                    continue
            filt = activation_filter(name, payload)
            ops.append(ReplaceOne(filt, payload, upsert=True))
            ingest_count += 1
            if len(ops) >= BATCH:
                _flush_ops(live, ops)
                ops = []
            if ingest_count and ingest_count % PROGRESS_EVERY == 0:
                _log(f"  activate {name}: {ingest_count}")
        _flush_ops(live, ops)
        live_counts[name] = live.count_documents({"data_origin": "snapshot", "snapshot_version": version})
        if live_counts[name] != ingest_count:
            raise RuntimeError(
                f"Activation count mismatch for {name}: live={live_counts[name]} ingest={ingest_count} skipped_protected={skipped_protected}"
            )
        if name in {"products", "batch_summaries"}:
            live.create_index([("data_origin", 1), ("snapshot_version", 1), ("active_date_key", 1)], background=True)
        _log(f"Activated {name}: {live_counts[name]}")
    return live_counts


def _delete_old_snapshot(target_db, keep_version: str, collections: list[str]) -> None:
    for name in collections:
        filt: dict[str, Any] = {
            "data_origin": "snapshot",
            "snapshot_version": {"$ne": keep_version},
        }
        if name == "users":
            filt["email"] = {"$ne": TESTING_MASTER_EMAIL}
            filt["is_testing_master"] = {"$ne": True}
        target_db[name].delete_many(filt)


def _drop_ingest(target_db, version: str, collections: list[str]) -> None:
    for name in collections:
        target_db.drop_collection(ingest_collection_name(name, version))


def _write_meta(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    os.chmod(path, 0o644)


def _preserve_check(target_db) -> dict[str, int]:
    counts = {name: target_db[name].count_documents({}) for name in PRESERVE_TARGET_COLLECTIONS}
    master = target_db.users.find_one({"email": TESTING_MASTER_EMAIL, "role": "master"}, {"_id": 0, "password": 0})
    if not master:
        raise RuntimeError("Testing Master Admin is missing; refusing snapshot refresh")
    return counts


def _ensure_testing_master_parity(target_db) -> None:
    """Keep Testing Master Admin at full master-role parity without rotating the password."""
    fields = testing_master_parity_fields()
    result = target_db.users.update_one(
        {"email": TESTING_MASTER_EMAIL},
        {"$set": fields},
    )
    if result.matched_count != 1:
        raise RuntimeError("Testing Master Admin missing while applying permission parity")


def refresh(rollback: bool, reset_testing_created: bool, dry_run: bool) -> int:
    _load_testing_env()
    testing_runtime.assert_env_isolation()
    if os.environ.get("DB_NAME") != TARGET_DB:
        raise SystemExit(f"Refusing to run: DB_NAME must be {TARGET_DB}")
    if os.environ.get("APP_ENV", "").strip().lower() != "testing":
        raise SystemExit("Refusing to run unless APP_ENV=testing")

    source_secret = os.environ.get("SNAPSHOT_SOURCE_DOCDB_SECRET_ID") or os.environ.get("SNAPSHOT_SOURCE_SECRET_ID")
    target_client = _connect()
    source_client = _connect(source_secret)
    source_db = ReadOnlyDatabase(source_client[SOURCE_DB])
    target_db = target_client[TARGET_DB]

    if target_db.name != TARGET_DB:
        raise SystemExit("Target database is not nmts_testing")

    meta_path = testing_runtime.snapshot_metadata_path()
    previous = testing_runtime.load_snapshot_metadata()
    preserve_before = _preserve_check(target_db)
    _log(f"Preserving testing users/orders/uploads/counters: { {k: preserve_before[k] for k in ('users','counters','orders','uploads')} }")

    if rollback:
        prev_version = str(previous.get("previous_snapshot_version") or "").strip()
        if not prev_version:
            raise SystemExit("No previous snapshot version is recorded")
        still = target_db.products.count_documents({"data_origin": "snapshot", "snapshot_version": prev_version})
        if still <= 0:
            raise SystemExit("Previous snapshot rows are no longer present; re-run a refresh instead")
        payload = dict(previous)
        payload["snapshot_version"] = prev_version
        payload["business_date_key"] = previous.get("previous_business_date_key") or previous.get("business_date_key")
        payload["status"] = "active"
        payload["rolled_back_at"] = datetime.now(timezone.utc).isoformat()
        _write_meta(meta_path, payload)
        _log(f"Rolled back active snapshot to {prev_version}")
        return 0

    if reset_testing_created:
        names = discover_mirror_collections(target_db)
        for name in names:
            query: dict[str, Any] = {"data_origin": "testing"}
            if name == "users":
                query["email"] = {"$ne": TESTING_MASTER_EMAIL}
                query["is_testing_master"] = {"$ne": True}
            result = target_db[name].delete_many(query)
            if result.deleted_count:
                _log(f"Reset testing-created {name}: deleted {result.deleted_count}")
        _ensure_testing_master_parity(target_db)
        _log("Testing Master Admin preserved")
        return 0

    collections = discover_mirror_collections(source_db)
    if "products" not in collections:
        raise SystemExit("Source nmts has no products collection")
    _log(f"Mirroring {len(collections)} production collections into nmts_testing")

    business_date = _business_date(source_db)
    copied_at = datetime.now(timezone.utc).isoformat()
    version = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    _log(f"Snapshot source=nmts target=nmts_testing business_date={business_date} version={version}")
    if source_secret:
        _log("Using dedicated SNAPSHOT_SOURCE_DOCDB_SECRET_ID for production reads")
    else:
        _log("WARNING: using the application DocumentDB credential in read-only mode for source. Create a dedicated production read-only user and set SNAPSHOT_SOURCE_DOCDB_SECRET_ID.")

    expected: dict[str, int] = {}
    try:
        for name in collections:
            expected[name] = source_db[name].count_documents({})
            _log(f"Source {name}: {expected[name]}")
            ingest_name = ingest_collection_name(name, version)
            target_db.drop_collection(ingest_name)
            ingest = target_db[ingest_name]
            batch: list[dict[str, Any]] = []
            written = 0
            for doc in _cursor_docs(source_db[name], {}):
                if name == "users" and is_protected_user(doc):
                    continue
                keep_id = keeps_original_id(name, doc)
                if not keep_id:
                    ident = activation_identity(name, doc)
                    if any(doc.get(field) in (None, "") for field in ident):
                        keep_id = True
                batch.append(stamp_snapshot_doc(
                    doc, version, copied_at, business_date,
                    keep_id=keep_id,
                ))
                if len(batch) >= BATCH:
                    ingest.insert_many(batch, ordered=False)
                    written += len(batch)
                    batch = []
                    if written % PROGRESS_EVERY == 0:
                        _log(f"  ingest {name}: {written}")
            if batch:
                ingest.insert_many(batch, ordered=False)
                written += len(batch)
            expected[name] = written if name == "users" else expected[name]
            if name == "users":
                # Protected Testing Master is never ingested from source.
                source_count = source_db[name].count_documents({})
                skipped = source_count - written
                expected[name] = written
                _log(f"  users ingested={written} source={source_count} skipped_protected_or_absent={skipped}")
        _validate_ingest(target_db, version, collections, expected)
    except SourceWriteBlocked as exc:
        raise SystemExit(str(exc)) from exc
    except Exception:
        _drop_ingest(target_db, version, collections)
        _log("Ingest validation failed; previous snapshot remains active")
        raise

    if dry_run:
        _drop_ingest(target_db, version, collections)
        _log("Dry run OK; ingest dropped and live testing data unchanged")
        return 0

    try:
        live_counts = _activate(target_db, version, business_date, copied_at, collections)
        _ensure_testing_master_parity(target_db)
        preserve_after_activate = _preserve_check(target_db)
        if preserve_after_activate["users"] < preserve_before["users"]:
            raise RuntimeError("User count dropped during activation")
        payload = {
            "status": "active",
            "source_origin": "nmts",
            "target_db": TARGET_DB,
            "snapshot_version": version,
            "previous_snapshot_version": previous.get("snapshot_version"),
            "business_date_key": business_date,
            "previous_business_date_key": previous.get("business_date_key"),
            "copied_at": copied_at,
            "source_business_date": business_date,
            "mirror": "full",
            "collection_count": len(collections),
            "counts": live_counts,
        }
        _write_meta(meta_path, payload)
        if testing_runtime.snapshot_business_date_key() != business_date:
            raise RuntimeError("Active snapshot metadata did not freeze the testing business date")
        _delete_old_snapshot(target_db, version, collections)
        _drop_ingest(target_db, version, collections)
        _ensure_testing_master_parity(target_db)
        preserve_after = _preserve_check(target_db)
        _log(f"Active snapshot {version} date={business_date} products={live_counts.get('products', 0)} collections={len(collections)}")
        _log(f"Preserved after refresh: users={preserve_after['users']} orders={preserve_after['orders']} uploads={preserve_after['uploads']}")
        return 0
    except Exception:
        _log("Activation failed; deleting the new version only and keeping the previous snapshot")
        for name in collections:
            target_db[name].delete_many({"data_origin": "snapshot", "snapshot_version": version})
        if previous:
            _write_meta(meta_path, previous)
        _drop_ingest(target_db, version, collections)
        _ensure_testing_master_parity(target_db)
        raise
    finally:
        try:
            source_client.close()
        except Exception:
            pass
        try:
            target_client.close()
        except Exception:
            pass


def main() -> int:
    parser = argparse.ArgumentParser(description="Refresh nmts_testing from current nmts operational data")
    parser.add_argument("--dry-run", action="store_true", help="Validate ingest only; do not activate")
    parser.add_argument("--rollback", action="store_true", help="Reactivate the previous snapshot version")
    parser.add_argument("--reset-testing-created", action="store_true", help="Delete testing-created operational rows; keep users including Testing Master Admin")
    args = parser.parse_args()
    try:
        return refresh(args.rollback, args.reset_testing_created, args.dry_run)
    except Exception as exc:
        _log(f"SNAPSHOT_FAILED: {type(exc).__name__}: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
