#!/usr/bin/env python3
"""Compare SOURCE MongoDB (Atlas) vs TARGET DocumentDB. Read-only; no writes.

For every user collection reports:
  - document count match/mismatch
  - index names present on source that are missing on target

Also prints every document in `counters` from both sides for manual comparison.

Required env:
  SOURCE_MONGO_URL
  TARGET_MONGO_URL

Optional env:
  SOURCE_DB_NAME / TARGET_DB_NAME  (default: DB_NAME, then "nmts")
  DOCDB_TLS_CA_FILE                DocumentDB CA bundle; applied only to TARGET
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from motor.motor_asyncio import AsyncIOMotorClient

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
load_dotenv(ROOT / ".env")

from mongo_connection import build_mongo_client_args  # noqa: E402

DEFAULT_DB_NAME = "nmts"


def _db_name(prefix: str) -> str:
    return os.environ.get(f"{prefix}_DB_NAME") or os.environ.get("DB_NAME") or DEFAULT_DB_NAME


def _user_collections(names):
    return [n for n in names if not n.startswith("system.")]


def _jsonable(value):
    return json.dumps(value, default=str, sort_keys=True)


async def _index_names(db, collection_name: str, exists: bool):
    if not exists:
        return set()
    info = await db[collection_name].index_information()
    return set(info.keys())


async def main() -> int:
    source_url = os.environ.get("SOURCE_MONGO_URL")
    target_url = os.environ.get("TARGET_MONGO_URL")
    if not source_url or not target_url:
        print("SOURCE_MONGO_URL and TARGET_MONGO_URL are required", file=sys.stderr)
        return 1

    source_client = AsyncIOMotorClient(source_url)
    target_resolved_url, target_kwargs = build_mongo_client_args(target_url)
    target_client = AsyncIOMotorClient(target_resolved_url, **target_kwargs)

    source_db = source_client[_db_name("SOURCE")]
    target_db = target_client[_db_name("TARGET")]

    source_names = set(_user_collections(await source_db.list_collection_names()))
    target_names = set(_user_collections(await target_db.list_collection_names()))
    all_names = sorted(source_names | target_names)

    mismatches = 0
    missing_indexes = 0
    print("=== collection parity ===")
    for name in all_names:
        in_source = name in source_names
        in_target = name in target_names
        source_count = await source_db[name].count_documents({}) if in_source else None
        target_count = await target_db[name].count_documents({}) if in_target else None
        count_ok = source_count == target_count
        if not count_ok:
            mismatches += 1

        src_idx = await _index_names(source_db, name, in_source)
        tgt_idx = await _index_names(target_db, name, in_target)
        missing_on_target = sorted(src_idx - tgt_idx)
        extra_on_target = sorted(tgt_idx - src_idx)
        if missing_on_target:
            missing_indexes += 1

        status = "MATCH" if count_ok and in_source and in_target else "MISMATCH"
        print(
            f"{name}: counts {status} source={source_count} target={target_count} "
            f"source_indexes={sorted(src_idx)} target_indexes={sorted(tgt_idx)} "
            f"missing_on_target={missing_on_target} extra_on_target={extra_on_target}"
        )

    print()
    print("=== counters (source) ===")
    if "counters" in source_names:
        async for doc in source_db.counters.find({}):
            print(_jsonable(doc))
    else:
        print("(collection missing on source)")

    print()
    print("=== counters (target) ===")
    if "counters" in target_names:
        async for doc in target_db.counters.find({}):
            print(_jsonable(doc))
    else:
        print("(collection missing on target)")

    print()
    print(
        f"SUMMARY count_mismatches={mismatches} "
        f"collections_missing_source_indexes_on_target={missing_indexes} "
        f"collections_compared={len(all_names)}"
    )

    source_client.close()
    target_client.close()
    return 0 if mismatches == 0 and missing_indexes == 0 else 2


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
