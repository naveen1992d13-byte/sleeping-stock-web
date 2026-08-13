#!/usr/bin/env python3
"""Non-destructive existing-data compatibility report (no deletes).

Reports:
- uploads where storage_key is null
- orphan upload_items (no parent uploads)
- historical products count/date span
- currently Dispatched Request Center records
- failed/incomplete archive_runs / manifests
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


async def main():
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
    from motor.motor_asyncio import AsyncIOMotorClient

    url = os.environ.get("MONGO_URL")
    db_name = os.environ.get("DB_NAME") or os.environ.get("MONGO_DB") or "nmts"
    if not url:
        print(json.dumps({"error": "MONGO_URL missing"}))
        return 1
    client = AsyncIOMotorClient(url)
    db = client[db_name]

    uploads_null = await db.uploads.count_documents({"$or": [{"storage_key": None}, {"storage_key": {"$exists": False}}]})
    uploads_total = await db.uploads.count_documents({})
    upload_ids = set()
    async for u in db.uploads.find({}, {"_id": 0, "id": 1, "upload_id": 1}):
        if u.get("id"):
            upload_ids.add(u["id"])
        if u.get("upload_id"):
            upload_ids.add(u["upload_id"])
    orphan_items = 0
    orphan_batches = {}
    async for it in db.upload_items.find({}, {"_id": 0, "upload_id": 1, "batch_id": 1}):
        uid = it.get("upload_id") or it.get("batch_id")
        if uid and uid not in upload_ids:
            orphan_items += 1
            orphan_batches[uid] = orphan_batches.get(uid, 0) + 1
    products_total = await db.products.count_documents({})
    dates = await db.products.distinct("active_date_key")
    dispatched = await db.order_requests.count_documents({"status": "Dispatched"})
    approved = await db.order_requests.count_documents({"status": "Approved"})
    received = await db.order_requests.count_documents({"status": "Received"})
    failed_manifests = await db.archive_manifests.count_documents(
        {"status": {"$in": ["FAILED", "VERIFICATION_FAILED", "PENDING", "RUNNING"]}}
    )
    incomplete_runs = await db.archive_runs.count_documents(
        {"overall_status": {"$in": ["Pending", "Running", "Failed"]}}
    )
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "destructive": False,
        "uploads_storage_key_null": uploads_null,
        "uploads_total": uploads_total,
        "orphan_upload_items": orphan_items,
        "orphan_upload_item_batches": orphan_batches,
        "products_total": products_total,
        "product_active_date_keys": sorted([d for d in dates if d])[:50],
        "request_center_dispatched": dispatched,
        "request_center_approved_stored": approved,
        "request_center_received_legacy": received,
        "archive_manifests_incomplete_or_failed": failed_manifests,
        "archive_runs_incomplete_or_failed": incomplete_runs,
        "recommendation": (
            "Do not auto-delete. Backfill storage_key only with explicit ops approval. "
            "Keep Dispatched until manual Complete. Keep ARCHIVE_PRUNE_ENABLED=false."
        ),
    }
    out = Path("/tmp/cursor/artifacts") if Path("/tmp/cursor").exists() else Path("/tmp")
    out.mkdir(parents=True, exist_ok=True)
    path = out / "existing_data_compat_report.json"
    path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))
    print(f"wrote {path}")
    client.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
