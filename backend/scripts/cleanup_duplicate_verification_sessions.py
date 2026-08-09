#!/usr/bin/env python3
"""One-time / idempotent cleanup of duplicate daily verification sessions."""
import asyncio
import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from motor.motor_asyncio import AsyncIOMotorClient

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
load_dotenv(ROOT / ".env")

from mobile_api import cleanup_duplicate_daily_verification_sessions  # noqa: E402


async def main():
    client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    database = client[os.environ.get("DB_NAME", "nmts")]
    before_groups = await database.stock_verification_sessions.aggregate([
        {
            "$match": {
                "session_kind": {"$in": ["auto_perpetual", "physical_perpetual", "mobile_daily"]},
                "mobile_user_id": {"$type": "string", "$ne": ""},
                "brand_id": {"$type": "string", "$ne": ""},
                "dealer_id": {"$type": "string", "$ne": ""},
                "branch_id": {"$type": "string", "$ne": ""},
                "verification_date": {"$type": "string", "$ne": ""},
            }
        },
        {
            "$group": {
                "_id": {
                    "session_kind": "$session_kind",
                    "verification_date": "$verification_date",
                    "mobile_user_id": "$mobile_user_id",
                    "brand_id": "$brand_id",
                    "dealer_id": "$dealer_id",
                    "branch_id": "$branch_id",
                },
                "count": {"$sum": 1},
            }
        },
        {"$match": {"count": {"$gt": 1}}},
        {"$count": "n"},
    ]).to_list(1)
    print("BEFORE_DUP_GROUPS", (before_groups[0]["n"] if before_groups else 0))
    stats = await cleanup_duplicate_daily_verification_sessions(database)
    print("CLEANUP_STATS", stats)
    after_groups = await database.stock_verification_sessions.aggregate([
        {
            "$match": {
                "session_kind": {"$in": ["auto_perpetual", "physical_perpetual", "mobile_daily"]},
                "mobile_user_id": {"$type": "string", "$ne": ""},
                "brand_id": {"$type": "string", "$ne": ""},
                "dealer_id": {"$type": "string", "$ne": ""},
                "branch_id": {"$type": "string", "$ne": ""},
                "verification_date": {"$type": "string", "$ne": ""},
            }
        },
        {
            "$group": {
                "_id": {
                    "session_kind": "$session_kind",
                    "verification_date": "$verification_date",
                    "mobile_user_id": "$mobile_user_id",
                    "brand_id": "$brand_id",
                    "dealer_id": "$dealer_id",
                    "branch_id": "$branch_id",
                },
                "count": {"$sum": 1},
            }
        },
        {"$match": {"count": {"$gt": 1}}},
        {"$count": "n"},
    ]).to_list(1)
    print("AFTER_DUP_GROUPS", (after_groups[0]["n"] if after_groups else 0))
    # Idempotent second pass
    stats2 = await cleanup_duplicate_daily_verification_sessions(database)
    print("CLEANUP_SECOND_PASS", stats2)


if __name__ == "__main__":
    asyncio.run(main())
