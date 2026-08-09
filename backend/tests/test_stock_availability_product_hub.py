"""Stock Availability must use current Product Hub data only (no history/duplicates/422)."""
import asyncio
import inspect
import os
import re
import sys
from collections import Counter
from pathlib import Path
from dotenv import load_dotenv
from motor.motor_asyncio import AsyncIOMotorClient

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
load_dotenv(ROOT / ".env")

from auto_perpetual import inventory_date_key  # noqa: E402
from mobile_api import (  # noqa: E402
    _dedupe_product_hub_rows,
    _product_hub_stock_scope,
    stock_search,
)


BRAND = "Hyundai"
DEALER = "FPL Hyundai"
BRANCH = "Vanagaram"
PART = "2630002752"
PREFIX = "26300"


def test_single_search_part_numbers_not_required():
    """Root cause of HTTP 422: part_numbers must be optional for Single Search."""
    params = inspect.signature(stock_search).parameters
    pn = params["part_numbers"]
    assert pn.default is not inspect.Parameter.empty
    assert getattr(pn.default, "default", pn.default) is None
    q = params["q"]
    assert getattr(q.default, "default", q.default) is None


async def _async_main():
    db = AsyncIOMotorClient(os.environ["MONGO_URL"])[os.environ.get("DB_NAME", "nmts")]
    today = inventory_date_key()
    session = {"brand_name": BRAND, "dealer_name": DEALER, "branch": BRANCH}
    scope = _product_hub_stock_scope(session, today)

    # Product Hub current scope must include the example part once with qty 214.
    hub_rows = await db.products.find({**scope, "part_number": PART}, {"_id": 0, "part_number": 1, "available_qty_number": 1, "active_date_key": 1, "is_active_today": 1}).to_list(10)
    assert len(hub_rows) == 1, hub_rows
    assert hub_rows[0]["active_date_key"] == today
    assert float(hub_rows[0]["available_qty_number"]) == 214.0

    # Old is_active_today-only query (duplicate root cause) returns 2 rows.
    stale_scope = {
        "brand_name": BRAND,
        "dealer_name": DEALER,
        "branch": BRANCH,
        "publish_status": {"$in": ["Published", "published"]},
        "is_active_today": True,
        "part_number": PART,
    }
    stale = await db.products.find(stale_scope, {"_id": 0, "part_number": 1, "active_date_key": 1, "available_qty_number": 1}).to_list(10)
    assert len(stale) >= 2
    assert any(r["active_date_key"] != today for r in stale)

    # Prefix under Product Hub scope: unique parts, no historical duplicates.
    safe = re.escape(PREFIX)
    prefix_rows = await db.products.find(
        {
            **scope,
            "$or": [
                {"part_number": {"$regex": f"^{safe}", "$options": "i"}},
                {"item_name": {"$regex": safe, "$options": "i"}},
            ],
        },
        {"_id": 0, "part_number": 1, "active_date_key": 1, "available_qty_number": 1},
    ).limit(200).to_list(200)
    assert any(r["part_number"] == PART for r in prefix_rows)
    assert all(r["active_date_key"] == today for r in prefix_rows)
    counts = Counter(r["part_number"] for r in prefix_rows)
    assert not [p for p, n in counts.items() if n > 1]

    # Dedupe helper collapses intentional duplicate cards.
    deduped = _dedupe_product_hub_rows(stale + stale)
    assert len(deduped) == 1

    # Exact multi: found once + not found preserved.
    parts = [PART, "ZZZZNOTEXIST999", PART.lower()]
    multi = await db.products.find({**scope, "part_number": {"$in": list({*parts, PART.upper(), PART.lower()})}}, {"_id": 0, "part_number": 1}).to_list(20)
    multi = _dedupe_product_hub_rows(multi)
    found_upper = {str(r["part_number"]).upper() for r in multi}
    assert found_upper == {PART}
    assert len(multi) == 1

    # Brand/Dealer/Branch scope pins paired session values only.
    assert scope["branch"] == BRANCH
    assert scope["brand_name"] == BRAND
    assert scope["dealer_name"] == DEALER

    # No-today branch: empty scope keys historically have older active rows only.
    no_today_scope = _product_hub_stock_scope(
        {"brand_name": "", "dealer_name": "", "branch": ""}, today
    )
    assert await db.products.count_documents(no_today_scope) == 0
    older = await db.products.count_documents(
        {"brand_name": "", "dealer_name": "", "branch": "", "publish_status": "Published", "is_active_today": True}
    )
    assert older > 0

    print("PRODUCT_HUB_SCOPE", scope)
    print("HUB_QTY", hub_rows[0]["available_qty_number"])
    print("PREFIX_UNIQUE", len(counts))
    print("STALE_DUP_COUNT", len(stale))
    print("ALL_PASS")


def test_product_hub_stock_data_contract():
    test_single_search_part_numbers_not_required()
    asyncio.run(_async_main())


if __name__ == "__main__":
    test_product_hub_stock_data_contract()
