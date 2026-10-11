#!/usr/bin/env python3
"""Testing-only Product History archive for one Upload Center IST date.

Reads nmts_testing directly. Writes one object under testing/. Reuses one
manifest per S3 key. Prunes only verified Testing-created product/upload_item
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


async def _main(date_iso: str, execute: bool, prune: bool, expected_uploads: int, expected_rows: int) -> int:
    from dotenv import load_dotenv
    from motor.motor_asyncio import AsyncIOMotorClient

    load_dotenv(os.getenv("NMTS_ENV_FILE") or "/opt/nmts-testing/backend/.env", override=True)
    _refuse_if_not_testing()

    import archive_manifest as am
    import s3_storage
    from mongo_connection import build_mongo_client_args, resolve_mongo_url
    from testing_product_history_archive import (
        assert_no_production_rows,
        is_testing_upload_on_day,
        production_leak_reason,
        upload_center_ist_date,
    )

    date_key = date_iso.replace("-", "")
    if prune and datetime.now(IST).strftime("%Y%m%d") <= date_key:
        raise SystemExit(f"Refusing prune: {date_iso} is still today in IST")

    url, kwargs = build_mongo_client_args(resolve_mongo_url())
    db = AsyncIOMotorClient(url, **kwargs)["nmts_testing"]
    storage = s3_storage.get_storage()
    print(f"storage.mode={storage.mode} real_s3={storage.is_s3()} env={storage.env} bucket={storage.bucket}")
    if execute and not storage.is_s3():
        raise SystemExit("Refusing execute: REAL S3 is required")

    uploads = await db.uploads.find({"data_origin": "testing"}, {"_id": 0}).to_list(500)
    eligible = [u for u in uploads if is_testing_upload_on_day(u, date_key)]
    print("=== ELIGIBLE ===")
    for u in eligible:
        print(json.dumps({
            "upload_no": u.get("upload_no"),
            "branch": u.get("branch"),
            "ist_day": upload_center_ist_date(u),
            "date_key": u.get("date_key"),
            "rows_imported": u.get("rows_imported"),
            "publish_status": u.get("publish_status"),
        }, default=str))
    print("=== EXCLUDED testing uploads ===")
    for u in uploads:
        if u in eligible:
            continue
        print(json.dumps({
            "upload_no": u.get("upload_no"),
            "branch": u.get("branch"),
            "ist_day": upload_center_ist_date(u),
            "publish_status": u.get("publish_status"),
            "rows_imported": u.get("rows_imported"),
        }, default=str))

    upload_nos = [u.get("upload_no") for u in eligible]
    upload_ids = [u.get("id") for u in eligible]
    if not upload_nos:
        print("No eligible Testing uploads.")
        return 1

    rows = await db.products.find({
        "data_origin": "testing",
        "publish_status": "Published",
        "upload_no": {"$in": upload_nos},
    }, {"_id": 0}).to_list(300000)
    try:
        assert_no_production_rows(rows)
        assert_no_production_rows(eligible)
    except RuntimeError as exc:
        print(str(exc))
        return 3
    if any(production_leak_reason(r) for r in rows):
        print("STOP: leak in product rows")
        return 3

    dry = {
        "uploads": len(eligible),
        "testing_rows": len(rows),
        "production_rows": sum(1 for r in rows if production_leak_reason(r)),
        "cancelled_rows": sum(1 for r in rows if str(r.get("publish_status") or "") == "Cancelled"),
        "chrompet_in_selection": sum(1 for r in rows if str(r.get("branch") or "").lower() in {"chrompet", "chromepet"}),
    }
    print("DRY_RUN", json.dumps(dry))
    if expected_uploads and dry["uploads"] != expected_uploads:
        print(f"STOP: uploads {dry['uploads']} != expected {expected_uploads}")
        return 2
    if expected_rows and dry["testing_rows"] != expected_rows:
        print(f"STOP: rows {dry['testing_rows']} != expected {expected_rows}")
        return 2
    if dry["production_rows"] or dry["cancelled_rows"] or dry["chrompet_in_selection"]:
        print("STOP: Production/cancelled/Chrompet rows in selection")
        return 2

    before = {
        "testing_products_eligible": len(rows),
        "testing_upload_items_eligible": await db.upload_items.count_documents({
            "data_origin": "testing",
            "publish_status": "Published",
            "upload_no": {"$in": upload_nos},
        }),
        "testing_uploads_metadata": await db.uploads.count_documents({"id": {"$in": upload_ids}}),
    }
    print("before", json.dumps(before))
    if not execute:
        return 0

    products_key = storage.key("product-history", date_iso, "testing-only-products.jsonl.gz")
    if not str(products_key).startswith("testing/"):
        raise SystemExit(f"Refusing key {products_key}")
    payload = _gzip_jsonl(rows)
    stored = storage.upload_bytes(
        products_key,
        payload,
        content_type="application/gzip",
        metadata={"data_origin": "testing", "archive_scope": "testing-only", "archive_date": date_iso},
        allow_replace=True,
    )
    ok = storage.verify_object(products_key, stored.sha256, int(stored.file_size))
    downloaded, _ = storage.download_bytes(products_key)
    replay = _read_jsonl_gz(downloaded)
    assert_no_production_rows(replay)
    if len(replay) != len(rows) or not ok:
        print("STOP: S3 verify/count failed")
        return 3

    existing = await db.archive_manifests.find_one(
        {"storage_key": products_key, "data_origin": "testing"},
        {"_id": 0},
    )
    manifest = existing or am.base_manifest(
        module="product-history",
        archive_date=date_iso,
        archive_month=date_iso[:7],
        storage_key=products_key,
        format="jsonl.gz",
        source_collection="products",
    )
    manifest.update({
        "storage_key": products_key,
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
    extras = await db.archive_manifests.count_documents({"storage_key": products_key})
    print("archive_verified", json.dumps({
        "key": products_key,
        "size": stored.file_size,
        "sha256": stored.sha256,
        "records": len(rows),
        "readable": len(replay),
        "verify_object": ok,
        "archive_id": manifest["archive_id"],
        "manifests_for_key": extras,
    }))
    if extras != 1:
        print("STOP: expected exactly one manifest per S3 key")
        return 3
    if not prune:
        return 0

    product_ids = [r.get("id") for r in rows if r.get("id")]
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
        "archive_date": date_iso,
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
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--date", required=True, help="Upload Center IST date YYYY-MM-DD")
    p.add_argument("--execute", action="store_true")
    p.add_argument("--prune", action="store_true")
    p.add_argument("--expected-uploads", type=int, default=0)
    p.add_argument("--expected-rows", type=int, default=0)
    args = p.parse_args()
    raise SystemExit(asyncio.run(_main(args.date, args.execute, args.prune, args.expected_uploads, args.expected_rows)))


if __name__ == "__main__":
    main()
