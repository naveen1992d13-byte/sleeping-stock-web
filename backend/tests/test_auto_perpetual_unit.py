"""Unit tests for Auto Perpetual helpers (no MongoDB required)."""
import asyncio
import re
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest
from fastapi import HTTPException

from auto_perpetual import (
    TODAY_STOCK_MISSING_MESSAGE,
    _loc_group,
    _working_days_left_in_month,
    branch_code,
    inventory_date_key,
    month_key,
    require_todays_branch_stock,
    resolve_pending_auto_assignment_date,
)


def test_loc_group():
    assert _loc_group("11A01234") == "11A0"
    assert _loc_group("") == "UNKNOWN"


def test_working_days_left():
    ist = ZoneInfo("Asia/Kolkata")
    d = datetime(2026, 8, 7, tzinfo=ist)
    assert _working_days_left_in_month(d) == 25


def test_month_key():
    assert month_key(datetime(2026, 3, 15, tzinfo=ZoneInfo("Asia/Kolkata"))) == "2026-03"


def test_inventory_date_key():
    assert inventory_date_key(datetime(2026, 8, 7, tzinfo=ZoneInfo("Asia/Kolkata"))) == "20260807"


def test_split_mix_20():
    from auto_perpetual_suggestions import _split_mix

    loc, inc, dec = _split_mix(20)
    assert loc + inc + dec == 20
    assert loc == 10 and inc == 5 and dec == 5


def test_select_dedupes():
    from auto_perpetual_suggestions import select_items_502525

    eligible = [
        {"part_number": "A", "system_qty": 1, "loc": "A-R01-B01", "movement": "location"},
        {"part_number": "B", "system_qty": 2, "loc": "A-R01-B02", "movement": "qty_increased"},
        {"part_number": "C", "system_qty": 3, "loc": "A-R01-B03", "movement": "qty_decreased"},
    ]
    picked = select_items_502525(eligible, 3, set())
    parts = [p["part_number"] for p in picked]
    assert len(parts) == len(set(parts))


def test_select_sorts_mixed_digit_and_letter_locations():
    from auto_perpetual_suggestions import select_items_502525

    eligible = [
        {"part_number": "A", "system_qty": 1, "loc": "12-A", "movement": "location"},
        {"part_number": "B", "system_qty": 1, "loc": "A-12", "movement": "location"},
        {"part_number": "C", "system_qty": 1, "loc": "B-01", "movement": "location"},
    ]
    picked = select_items_502525(eligible, 3, set())
    assert {p["part_number"] for p in picked} == {"A", "B", "C"}


class FakeCursor:
    def __init__(self, rows):
        self.rows = list(rows)

    def sort(self, key, direction=1):
        reverse = int(direction or 1) < 0
        self.rows.sort(key=lambda r: r.get(key) or "", reverse=reverse)
        return self

    async def to_list(self, n):
        return self.rows[: int(n)] if n is not None else list(self.rows)


class FakeCollection:
    def __init__(self, rows=None):
        self.rows = list(rows or [])

    def find(self, query=None, projection=None):
        return FakeCursor([r for r in self.rows if _match(r, query or {})])

    async def find_one(self, query=None, projection=None, **kwargs):
        for row in self.rows:
            if _match(row, query or {}):
                return dict(row)
        return None

    async def count_documents(self, query=None):
        return sum(1 for row in self.rows if _match(row, query or {}))


class FakeDB:
    def __init__(self, products=None, assignments=None, suggestions=None):
        self.products = FakeCollection(products)
        self.auto_perpetual_assignments = FakeCollection(assignments)
        self.auto_perpetual_suggestions = FakeCollection(suggestions)


def _match(row, query):
    for key, value in (query or {}).items():
        if key == "$or":
            if not any(_match(row, clause) for clause in value):
                return False
            continue
        actual = row.get(key)
        if isinstance(value, dict):
            if "$in" in value and actual not in value["$in"]:
                return False
            if "$regex" in value:
                flags = re.I if "i" in str(value.get("$options") or "") else 0
                if not re.match(value["$regex"], str(actual or ""), flags):
                    return False
            continue
        if actual != value:
            return False
    return True


def test_generate_requires_todays_stock_for_exact_scope():
    today = inventory_date_key()
    db = FakeDB(products=[
        {
            "brand_name": "Hyundai",
            "dealer_name": "FPL automobiles pvt ltd",
            "branch": "Retteri",
            "publish_status": "Published",
            "active_date_key": today,
        }
    ])

    async def run():
        with pytest.raises(HTTPException) as exc:
            await require_todays_branch_stock(
                db, brand_name="Hyundai", dealer_name="FPL automobiles pvt ltd", branch="Vanagaram"
            )
        assert exc.value.status_code == 400
        assert exc.value.detail == TODAY_STOCK_MISSING_MESSAGE
        key = await require_todays_branch_stock(
            db, brand_name="Hyundai", dealer_name="FPL automobiles pvt ltd", branch="Retteri"
        )
        assert key == today

    asyncio.get_event_loop().run_until_complete(run())


def test_pending_assignment_date_returns_open_sent_work_not_today():
    db = FakeDB(assignments=[
        {
            "mobile_user_id": "MUVAN2609190001",
            "brand_name": "Hyundai",
            "dealer_name": "FPL automobiles pvt ltd",
            "branch": "Vanagaram",
            "allocation_date": "2026-10-12",
            "status": "pending",
            "suggestion_id": "b35816e8-435a-4d83-bf77-d144c891b9fb",
        }
    ])

    async def run():
        date_key = await resolve_pending_auto_assignment_date(
            db,
            mobile_user_id="MUVAN2609190001",
            brand_name="Hyundai",
            dealer_name="FPL automobiles pvt ltd",
            branch="Vanagaram",
        )
        assert date_key == "2026-10-12"

    asyncio.get_event_loop().run_until_complete(run())


def test_suggestion_view_fills_items_from_assignments_and_sorts_mixed_locations():
    from auto_perpetual_suggestions import suggestion_with_line_items

    db = FakeDB(
        suggestions=[{
            "id": "sug-1",
            "suggestion_number": "TS-APS2610120001",
            "status": "SENT",
            "items": [],
            "total_items": 2,
        }],
        assignments=[
            {"suggestion_id": "sug-1", "part_number": "B", "loc": 12, "status": "pending"},
            {"suggestion_id": "sug-1", "part_number": "A", "loc": "A-01", "status": "pending"},
        ],
    )

    async def run():
        row = await suggestion_with_line_items(db, "sug-1")
        assert [it["part_number"] for it in row["items"]] == ["B", "A"]
        assert row["items"][0]["system_location"] == 12
        assert row["items"][1]["system_location"] == "A-01"

    asyncio.get_event_loop().run_until_complete(run())


def test_natural_sort_accepts_numeric_locations():
    from auto_perpetual_suggestions import _natural_sort_key

    assert isinstance(_natural_sort_key(12), tuple)
    assert isinstance(_natural_sort_key("A-01"), tuple)
