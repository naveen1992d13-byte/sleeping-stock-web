#!/usr/bin/env python3
"""Copy every collection from SOURCE MongoDB (Atlas) to TARGET (DocumentDB).

Read-only on source. Upserts by _id so re-runs are safe and do not duplicate.
Documents are copied unchanged, including `counters` (sequence numbers).

Required env:
  SOURCE_MONGO_URL   Atlas (or other source) connection string — read only
  TARGET_MONGO_URL   DocumentDB connection string

Optional env:
  SOURCE_DB_NAME / TARGET_DB_NAME  (default: DB_NAME, then "nmts")
  DOCDB_TLS_CA_FILE                DocumentDB CA bundle; applied only to TARGET
  MIGRATE_BATCH_SIZE               bulk upsert batch size (default 500)
"""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from motor.motor_asyncio import AsyncIOMotorClient
from pymongo import ReplaceOne

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
load_dotenv(ROOT / ".env")

from mongo_connection import build_mongo_client_args  # noqa: E402

DEFAULT_DB_NAME = "nmts"
DEFAULT_BATCH_SIZE = 500


def _db_name(prefix: str) -> str:
    return os.environ.get(f"{prefix}_DB_NAME") or os.environ.get("DB_NAME") or DEFAULT_DB_NAME


def _user_collections(names):
    return [n for n in names if not n.startswith("system.")]


async def _copy_collection(source_coll, target_coll, batch_size: int) -> int:
    batch = []
    copied = 0
    async for doc in source_coll.find({}):
        batch.append(ReplaceOne({"_id": doc["_id"]}, doc, upsert=True))
        if len(batch) >= batch_size:
            await target_coll.bulk_write(batch, ordered=False)
            copied += len(batch)
            batch = []
    if batch:
        await target_coll.bulk_write(batch, ordered=False)
        copied += len(batch)
    return copied


async def main() -> int:
    source_url = os.environ.get("SOURCE_MONGO_URL")
    target_url = os.environ.get("TARGET_MONGO_URL")
    if not source_url or not target_url:
        print("SOURCE_MONGO_URL and TARGET_MONGO_URL are required", file=sys.stderr)
        return 1

    try:
        batch_size = int(os.environ.get("MIGRATE_BATCH_SIZE") or DEFAULT_BATCH_SIZE)
    except ValueError:
        print("MIGRATE_BATCH_SIZE must be an integer", file=sys.stderr)
        return 1
    if batch_size < 1:
        print("MIGRATE_BATCH_SIZE must be >= 1", file=sys.stderr)
        return 1

    # Source is Atlas: never apply DocumentDB TLS options.
    source_client = AsyncIOMotorClient(source_url)
    target_resolved_url, target_kwargs = build_mongo_client_args(target_url)
    target_client = AsyncIOMotorClient(target_resolved_url, **target_kwargs)

    source_db = source_client[_db_name("SOURCE")]
    target_db = target_client[_db_name("TARGET")]

    source_names = _user_collections(await source_db.list_collection_names())
    target_existing = set(_user_collections(await target_db.list_collection_names()))
    print(f"source collections: {len(source_names)}")
    print(f"target existing collections: {len(target_existing)}")

    totals = {}
    for name in source_names:
        source_count = await source_db[name].count_documents({})
        if source_count == 0 and name not in target_existing:
            await target_db.create_collection(name)
            copied = 0
        else:
            copied = await _copy_collection(source_db[name], target_db[name], batch_size)
        target_count = await target_db[name].count_documents({})
        totals[name] = {"source": source_count, "copied_upserts": copied, "target": target_count}
        print(f"{name}: source={source_count} upserts={copied} target={target_count}")

    print("DONE")
    print(totals)
    source_client.close()
    target_client.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
