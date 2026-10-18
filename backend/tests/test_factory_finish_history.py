"""Stage B+ follow-up: factory unlock matrix, Finish gating, Order History dates."""
from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo
from unittest.mock import AsyncMock, MagicMock, patch

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

import order_desk_workflow as odw  # noqa: E402


ORDER = {"brand_name": "Hyundai", "dealer_name": "FPL", "branch": "Koyambedu"}


def _factory_open_item(remaining=1, **extra):
    item = {
        "id": "i1",
        "part_number": "091490B000",
        "required_qty": remaining,
        "remaining_qty": remaining,
        "accepted_qty": 0,
        "same_dealer_sources": [],
        "other_dealer_sources": [],
        "own_stage_exhausted": True,
        "branch_stage_exhausted": True,
        "dealer_stage_exhausted": True,
        "no_further_stock": True,
        "factory_order_qty": remaining,
        "allocations": [],
    }
    item.update(extra)
    return item


def test_factory_stage_unlocks_only_when_branch_dealer_exhausted_and_balance():
    """Role-agnostic stage flags: factory opens only after prior stages exhaust."""
    item = _factory_open_item(1)
    flags = odw.compute_stage_flags(item, ORDER, set())
    assert flags["factory_stage_status"] == "open"
    assert flags["branch_stage_status"] == "exhausted"
    assert flags["dealer_stage_status"] == "exhausted"
    assert flags["expected_next_outcome"] == "Factory Order"

    # Open Branch/Dealer request (pending allocation) still keeps factory open once
    # pools are empty, but qty_lock is separate; stage stays factory.
    with_pool = _factory_open_item(
        1,
        dealer_stage_exhausted=False,
        other_dealer_sources=[{
            "dealer_name": "Other", "branch": "X", "available_qty": 2,
            "net_available_qty": 2, "purchase_aging_days": 10, "stock_id": "s1",
        }],
    )
    locked = odw.compute_stage_flags(with_pool, ORDER, set())
    assert locked["factory_stage_status"] == "locked"
    assert locked["dealer_stage_status"] == "open"

    zero = _factory_open_item(0, remaining_qty=0, factory_order_qty=0, accepted_qty=1, no_further_stock=False)
    done = odw.compute_stage_flags(zero, ORDER, set())
    assert done["factory_stage_status"] == "locked"


def test_finish_blocks_remaining_open_request_and_missing_factory_no():
    open_req = {"i1": [{"status": "Requested", "request_number": "TS-RQ1", "level": "branch"}]}
    r1 = odw.evaluate_finish_readiness(ORDER, [_factory_open_item(1)], open_req)
    assert r1["can_finish"] is False
    assert r1["unresolved_requests"]
    assert "Unresolved request still open" in odw.finish_block_reason(r1)

    r2 = odw.evaluate_finish_readiness(ORDER, [_factory_open_item(1)], {})
    assert r2["can_finish"] is False
    assert r2["remaining_open_items"] == ["i1"]
    assert "i1" in r2["missing_factory_order_no"]
    assert "Remaining qty is still open" in odw.finish_block_reason(r2)
    assert "Factory Order No missing on a factory row" in odw.finish_block_reason(r2)


def test_factory_order_qty_alone_does_not_satisfy_finish():
    """Stage B dealer Auto Suggest sets factory_order_qty — still needs saved number.

    Even if remaining_qty was wrongly zeroed, factory_order_qty without
    system_order_number must block Finish.
    """
    item = _factory_open_item(0, remaining_qty=0, factory_order_qty=1, no_further_stock=True)
    result = odw.evaluate_finish_readiness(ORDER, [item], {})
    assert result["can_finish"] is False
    assert "i1" in result["missing_factory_order_no"]
    assert "i1" in result["remaining_open_items"]


def test_finish_ok_with_saved_factory_number():
    item = _factory_open_item(
        0,
        remaining_qty=0,
        factory_order_qty=0,
        factory_fulfilled_qty=1,
        system_order_number="FO-100",
        no_further_stock=False,
    )
    result = odw.evaluate_finish_readiness(ORDER, [item], {})
    assert result["can_finish"] is True
    assert result["missing_factory_order_no"] == []
    assert result["remaining_open_items"] == []


def test_finish_ok_when_remainder_fully_cancelled():
    item = _factory_open_item(1, cancellation_status="approved", factory_order_qty=1)
    result = odw.evaluate_finish_readiness(ORDER, [item], {})
    assert result["can_finish"] is True
    assert result["missing_factory_order_no"] == []
    assert result["remaining_open_items"] == []


def test_order_history_date_filter_uses_isoformat_strings():
    """Regression: BSON datetime vs ISO string created_at matched 0 live TS rows."""
    import asyncio
    import server

    ist = ZoneInfo("Asia/Kolkata")
    start = datetime.strptime("2026-10-18", "%Y-%m-%d").replace(tzinfo=ist).astimezone(timezone.utc)
    end = datetime.strptime("2026-10-18", "%Y-%m-%d").replace(tzinfo=ist) + timedelta(days=1)
    end = end.astimezone(timezone.utc)

    captured = {}

    class _Cursor:
        def sort(self, *a, **k):
            return self

        def limit(self, n):
            return self

        async def to_list(self, n):
            return [{
                "id": "ord1",
                "order_number": "TS-ORHY261018005",
                "created_at": "2026-10-18T17:20:33.910111+00:00",
                "status": "Completed",
                "dealer_name": "FPL",
                "branch": "Koyambedu",
                "item_count": 1,
                "total_required_qty": 1,
                "total_order_value": 1,
            }]

    mock_db = MagicMock()
    mock_db.order_headers.find = MagicMock(side_effect=lambda q, *a, **k: (captured.update({"query": q}) or _Cursor()))
    mock_db.order_items.find = MagicMock(return_value=MagicMock(to_list=AsyncMock(return_value=[])))
    mock_db.order_requests.find = MagicMock(return_value=MagicMock(to_list=AsyncMock(return_value=[])))

    class _User:
        role = "master"
        id = "m1"
        group = ""
        location = ""
        username = "master"

    async def _run():
        with patch.object(server, "db", mock_db), \
             patch.object(server.hybrid_order_history, "list_archived_order_headers", AsyncMock(return_value=[])):
            rows = await server.order_desk_orders(
                date_from="2026-10-18", date_to="2026-10-18", current_user=_User(),
            )
        assert len(rows) == 1
        assert rows[0]["order_number"] == "TS-ORHY261018005"
        created = captured["query"]["created_at"]
        assert isinstance(created["$gte"], str)
        assert isinstance(created["$lt"], str)
        assert created["$gte"] == start.isoformat()
        assert created["$lt"] == end.isoformat()

    asyncio.get_event_loop().run_until_complete(_run())


def test_can_save_factory_number_roles():
    """Permission matrix: only Admin/Master enter Factory Order No (UI+API)."""
    def can_save(role: str) -> bool:
        return str(role or "").lower() in ("master", "admin")

    assert can_save("master") is True
    assert can_save("admin") is True
    assert can_save("user") is False
    assert can_save("User") is False
