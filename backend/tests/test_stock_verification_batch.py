"""Stock verification batch endpoint — reuses single-save session logic."""
from __future__ import annotations

import asyncio
import os
import sys
import uuid
from pathlib import Path

from dotenv import load_dotenv
from motor.motor_asyncio import AsyncIOMotorClient

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
load_dotenv(ROOT / ".env")

import mobile_api  # noqa: E402
from mobile_api import (  # noqa: E402
    StockVerificationBatchSubmit,
    StockVerificationSubmit,
    _process_stock_verification_submit,
    submit_stock_verification_batch,
)


BRAND = "Hyundai"
DEALER = "FPL Hyundai"
BRANCH = "Vanagaram"


def _db():
    return AsyncIOMotorClient(os.environ["MONGO_URL"])[os.environ.get("DB_NAME", "nmts")]


async def _bind():
    mobile_api.db = _db()
    return mobile_api.db


def _fake_session(mobile_user_id: str, device_id: str) -> dict:
    return {
        "device": {"device_id": device_id},
        "mobile_user": {"mobile_user_id": mobile_user_id, "name": "Batch Tester"},
        "brand_name": BRAND,
        "dealer_name": DEALER,
        "branch": BRANCH,
    }


def test_batch_routes_registered():
    paths = {getattr(r, "path", "") for r in mobile_api.router.routes}
    assert "/mobile/stock-verification/batch" in paths
    assert "/mobile/stock-verification/batch/" in paths
    assert "/mobile/stock-verification" in paths


def test_batch_empty_items():
    async def _run():
        await _bind()
        session = _fake_session("BATCH_EMPTY", "dev-empty")
        out = await submit_stock_verification_batch(
            StockVerificationBatchSubmit(items=[]),
            session=session,
        )
        assert out["success"] is True
        assert out["synced"] == 0
        assert out["failed"] == 0
        assert out["results"] == []

    asyncio.run(_run())


def test_batch_reuses_daily_mops_session_and_is_idempotent():
    async def _run():
        db = await _bind()
        mu = f"BATCH_MOPS_{uuid.uuid4().hex[:8]}"
        device_id = f"dev-{uuid.uuid4().hex[:8]}"
        session = _fake_session(mu, device_id)
        client_a = str(uuid.uuid4())
        client_b = str(uuid.uuid4())
        part_a = f"BATCHTEST{uuid.uuid4().hex[:6].upper()}"
        part_b = f"BATCHTEST{uuid.uuid4().hex[:6].upper()}"

        try:
            payload = StockVerificationBatchSubmit(
                items=[
                    StockVerificationSubmit(
                        part_number=part_a,
                        physical_qty=1,
                        location="A1",
                        remark="",
                        entry_method="MANUAL",
                        client_id=client_a,
                        verification_type="physical",
                        damage_qty=0,
                    ),
                    StockVerificationSubmit(
                        part_number=part_b,
                        physical_qty=2,
                        location="B1",
                        remark="",
                        entry_method="MANUAL",
                        client_id=client_b,
                        verification_type="physical",
                        damage_qty=0,
                    ),
                ]
            )
            first = await submit_stock_verification_batch(payload, session=session)
            assert first["success"] is True
            assert first["synced"] == 2
            assert first["failed"] == 0
            assert len(first["results"]) == 2
            assert all(r["success"] for r in first["results"])
            session_ids = {r.get("session_id") for r in first["results"]}
            assert len(session_ids) == 1
            sid = session_ids.pop()
            assert sid and str(sid).startswith("MOPS")

            second = await submit_stock_verification_batch(payload, session=session)
            assert second["synced"] == 2
            assert second["failed"] == 0
            assert all(r.get("duplicate") for r in second["results"])
            assert {r.get("session_id") for r in second["results"]} == {sid}

            single = await _process_stock_verification_submit(
                StockVerificationSubmit(
                    part_number=part_a,
                    physical_qty=1,
                    location="A1",
                    remark="",
                    entry_method="MANUAL",
                    client_id=client_a,
                    verification_type="physical",
                ),
                session,
            )
            assert single.get("duplicate") is True
            assert single.get("session_id") == sid
        finally:
            await db.stock_verification_history.delete_many({"mobile_user_id": mu})
            await db.stock_verification_sessions.delete_many({"mobile_user_id": mu})

    asyncio.run(_run())


def test_batch_isolates_per_item_errors():
    async def _run():
        await _bind()
        mu = f"BATCH_ERR_{uuid.uuid4().hex[:8]}"
        session = _fake_session(mu, f"dev-{uuid.uuid4().hex[:8]}")
        payload = StockVerificationBatchSubmit(
            items=[
                StockVerificationSubmit(
                    part_number="   ",
                    physical_qty=1,
                    location="",
                    remark="",
                    entry_method="MANUAL",
                    client_id=str(uuid.uuid4()),
                    verification_type="physical",
                ),
                StockVerificationSubmit(
                    part_number=f"BATCHOK{uuid.uuid4().hex[:6].upper()}",
                    physical_qty=1,
                    location="C1",
                    remark="",
                    entry_method="MANUAL",
                    client_id=str(uuid.uuid4()),
                    verification_type="physical",
                ),
            ]
        )
        try:
            out = await submit_stock_verification_batch(payload, session=session)
            assert out["synced"] == 1
            assert out["failed"] == 1
            assert out["success"] is False
            assert out["results"][0]["success"] is False
            assert out["results"][1]["success"] is True
        finally:
            db = await _bind()
            await db.stock_verification_history.delete_many({"mobile_user_id": mu})
            await db.stock_verification_sessions.delete_many({"mobile_user_id": mu})

    asyncio.run(_run())
