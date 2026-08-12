#!/usr/bin/env python3
import asyncio
import os
import time
from dotenv import load_dotenv

load_dotenv("/agent/repos/sleeping-stock-web/backend/.env")

from motor.motor_asyncio import AsyncIOMotorClient
import history_archive as ha
import hybrid_history as hh
from s3_storage import get_storage, reset_storage_for_tests


async def main():
    reset_storage_for_tests()
    storage = get_storage()
    client = AsyncIOMotorClient(os.environ["MONGO_URL"], serverSelectionTimeoutMS=20000)
    db = client[os.environ.get("DB_NAME", "nmts")]
    print("start", flush=True)
    dry = await ha.cleanup_published_upload_items(db, dry_run=True)
    print("dry_run", dry, flush=True)
    t0 = time.time()
    cleaned = await ha.cleanup_published_upload_items(db, dry_run=False)
    print("cleaned", cleaned, "elapsed", round(time.time() - t0, 1), flush=True)
    try:
        await db["quota_probe"].insert_one({"ok": 1})
        await db["quota_probe"].delete_many({})
        print("writes_restored: YES", flush=True)
    except Exception as e:
        print("writes_restored: NO", type(e).__name__, str(e)[:180], flush=True)
    date = "2026-08-01"
    before = await db.products.count_documents(
        {"publish_status": "Published", "active_date_key": {"$in": ["20260801", "2026-08-01"]}}
    )
    print("before_count", before, "backend", storage.mode, flush=True)
    t1 = time.time()
    result = await ha.archive_product_history_for_date(db, date)
    print(
        "archive_status",
        result.get("status"),
        "records",
        result.get("record_count") or (result.get("manifest") or {}).get("record_count"),
        "elapsed",
        round(time.time() - t1, 1),
        flush=True,
    )
    man = result.get("manifest") or {}
    print("eligible_for_prune", man.get("eligible_for_prune"), "sha", (man.get("sha256") or "")[:16], flush=True)
    read = await hh.read_product_history(db, date_key=date, page=1, page_size=3)
    print(
        "hybrid_total",
        read.get("total"),
        "sources",
        read.get("sources"),
        "page",
        len(read.get("rows") or []),
        flush=True,
    )
    pruned = await ha.prune_product_history_date(db, date)
    print("prune", pruned.get("status"), pruned.get("reason"), flush=True)
    after = await db.products.count_documents(
        {"publish_status": "Published", "active_date_key": {"$in": ["20260801", "2026-08-01"]}}
    )
    print("after_count", after, "unchanged", after == before, flush=True)
    second = await ha.archive_product_history_for_date(db, date)
    print("second", second.get("status"), flush=True)
    client.close()
    print("DONE", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
