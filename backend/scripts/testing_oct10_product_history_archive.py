#!/usr/bin/env python3
"""Controlled Testing-only Product History archive for 10 October 2026.

Reads nmts_testing directly (no Production overlay). Writes only under the
Testing S3 prefix. Prunes only verified Testing-created product / upload_item
rows. Never enables host-wide ARCHIVE_PRUNE_ENABLED. Refuses Production.
"""
from __future__ import annotations

import argparse
import asyncio
import gzip
import json
import os
import sys
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from zoneinfo import ZoneInfo

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

IST = ZoneInfo("Asia/Kolkata")
ARCHIVE_DATE = "2026-10-10"
ARCHIVE_KEY = "20261010"


def _ist_now() -> datetime:
    return datetime.now(IST)


def _refuse_if_not_testing() -> None:
    if os.getenv("APP_ENV", "").strip().lower() != "testing":
        raise SystemExit("Refusing: APP_ENV must be testing")
    if os.getenv("DB_NAME", "").strip() != "nmts_testing":
        raise SystemExit("Refusing: DB_NAME must be nmts_testing")
    if os.getenv("NMTS_STORAGE_ENV", "").strip().lower() != "testing":
        raise SystemExit("Refusing: NMTS_STORAGE_ENV must be testing")
    if os.getenv("ARCHIVE_PRUNE_ENABLED", "false").strip().lower() in {"1", "true", "yes", "on"}:
        raise SystemExit("Refusing: host ARCHIVE_PRUNE_ENABLED is on — will not run")


def _gzip_jsonl(rows) -> bytes:
    buf = BytesIO()
    with gzip.GzipFile(fileobj=buf, mode="wb", mtime=0) as gz:
        for row in rows:
            gz.write((json.dumps(row, ensure_ascii=False, default=str) + "\n").encode("utf-8"))
    return buf.getvalue()


def _read_jsonl_gz(data: bytes) -> list[dict]:
    out = []
    with gzip.GzipFile(fileobj=BytesIO(data), mode="rb") as gz:
        for line in gz:
            text = line.decode("utf-8").strip()
            if text:
                out.append(json.loads(text))
    return out


async def _main(execute: bool, prune: bool) -> int:
    from dotenv import load_dotenv
    from motor.motor_asyncio import AsyncIOMotorClient

    env_file = os.getenv("NMTS_ENV_FILE") or "/opt/nmts-testing/backend/.env"
    load_dotenv(env_file, override=True)
    _refuse_if_not_testing()

    import archive_manifest as am
    import s3_storage
    from mongo_connection import build_mongo_client_args, resolve_mongo_url
    from testing_product_history_archive import (
        assert_no_production_rows,
        is_oct10_testing_upload,
        production_leak_reason,
    )

    if prune and _ist_now().strftime("%Y%m%d") <= ARCHIVE_KEY:
        raise SystemExit("Refusing prune: 10 October is still today in IST")

    mongo_url = resolve_mongo_url()
    url, kwargs = build_mongo_client_args(mongo_url)
    if "nmts_testing" not in (os.getenv("DB_NAME") or "") and "nmts_testing" not in (url or ""):
        # Extra belt: never point this client at Production db name.
        pass
    client = AsyncIOMotorClient(url, **kwargs)
    db = client["nmts_testing"]
    storage = s3_storage.get_storage()
    print(f"storage.mode={storage.mode} real_s3={storage.is_s3()} env={storage.env} bucket={storage.bucket}")
    if execute and not storage.is_s3():
        raise SystemExit("Refusing execute: REAL S3 is required for the Testing archive")

    uploads = await db.uploads.find({"data_origin": "testing"}, {"_id": 0}).to_list(500)
    eligible = [u for u in uploads if is_oct10_testing_upload(u)]
    excluded = []
    for u in uploads:
        if u in eligible:
            continue
        excluded.append({
            "upload_no": u.get("upload_no"),
            "branch": u.get("branch"),
            "date_key": u.get("date_key"),
            "rows_imported": u.get("rows_imported"),
            "publish_status": u.get("publish_status"),
            "reason": "not Oct-10 Testing published" if u.get("data_origin") == "testing" else "not testing-owned",
        })

    print("=== ELIGIBLE Testing uploads ===")
    for u in eligible:
        print(json.dumps({
            "upload_no": u.get("upload_no"),
            "branch": u.get("branch"),
            "date_key": u.get("date_key"),
            "rows_imported": u.get("rows_imported"),
            "publish_status": u.get("publish_status"),
            "created_at": u.get("created_at"),
        }, default=str))
    print("=== EXCLUDED Testing-owned uploads ===")
    for row in excluded:
        print(json.dumps(row, default=str))

    upload_nos = [u.get("upload_no") for u in eligible]
    upload_ids = [u.get("id") for u in eligible]
    if not upload_nos:
        print("No eligible Testing uploads. Stop.")
        return 1

    query = {
        "data_origin": "testing",
        "publish_status": "Published",
        "upload_no": {"$in": upload_nos},
    }
    rows = await db.products.find(query, {"_id": 0}).to_list(200000)
    try:
        assert_no_production_rows(rows)
        assert_no_production_rows(eligible)
    except RuntimeError as exc:
        print(str(exc))
        return 3

    for row in rows:
        reason = production_leak_reason(row)
        if reason:
            print("STOP:", reason)
            return 3

    print(f"eligible_product_rows={len(rows)}")
    print("branches", sorted({r.get("branch") for r in rows}))
    print("upload_nos", sorted({r.get("upload_no") for r in rows}))

    before = {
        "testing_products_eligible": len(rows),
        "testing_products_total": await db.products.count_documents({"data_origin": "testing"}),
        "testing_upload_items_eligible": await db.upload_items.count_documents({
            "data_origin": "testing",
            "publish_status": "Published",
            "upload_no": {"$in": upload_nos},
        }),
        "testing_uploads_metadata": await db.uploads.count_documents({"id": {"$in": upload_ids}}),
    }
    print("before", json.dumps(before))

    if not execute:
        print("List-only complete. Re-run with --execute after 00:00 IST to archive.")
        return 0

    products_key = storage.key("product-history", ARCHIVE_DATE, "testing-only-products.jsonl.gz")
    if not str(products_key).startswith("testing/"):
        raise SystemExit(f"Refusing: storage key is not under testing/ ({products_key})")

    payload = _gzip_jsonl(rows)
    stored = storage.upload_bytes(
        products_key,
        payload,
        content_type="application/gzip",
        metadata={"data_origin": "testing", "archive_scope": "testing-only", "archive_date": ARCHIVE_DATE},
        allow_replace=True,
    )
    ok = storage.verify_object(products_key, stored.sha256, int(stored.file_size))
    downloaded, _ = storage.download_bytes(products_key)
    replay = _read_jsonl_gz(downloaded)
    try:
        assert_no_production_rows(replay)
    except RuntimeError as exc:
        print("STOP after S3 verify:", exc)
        return 3
    if len(replay) != len(rows):
        print(f"STOP: readable count {len(replay)} != archived {len(rows)}")
        return 3

    manifest = am.base_manifest(
        module="product-history",
        archive_date=ARCHIVE_DATE,
        archive_month=ARCHIVE_DATE[:7],
        storage_key=products_key,
        format="jsonl.gz",
        source_collection="products",
    )
    manifest.update({
        "record_count": len(rows),
        "file_size": int(stored.file_size),
        "sha256": stored.sha256,
        "status": am.STATUS_VERIFIED,
        "verified_at": datetime.now(timezone.utc).isoformat(),
        "eligible_for_prune": True,
        "storage_backend": "s3",
        "storage_env": "testing",
        "data_origin": "testing",
        "archive_scope": "testing-only",
        "scope_upload_nos": upload_nos,
        "scope_branches": sorted({str(r.get("branch") or "") for r in rows}),
        "readable_record_count": len(replay),
    })
    await db.archive_manifests.update_one(
        {"archive_id": manifest["archive_id"]},
        {"$set": manifest},
        upsert=True,
    )
    print("archive_verified", json.dumps({
        "key": products_key,
        "size": stored.file_size,
        "sha256": stored.sha256,
        "records": len(rows),
        "readable": len(replay),
        "verify_object": ok,
        "archive_id": manifest["archive_id"],
    }))
    if not ok:
        return 3

    if not prune:
        print("Archive verified. Re-run with --execute --prune after History check if needed.")
        return 0

    product_ids = [r.get("id") for r in rows if r.get("id")]
    if not product_ids:
        print("STOP: no Testing product ids to prune")
        return 3
    del_prod = await db.products.delete_many({
        "id": {"$in": product_ids},
        "data_origin": "testing",
        "upload_no": {"$in": upload_nos},
    })
    del_items = await db.upload_items.delete_many({
        "data_origin": "testing",
        "publish_status": "Published",
        "upload_no": {"$in": upload_nos},
    })
    uploads_after = await db.uploads.count_documents({"id": {"$in": upload_ids}})
    receipt = {
        "archive_date": ARCHIVE_DATE,
        "status": "pruned",
        "environment": "testing",
        "data_origin": "testing",
        "s3_keys": [products_key],
        "sha256": stored.sha256,
        "file_size": int(stored.file_size),
        "archived_count": len(rows),
        "deleted_products": int(getattr(del_prod, "deleted_count", 0) or 0),
        "deleted_upload_items": int(getattr(del_items, "deleted_count", 0) or 0),
        "uploads_metadata_remaining": uploads_after,
        "scope_upload_nos": upload_nos,
        "manifest_id": manifest["archive_id"],
        "pruned_at": datetime.now(timezone.utc).isoformat(),
        "before": before,
    }
    await db.archive_prune_receipts.insert_one(dict(receipt))
    await db.testing_audit_receipts.insert_one(dict(receipt))
    await db.archive_manifests.update_one(
        {"archive_id": manifest["archive_id"]},
        {"$set": {"status": am.STATUS_PRUNED, "eligible_for_prune": False, "pruned_at": receipt["pruned_at"]}},
    )
    print("prune_receipt", json.dumps({k: v for k, v in receipt.items() if k != "before"}, default=str))
    if uploads_after != before["testing_uploads_metadata"]:
        print("WARNING: upload metadata count changed")
        return 1
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute", action="store_true", help="Write Testing S3 archive after midnight")
    parser.add_argument("--prune", action="store_true", help="Prune verified Testing Mongo rows only")
    args = parser.parse_args()
    raise SystemExit(asyncio.run(_main(args.execute, args.prune)))


if __name__ == "__main__":
    main()
