"""Unit tests for Analytics daily rollup finalizer and window clamp."""
from __future__ import annotations

import asyncio
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import analytics_rollup as ar  # noqa: E402


IST = ZoneInfo("Asia/Kolkata")


class FakeCursor:
    def __init__(self, rows):
        self.rows = list(rows)

    def sort(self, key_or_list, direction=None):
        spec = [(key_or_list, direction)] if direction is not None else key_or_list
        for key, direction in reversed(list(spec or [])):
            self.rows.sort(key=lambda r, k=key: r.get(k) or "", reverse=int(direction or 1) < 0)
        return self

    async def to_list(self, n=None):
        return list(self.rows)


class FakeColl:
    def __init__(self, rows=None):
        self.rows = list(rows or [])

    def find(self, query=None, projection=None):
        return FakeCursor([r for r in self.rows if _match(r, query or {})])

    async def find_one(self, query=None, projection=None, sort=None):
        rows = [r for r in self.rows if _match(r, query or {})]
        if sort:
            for key, direction in reversed(sort):
                rows.sort(key=lambda r: r.get(key) or "", reverse=direction < 0)
        return rows[0] if rows else None

    async def update_one(self, query, update, upsert=False):
        for i, row in enumerate(self.rows):
            if _match(row, query):
                self.rows[i] = {**row, **(update.get("$set") or {})}
                return {"matched": 1}
        if upsert:
            self.rows.append({**(query or {}), **(update.get("$set") or {})})
        return {"matched": 0}

    async def delete_many(self, query):
        before = len(self.rows)
        self.rows = [r for r in self.rows if not _match(r, query or {})]
        return {"deleted": before - len(self.rows)}

    async def insert_many(self, docs, ordered=True):
        self.rows.extend(dict(d) for d in docs)
        return {"n": len(docs)}

    def aggregate(self, pipeline, **kwargs):
        # Force the Python newest-first dedupe path used as DocumentDB fallback.
        raise RuntimeError("force fallback dedupe")

    async def create_index(self, *a, **k):
        return "ok"

    async def estimated_document_count(self):
        return len(self.rows)


class FakeDB(dict):
    def __getitem__(self, name):
        if name not in self:
            self[name] = FakeColl()
        return dict.__getitem__(self, name)

    def __getattr__(self, name):
        return self[name]


def _match(doc, query):
    if not query:
        return True
    if "$and" in query:
        return all(_match(doc, q) for q in query["$and"])
    for k, v in query.items():
        if isinstance(v, dict):
            if "$in" in v and doc.get(k) not in v["$in"]:
                return False
            if "$lte" in v and str(doc.get(k) or "") > str(v["$lte"]):
                return False
            if "$gte" in v and str(doc.get(k) or "") < str(v["$gte"]):
                return False
        elif doc.get(k) != v:
            return False
    return True


def test_resolve_analytics_end_date_caps_to_yesterday_and_last_success():
    now = datetime(2026, 10, 17, 14, 0, tzinfo=IST)
    effective, updated = ar.resolve_analytics_end_date(
        "2026-10-17", last_success="2026-10-16", now=now
    )
    assert effective == "2026-10-16"
    assert updated == "2026-10-16"
    effective2, updated2 = ar.resolve_analytics_end_date(
        "2026-10-15", last_success="2026-10-16", now=now
    )
    assert effective2 == "2026-10-15"
    assert updated2 == "2026-10-16"


def test_finalize_rollup_idempotent_and_reconciled():
    db = FakeDB()
    db.products = FakeColl([
        {
            "publish_status": "Published",
            "active_date_key": "20261016",
            "brand_name": "Hyundai",
            "dealer_name": "FPL",
            "branch": "Vanagaram",
            "part_number": "P1",
            "quantity": 2,
            "mav": 100,
            "total_value": 200,
            "published_at": "2026-10-16T10:00:00Z",
            "updated_at": "2026-10-16T10:00:00Z",
        },
        {
            "publish_status": "Published",
            "active_date_key": "20261016",
            "brand_name": "Hyundai",
            "dealer_name": "FPL",
            "branch": "Vanagaram",
            "part_number": "P1",
            "quantity": 5,
            "mav": 100,
            "total_value": 500,
            "published_at": "2026-10-16T18:00:00Z",
            "updated_at": "2026-10-16T18:00:00Z",
        },
    ])

    first = asyncio.run(ar.finalize_analytics_rollup_for_date(db, "2026-10-16"))
    assert first["status"] == ar.STATUS_SUCCESS
    assert first["reconciled"] is True
    assert first.get("snapshot_record_count") == 1
    assert first.get("daily_agg_record_count") == 1
    assert abs(first["snapshot_total_value"] - 500) < 1e-6
    assert abs(first["daily_agg_total_value"] - 500) < 1e-6
    assert len(db.analytics_stock_daily_snapshots.rows) == 1
    assert len(db.analytics_daily_aggs.rows) == 1
    assert db.analytics_daily_aggs.rows[0]["stock_value"] == 500.0
    assert db.analytics_daily_aggs.rows[0]["line_count"] == 1

    second = asyncio.run(ar.finalize_analytics_rollup_for_date(db, "2026-10-16"))
    assert second.get("skipped") is True
    assert second["status"] == ar.STATUS_SUCCESS
    assert len(db.analytics_stock_daily_snapshots.rows) == 1
    assert len(db.analytics_daily_aggs.rows) == 1


def test_build_snapshot_docs_preserve_zero():
    docs = ar.build_snapshot_docs("2026-10-16", [{
        "brand_name": "Hyundai",
        "dealer_name": "FPL",
        "branch": "X",
        "part_number": "Z",
        "quantity": 0,
        "mav": 10,
        "total_value": 0,
    }])
    assert docs[0]["available_qty"] == 0.0
    assert docs[0]["total_value"] == 0.0


def test_build_daily_agg_docs_buckets_and_totals():
    snaps = ar.build_snapshot_docs("2026-10-16", [
        {
            "brand_name": "Hyundai",
            "dealer_name": "FPL",
            "branch": "Vanagaram",
            "part_number": "A",
            "part_category": "Fast Moving",
            "quantity": 2,
            "mav": 50,
            "total_value": 100,
            "purchase_aging_days": 10,
            "sales_aging_days": 400,
        },
        {
            "brand_name": "Hyundai",
            "dealer_name": "FPL",
            "branch": "Vanagaram",
            "part_number": "B",
            "part_category": "Fast Moving",
            "quantity": 1,
            "mav": 20,
            "total_value": 20,
            "purchase_aging_days": None,
            "sales_aging_days": 50,
        },
    ])
    aggs = ar.build_daily_agg_docs("2026-10-16", snaps)
    assert len(aggs) == 1
    g = aggs[0]
    assert abs(g["stock_value"] - 120) < 1e-6
    assert g["line_count"] == 2
    assert abs(g["aging_purchase"]["0–90 Days"]["value"] - 100) < 1e-6
    assert abs(g["aging_purchase"][">361 Days"]["value"] - 20) < 1e-6
    assert abs(g["aging_sales"][">361 Days"]["value"] - 100) < 1e-6


def test_query_daily_aggs_scope_filter():
    db = FakeDB()
    db[ar.DAILY_AGG_COLLECTION] = FakeColl([
        {
            "rollup_date_ist": "2026-10-16",
            "brand_name": "Hyundai",
            "dealer_name": "FPL",
            "branch_name": "Vanagaram",
            "category": "Fast Moving",
            "stock_qty": 1,
            "stock_value": 10,
        },
        {
            "rollup_date_ist": "2026-10-16",
            "brand_name": "Kia",
            "dealer_name": "Other",
            "branch_name": "X",
            "category": "Others",
            "stock_qty": 9,
            "stock_value": 90,
        },
    ])
    rows = asyncio.run(ar.query_daily_aggs(db, ["20261016"], {"brand": "Hyundai"}, None))
    assert len(rows) == 1
    assert rows[0]["stock_value"] == 10
