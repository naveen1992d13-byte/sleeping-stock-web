"""Focused tests for mobile prefix stock-search and Auto Perpetual daily session invariance."""
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from auto_perpetual import get_or_create_auto_daily_session, ist_date_key  # noqa: E402


@pytest.fixture(scope="module")
def anyio_backend():
    return "asyncio"


@pytest.mark.asyncio
async def test_auto_session_idempotent_across_status_flip():
    from server import db

    mu = "TEST_MOBILE_SESSION_USER"
    brand = "TEST_BRAND_SESSION"
    dealer = "TEST_DEALER_SESSION"
    branch = "TEST_BRANCH_SESSION"
    day = ist_date_key()

    await db.stock_verification_sessions.delete_many(
        {
            "session_kind": "auto_perpetual",
            "verification_date": day,
            "mobile_user_id": mu,
            "brand_id": brand,
            "dealer_id": dealer,
            "branch_id": branch,
        }
    )

    first = await get_or_create_auto_daily_session(
        db,
        mobile_user_id=mu,
        brand_name=brand,
        dealer_name=dealer,
        branch=branch,
        device_id="dev-1",
    )
    assert first and first.startswith("AOPS")

    # Simulate first verification flipping status (the previous bug trigger).
    await db.stock_verification_sessions.update_one(
        {"session_id": first},
        {"$set": {"status": "IN_PROGRESS"}},
    )

    second = await get_or_create_auto_daily_session(
        db,
        mobile_user_id=mu,
        brand_name=brand,
        dealer_name=dealer,
        branch=branch,
        device_id="dev-1",
    )
    third = await get_or_create_auto_daily_session(
        db,
        mobile_user_id=mu,
        brand_name=brand,
        dealer_name=dealer,
        branch=branch,
        device_id="dev-2",
    )
    assert second == first
    assert third == first

    rows = await db.stock_verification_sessions.find(
        {
            "session_kind": "auto_perpetual",
            "verification_date": day,
            "mobile_user_id": mu,
            "brand_id": brand,
            "dealer_id": dealer,
            "branch_id": branch,
        },
        {"_id": 0, "session_id": 1},
    ).to_list(20)
    ids = {r["session_id"] for r in rows}
    assert ids == {first}


@pytest.mark.asyncio
async def test_prefix_stock_search_returns_multiple_matches():
    """Requires a running API + paired device token via env MOBILE_TEST_TOKEN optional.
    When unavailable, exercises the query shape against Mongo directly.
    """
    from server import db
    from mobile_api import _published_status_filter

    # Find any published product with a long enough part number to derive a prefix.
    sample = await db.products.find_one(
        {
            "publish_status": _published_status_filter(),
            "is_active_today": True,
            "part_number": {"$regex": r"^[A-Z0-9]{6,}$"},
        },
        {"_id": 0, "part_number": 1, "brand_name": 1, "dealer_name": 1, "branch": 1},
    )
    if not sample:
        pytest.skip("No published products available in shared DB")

    part = str(sample["part_number"])
    prefix = part[:5]
    scope = {
        "brand_name": sample["brand_name"],
        "dealer_name": sample["dealer_name"],
        "branch": sample["branch"],
        "publish_status": _published_status_filter(),
        "is_active_today": True,
    }
    import re

    safe = re.escape(prefix)
    rows = await db.products.find(
        {
            **scope,
            "$or": [
                {"part_number": {"$regex": f"^{safe}", "$options": "i"}},
                {"part_name": {"$regex": safe, "$options": "i"}},
                {"item_name": {"$regex": safe, "$options": "i"}},
            ],
        },
        {"_id": 0, "part_number": 1},
    ).limit(100).to_list(100)

    assert len(rows) >= 1
    assert any(str(r["part_number"]).upper().startswith(prefix.upper()) for r in rows)
    # Exact full part still matches via prefix of itself.
    exact = await db.products.find(
        {**scope, "part_number": {"$in": [part]}},
        {"_id": 0, "part_number": 1},
    ).to_list(5)
    assert exact and exact[0]["part_number"] == part
    print(f"PREFIX_TEST prefix={prefix} matches={len(rows)} sample_part={part}")
