"""Daily verification session uniqueness + Product Hub scoped lookup tests."""
import asyncio
import os
import sys
import uuid
from pathlib import Path

import pytest
from dotenv import load_dotenv
from motor.motor_asyncio import AsyncIOMotorClient

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
load_dotenv(ROOT / ".env")

from auto_perpetual import get_or_create_auto_daily_session, ist_date_key, inventory_date_key  # noqa: E402
import mobile_api  # noqa: E402
from mobile_api import (  # noqa: E402
    _get_or_create_mobile_daily_verification_session,
    cleanup_duplicate_daily_verification_sessions,
    find_scoped_product,
)


BRAND = "Hyundai"
DEALER = "FPL Hyundai"
BRANCH = "Vanagaram"


@pytest.fixture(scope="module")
def anyio_backend():
    return "asyncio"


def _db():
    return AsyncIOMotorClient(os.environ["MONGO_URL"])[os.environ.get("DB_NAME", "nmts")]


async def _bind():
    mobile_api.db = _db()
    return mobile_api.db


@pytest.mark.asyncio
async def test_aops_same_day_reuses_session_across_status():
    db = await _bind()
    mu = f"AUDIT_FIX_AOPS_{uuid.uuid4().hex[:8]}"
    day = ist_date_key()
    try:
        first = await get_or_create_auto_daily_session(
            db, mobile_user_id=mu, brand_name=BRAND, dealer_name=DEALER, branch=BRANCH, device_id="d1"
        )
        await db.stock_verification_sessions.update_one({"session_id": first}, {"$set": {"status": "IN_PROGRESS"}})
        second = await get_or_create_auto_daily_session(
            db, mobile_user_id=mu, brand_name=BRAND, dealer_name=DEALER, branch=BRANCH, device_id="d2"
        )
        await db.stock_verification_sessions.update_one({"session_id": first}, {"$set": {"status": "COMPLETED"}})
        third = await get_or_create_auto_daily_session(
            db, mobile_user_id=mu, brand_name=BRAND, dealer_name=DEALER, branch=BRANCH, device_id="d3"
        )
        assert first == second == third
        assert first.startswith("AOPS")
        rows = await db.stock_verification_sessions.find(
            {
                "session_kind": "auto_perpetual",
                "verification_date": day,
                "mobile_user_id": mu,
                "brand_id": BRAND,
                "dealer_id": DEALER,
                "branch_id": BRANCH,
            },
            {"_id": 0, "session_id": 1},
        ).to_list(20)
        assert {r["session_id"] for r in rows} == {first}
    finally:
        await db.stock_verification_sessions.delete_many({"mobile_user_id": mu})


@pytest.mark.asyncio
async def test_mops_same_day_reuses_session_across_status():
    db = await _bind()
    mu = f"AUDIT_FIX_MOPS_{uuid.uuid4().hex[:8]}"
    try:
        first = await _get_or_create_mobile_daily_verification_session(mu, "d1", BRAND, DEALER, BRANCH, "physical")
        await db.stock_verification_sessions.update_one({"session_id": first}, {"$set": {"status": "IN_PROGRESS"}})
        second = await _get_or_create_mobile_daily_verification_session(mu, "d2", BRAND, DEALER, BRANCH, "physical")
        await db.stock_verification_sessions.update_one({"session_id": first}, {"$set": {"status": "submitted"}})
        third = await _get_or_create_mobile_daily_verification_session(mu, "d3", BRAND, DEALER, BRANCH, "physical")
        assert first == second == third
        assert first.startswith("MOPS")
    finally:
        await db.stock_verification_sessions.delete_many({"mobile_user_id": mu})


@pytest.mark.asyncio
async def test_parallel_get_or_create_single_aops():
    db = await _bind()
    mu = f"AUDIT_FIX_PAR_{uuid.uuid4().hex[:8]}"
    try:
        ids = await asyncio.gather(*[
            get_or_create_auto_daily_session(
                db, mobile_user_id=mu, brand_name=BRAND, dealer_name=DEALER, branch=BRANCH, device_id=f"d{i}"
            )
            for i in range(8)
        ])
        assert len(set(ids)) == 1
        count = await db.stock_verification_sessions.count_documents(
            {
                "session_kind": "auto_perpetual",
                "verification_date": ist_date_key(),
                "mobile_user_id": mu,
                "brand_id": BRAND,
                "dealer_id": DEALER,
                "branch_id": BRANCH,
            }
        )
        # Unique index may already exist after cleanup; at most one parent.
        assert count == 1
    finally:
        await db.stock_verification_sessions.delete_many({"mobile_user_id": mu})


@pytest.mark.asyncio
async def test_different_user_branch_kind_and_day_boundaries():
    db = await _bind()
    mu1 = f"AUDIT_FIX_U1_{uuid.uuid4().hex[:8]}"
    mu2 = f"AUDIT_FIX_U2_{uuid.uuid4().hex[:8]}"
    try:
        aops = await get_or_create_auto_daily_session(
            db, mobile_user_id=mu1, brand_name=BRAND, dealer_name=DEALER, branch=BRANCH, device_id="d"
        )
        mops = await _get_or_create_mobile_daily_verification_session(mu1, "d", BRAND, DEALER, BRANCH, "physical")
        other_user = await get_or_create_auto_daily_session(
            db, mobile_user_id=mu2, brand_name=BRAND, dealer_name=DEALER, branch=BRANCH, device_id="d"
        )
        other_branch = await get_or_create_auto_daily_session(
            db, mobile_user_id=mu1, brand_name=BRAND, dealer_name=DEALER, branch="Retteri", device_id="d"
        )
        assert aops != mops
        assert aops != other_user
        assert aops != other_branch
    finally:
        await db.stock_verification_sessions.delete_many({"mobile_user_id": {"$in": [mu1, mu2]}})


@pytest.mark.asyncio
async def test_cleanup_idempotent_and_preserves_history():
    db = await _bind()
    mu = f"AUDIT_FIX_CLN_{uuid.uuid4().hex[:8]}"
    day = ist_date_key()
    sid_a = f"AOPSTEST{uuid.uuid4().hex[:6].upper()}"
    sid_b = f"AOPSTEST{uuid.uuid4().hex[:6].upper()}"
    index_name = "uq_daily_verification_session_identity"
    try:
        # Temporarily relax uniqueness so we can seed a duplicate group.
        try:
            await db.stock_verification_sessions.drop_index(index_name)
        except Exception:
            pass
        await db.stock_verification_sessions.insert_many([
            {
                "id": str(uuid.uuid4()),
                "session_id": sid_a,
                "session_kind": "auto_perpetual",
                "verification_date": day,
                "mobile_user_id": mu,
                "brand_id": BRAND,
                "dealer_id": DEALER,
                "branch_id": BRANCH,
                "status": "IN_PROGRESS",
                "created_at": "2026-08-09T01:00:00",
                "total_items": 0,
            },
            {
                "id": str(uuid.uuid4()),
                "session_id": sid_b,
                "session_kind": "auto_perpetual",
                "verification_date": day,
                "mobile_user_id": mu,
                "brand_id": BRAND,
                "dealer_id": DEALER,
                "branch_id": BRANCH,
                "status": "ACTIVE",
                "created_at": "2026-08-09T02:00:00",
                "total_items": 0,
            },
        ])
        hist_id = str(uuid.uuid4())
        await db.stock_verification_history.insert_one({
            "id": hist_id,
            "session_id": sid_b,
            "part_number": "TESTPART1",
            "mobile_user_id": mu,
            "brand_name": BRAND,
            "dealer_name": DEALER,
            "branch": BRANCH,
        })
        stats1 = await cleanup_duplicate_daily_verification_sessions(db)
        assert stats1["groups_merged"] >= 1
        hist = await db.stock_verification_history.find_one({"id": hist_id}, {"_id": 0, "session_id": 1})
        assert hist["session_id"] == sid_a  # earliest created_at is canonical
        count = await db.stock_verification_sessions.count_documents(
            {"mobile_user_id": mu, "session_kind": "auto_perpetual", "verification_date": day}
        )
        assert count == 1
        stats2 = await cleanup_duplicate_daily_verification_sessions(db)
        assert stats2["groups_merged"] == 0
        hist2 = await db.stock_verification_history.find_one({"id": hist_id}, {"_id": 0})
        assert hist2 is not None
    finally:
        await db.stock_verification_sessions.delete_many({"mobile_user_id": mu})
        await db.stock_verification_history.delete_many({"mobile_user_id": mu})
        try:
            await db.stock_verification_sessions.create_index(
                [
                    ("session_kind", 1),
                    ("verification_date", 1),
                    ("mobile_user_id", 1),
                    ("brand_id", 1),
                    ("dealer_id", 1),
                    ("branch_id", 1),
                ],
                unique=True,
                name=index_name,
                partialFilterExpression={
                    "session_kind": {"$in": ["auto_perpetual", "physical_perpetual", "mobile_daily"]},
                    "mobile_user_id": {"$type": "string"},
                    "brand_id": {"$type": "string"},
                    "dealer_id": {"$type": "string"},
                    "branch_id": {"$type": "string"},
                    "verification_date": {"$type": "string"},
                },
            )
        except Exception:
            pass


@pytest.mark.asyncio
async def test_find_scoped_product_uses_product_hub_today_scope():
    await _bind()
    product, pn = await find_scoped_product("2630002752", BRAND, DEALER, BRANCH)
    assert pn == "2630002752"
    assert product is not None
    assert product.get("active_date_key") == inventory_date_key()
    assert product.get("publish_status") == "Published"
    assert product.get("is_active_today") is True
    assert float(product.get("available_qty_number") or 0) == float(
        (await _db().products.find_one(
            {
                "brand_name": BRAND,
                "dealer_name": DEALER,
                "branch": BRANCH,
                "publish_status": "Published",
                "is_active_today": True,
                "active_date_key": inventory_date_key(),
                "part_number": "2630002752",
            },
            {"_id": 0, "available_qty_number": 1},
        ) or {}).get("available_qty_number") or 0
    )


def test_run_async_suite():
    """Allow `python tests/test_daily_session_uniqueness.py` without pytest-asyncio plugin."""
    async def _run():
        await test_aops_same_day_reuses_session_across_status()
        await test_mops_same_day_reuses_session_across_status()
        await test_parallel_get_or_create_single_aops()
        await test_different_user_branch_kind_and_day_boundaries()
        await test_cleanup_idempotent_and_preserves_history()
        await test_find_scoped_product_uses_product_hub_today_scope()
        print("ALL_SESSION_TESTS_PASS")

    asyncio.run(_run())


if __name__ == "__main__":
    test_run_async_suite()
