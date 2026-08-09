"""Stock Availability: IST today-only scope, prefix vs exact, no yesterday fallback."""
import asyncio
import os
import re
import sys
from pathlib import Path

from dotenv import load_dotenv
from motor.motor_asyncio import AsyncIOMotorClient

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
load_dotenv(ROOT / ".env")

from auto_perpetual import inventory_date_key  # noqa: E402


PUB = {"$in": ["Published", "published"]}


async def main():
    db = AsyncIOMotorClient(os.environ["MONGO_URL"])[os.environ.get("DB_NAME", "nmts")]
    today = inventory_date_key()
    print("IST_TODAY_KEY", today)

    scope_today = {
        "brand_name": "Hyundai",
        "dealer_name": "FPL Hyundai",
        "branch": "Vanagaram",
        "publish_status": PUB,
        "active_date_key": today,
    }
    assert await db.products.find_one(scope_today, {"_id": 1}), "Vanagaram must have today's upload for this test"

    # Exact
    exact = await db.products.find({**scope_today, "part_number": {"$in": ["2630002752"]}}, {"_id": 0, "part_number": 1, "active_date_key": 1}).to_list(5)
    print("EXACT", exact)
    assert exact and exact[0]["active_date_key"] == today

    # Prefix 26300 — today's only
    safe = re.escape("26300")
    prefix_rows = await db.products.find(
        {
            **scope_today,
            "$or": [
                {"part_number": {"$regex": f"^{safe}", "$options": "i"}},
                {"part_name": {"$regex": safe, "$options": "i"}},
                {"item_name": {"$regex": safe, "$options": "i"}},
            ],
        },
        {"_id": 0, "part_number": 1, "active_date_key": 1},
    ).limit(100).to_list(100)
    print("PREFIX_COUNT", len(prefix_rows))
    print("PREFIX", [r["part_number"] for r in prefix_rows[:12]])
    assert len(prefix_rows) > 1
    assert all(r["active_date_key"] == today for r in prefix_rows)
    assert any(r["part_number"] == "2630002752" for r in prefix_rows)

    # Old is_active_today-only query would also include 20260801 — prove today's filter excludes it
    stale = await db.products.count_documents(
        {
            "brand_name": "Hyundai",
            "dealer_name": "FPL Hyundai",
            "branch": "Vanagaram",
            "publish_status": PUB,
            "is_active_today": True,
            "active_date_key": {"$ne": today},
            "part_number": {"$regex": f"^{safe}", "$options": "i"},
        }
    )
    print("STALE_ACTIVE_PREFIX_ROWS_EXCLUDED", stale)

    # Multiple exact + one wrong
    parts = ["2630002752", "ZZZZNOTEXIST999"]
    multi = await db.products.find({**scope_today, "part_number": {"$in": parts}}, {"_id": 0, "part_number": 1}).to_list(10)
    found = {r["part_number"] for r in multi}
    not_found = [p for p in parts if p not in found]
    print("MULTI_FOUND", found, "NOT_FOUND", not_found)
    assert found == {"2630002752"} and not_found == ["ZZZZNOTEXIST999"]

    # No-today-upload branch: empty brand/dealer/branch only has 20260802
    no_today_scope = {
        "brand_name": "",
        "dealer_name": "",
        "branch": "",
        "publish_status": PUB,
        "active_date_key": today,
    }
    no_today = await db.products.count_documents(no_today_scope)
    older = await db.products.count_documents(
        {"brand_name": "", "dealer_name": "", "branch": "", "publish_status": PUB, "is_active_today": True}
    )
    print("NO_TODAY_COUNT", no_today, "OLDER_ACTIVE_COUNT", older)
    assert no_today == 0
    assert older > 0  # would incorrectly show if we used is_active_today alone

    # Aging highlight rule unit check (mirrors mobile AgingBadge)
    def hot(value, threshold):
        m = re.search(r"-?\d+(\.\d+)?", str(value))
        days = float(m.group(0)) if m else None
        return days is not None and days > threshold

    assert hot(120, 90) and not hot(90, 90) and not hot(60, 90)
    assert hot(200, 180) and not hot(180, 180) and not hot(120, 180)
    print("AGING_RULE_OK")
    print("ALL_PASS")


if __name__ == "__main__":
    asyncio.run(main())
