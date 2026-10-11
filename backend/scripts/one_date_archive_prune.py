#!/usr/bin/env python3
"""One-date Product Hub archive prune (Move: verified S3 then delete Mongo).

Default is verify-only. Pass --execute to prune a single historical date
after REAL S3 re-verification. Never enables host-wide ARCHIVE_PRUNE_ENABLED.
Never deletes today's live Product Hub rows or Upload History metadata.
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))


async def _main(date_iso: str, execute: bool) -> int:
    from dotenv import load_dotenv

    env_file = os.getenv("NMTS_ENV_FILE") or str(BACKEND / ".env")
    load_dotenv(env_file, override=True)
    if os.getenv("APP_ENV", "").strip().lower() == "testing":
        print("Refusing: this script must not run against the testing overlay.")
        return 2

    os.environ.setdefault("ARCHIVE_SCHEDULER_ENABLED", "false")
    if execute:
        os.environ["ARCHIVE_PRUNE_ENABLED"] = "true"
    else:
        os.environ.setdefault("ARCHIVE_PRUNE_ENABLED", os.getenv("ARCHIVE_PRUNE_ENABLED", "false"))

    from mongo_connection import build_mongo_client_args, resolve_mongo_url
    from motor.motor_asyncio import AsyncIOMotorClient
    import history_archive as ha
    import hybrid_history as hh
    import s3_storage

    mongo_url = resolve_mongo_url()
    url, kwargs = build_mongo_client_args(mongo_url)
    client = AsyncIOMotorClient(url, **kwargs)
    db = client[os.environ.get("DB_NAME", "nmts")]
    storage = s3_storage.get_storage()
    date_iso = ha.date_key_to_iso(date_iso)

    manifests = await db.archive_manifests.find(
        {"module": "product-history", "archive_date": date_iso},
        {"_id": 0},
    ).to_list(50)
    print(f"date={date_iso} manifests={len(manifests)} storage={storage.mode} real_s3={storage.is_s3()}")
    if not manifests:
        print("No product-history manifests for this date. Mongo untouched.")
        return 1

    mongo_n = await db.products.count_documents({
        "publish_status": "Published",
        "active_date_key": {"$in": [date_iso, date_iso.replace("-", "")]},
    })
    uploads_n = await db.uploads.count_documents({})
    print(f"mongo_products_for_date={mongo_n} uploads_metadata_total={uploads_n}")

    for man in manifests:
        key = man.get("storage_key") or ""
        status = man.get("status")
        eligible = bool(man.get("eligible_for_prune"))
        ok = storage.verify_object(key, man.get("sha256") or "", int(man.get("file_size") or 0)) if key else False
        print(
            f"  archive_id={man.get('archive_id')} status={status} "
            f"eligible={eligible} records={man.get('record_count')} "
            f"key={key} reverify={ok}"
        )
        if not ok:
            print("S3 re-verification failed. Mongo untouched.")
            return 1

    history = await hh.summarize_product_history(db, date_key=date_iso)
    print(f"history_summary_rows={len(history)} (Mongo or S3 fallback)")

    if not execute:
        print("Verify-only complete. Re-run with --execute to prune this date.")
        return 0

    result = await ha.prune_product_history_date(db, date_iso)
    print(f"prune_result={result}")
    if result.get("status") != "pruned":
        return 1

    after = await db.products.count_documents({
        "publish_status": "Published",
        "active_date_key": {"$in": [date_iso, date_iso.replace("-", "")]},
    })
    uploads_after = await db.uploads.count_documents({})
    history_after = await hh.summarize_product_history(db, date_key=date_iso)
    print(
        f"after_mongo_products={after} uploads_metadata_total={uploads_after} "
        f"history_summary_rows={len(history_after)}"
    )
    if after != 0:
        print("WARNING: Mongo still has products for this date.")
        return 1
    if uploads_after != uploads_n:
        print("WARNING: Upload History metadata count changed.")
        return 1
    if not history_after:
        print("WARNING: Product Hub History could not read the date after prune.")
        return 1
    print("One-date Move verified: S3 archive retained, History readable, Mongo historical copy removed.")
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--date", required=True, help="Historical date YYYY-MM-DD")
    parser.add_argument("--execute", action="store_true", help="Delete verified Mongo copy for this date only")
    args = parser.parse_args()
    raise SystemExit(asyncio.run(_main(args.date, args.execute)))


if __name__ == "__main__":
    main()
