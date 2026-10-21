"""Stage B — Dealer Auto Suggest remainder becomes Factory Order."""
from __future__ import annotations

import asyncio
import os
import sys
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


class _User:
    id = "u1"
    username = "tester"
    role = "master"
    group = "Dealer A"
    email = "tester@example.com"


def test_dealer_auto_suggest_marks_factory_when_unfulfilled():
    import server
    import order_desk_workflow as odw

    order = {
        "id": "ord1",
        "order_number": "ORTEST1",
        "brand_name": "Brand",
        "dealer_name": "Dealer A",
        "branch": "Branch A",
        "created_by": "u1",
        "finished": False,
    }
    item = {
        "id": "item1",
        "order_id": "ord1",
        "part_number": "P1",
        "required_qty": 10,
        "remaining_qty": 10,
        "availability_checked_at": "2026-01-01T00:00:00+00:00",
        "own_stage_exhausted": True,
        "branch_stage_exhausted": True,
        "other_dealer_sources": [],
        "same_dealer_sources": [],
        "allocations": [],
    }

    mock_db = MagicMock()
    mock_db.order_headers.find_one = AsyncMock(return_value=order)
    mock_db.order_items.find = MagicMock(return_value=MagicMock(to_list=AsyncMock(return_value=[item])))
    mock_db.order_requests.find_one = AsyncMock(return_value=None)
    mock_db.order_requests.find = MagicMock(return_value=MagicMock(to_list=AsyncMock(return_value=[])))
    mock_db.order_items.update_one = AsyncMock()
    mock_db.order_items.find_one = AsyncMock(side_effect=[
        {**item, "factory_order_qty": 10, "no_further_stock": True, "request_status": odw.REQUEST_STATUS_FACTORY},
    ])
    mock_db.order_headers.update_one = AsyncMock()
    mock_db.order_activity.insert_one = AsyncMock()

    async def _run():
        with patch.object(server, "db", mock_db), \
             patch.object(server.odg, "assert_order_not_finished"), \
             patch.object(server.odw, "load_today_freezes", AsyncMock(return_value={})), \
             patch.object(server.odw, "eligible_pool", return_value=[]), \
             patch.object(server.odw, "enrich_order_items", AsyncMock(side_effect=lambda *a, **k: a[2])), \
             patch.object(server, "_reservation_qty_map", AsyncMock(return_value={})):
            result = await server.order_desk_auto_suggest("ord1", {"level": "dealer"}, _User())
        assert result["level"] == "dealer"
        assert mock_db.order_items.update_one.await_count == 1
        set_doc = mock_db.order_items.update_one.await_args.args[1]["$set"]
        assert set_doc["factory_order_qty"] == 10
        assert set_doc["no_further_stock"] is True
        assert set_doc["dealer_stage_exhausted"] is True
        assert set_doc["request_status"] == odw.REQUEST_STATUS_FACTORY

    asyncio.get_event_loop().run_until_complete(_run())
