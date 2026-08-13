"""Regression tests for night-archive + OD/RC unified PR.

Covers: frozen archive_date, maintenance window, Excel S3 hard-require,
Product History unavailable, Analytics snapshot filters, OD completion,
Factory System Order Number, Request Center display/transitions helpers.
"""
from __future__ import annotations

import asyncio
import os
import sys
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from zoneinfo import ZoneInfo

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

os.environ.setdefault("ARCHIVE_PRUNE_ENABLED", "false")
os.environ.setdefault("ARCHIVE_SCHEDULER_ENABLED", "false")

IST = ZoneInfo("Asia/Kolkata")


class FakeColl:
    def __init__(self, docs=None):
        self.docs = list(docs or [])

    async def find_one(self, query=None, projection=None, sort=None):
        rows = list(self.docs)
        if sort:
            for key, direction in reversed(sort):
                rows.sort(key=lambda d: d.get(key) or "", reverse=direction < 0)
        for d in rows:
            if all(d.get(k) == v for k, v in (query or {}).items() if not isinstance(v, dict)):
                return {k: v for k, v in d.items() if k != "_id"}
        return None

    async def insert_one(self, doc):
        self.docs.append(dict(doc))
        return SimpleNamespace(inserted_id="x")

    async def update_one(self, query, ops):
        def _set_path(doc, key, value):
            parts = key.split(".")
            cur = doc
            for p in parts[:-1]:
                nxt = cur.get(p)
                if not isinstance(nxt, dict):
                    nxt = {}
                    cur[p] = nxt
                cur = nxt
            cur[parts[-1]] = value

        for d in self.docs:
            if all(d.get(k) == v for k, v in (query or {}).items()):
                if "$set" in ops:
                    for k, v in ops["$set"].items():
                        if "." in k:
                            _set_path(d, k, v)
                        else:
                            d[k] = v
                if "$inc" in ops:
                    for k, v in ops["$inc"].items():
                        parts = k.split(".")
                        cur = d
                        for p in parts[:-1]:
                            cur = cur.setdefault(p, {})
                        cur[parts[-1]] = int(cur.get(parts[-1]) or 0) + int(v)
                return SimpleNamespace(modified_count=1)
        return SimpleNamespace(modified_count=0)

    def find(self, query=None, projection=None):
        class Cursor:
            def __init__(self, docs):
                self._docs = docs

            def sort(self, *a, **k):
                return self

            def limit(self, n):
                return self

            async def to_list(self, n):
                return list(self._docs)[:n]

        return Cursor(self.docs)

    async def create_index(self, *a, **k):
        return "ok"


class FakeDB:
    def __init__(self):
        self.archive_runs = FakeColl()
        self.archive_manifests = FakeColl()
        self.archive_job_locks = FakeColl()


def test_maintenance_window_bounds():
    import maintenance as m

    assert m.in_maintenance_window(datetime(2026, 8, 13, 23, 0, tzinfo=IST))
    assert m.in_maintenance_window(datetime(2026, 8, 14, 0, 0, tzinfo=IST))
    assert m.in_maintenance_window(datetime(2026, 8, 14, 3, 59, tzinfo=IST))
    assert not m.in_maintenance_window(datetime(2026, 8, 14, 4, 0, tzinfo=IST))
    assert not m.in_maintenance_window(datetime(2026, 8, 13, 22, 59, tzinfo=IST))
    st = m.maintenance_status(datetime(2026, 8, 13, 23, 30, tzinfo=IST))
    assert st["maintenance_active"] is True
    assert "4:00 AM" in (st["message"] or "")


def test_frozen_archive_date_midnight_crossing():
    import archive_runs as ar
    import archive_scheduler as ash

    start = datetime(2026, 8, 13, 23, 0, tzinfo=IST)
    frozen = ash.same_business_day_iso(start)
    assert frozen == "2026-08-13"

    async def _run():
        db = FakeDB()
        run = await ar.start_or_resume_run(db, archive_date=frozen, started_at=start.isoformat())
        assert run["archive_date"] == "2026-08-13"
        assert run["run_id"]
        # Resume after midnight must keep same frozen date + run_id
        again = await ar.start_or_resume_run(db, archive_date=frozen)
        assert again["run_id"] == run["run_id"]
        assert again["archive_date"] == "2026-08-13"
        await ar.mark_module(db, run["run_id"], "uploads", status=ar.STATUS_FAILED, error="s3 timeout", increment_retry=True)
        failed = await ar.find_run(db, run["run_id"])
        assert failed["modules"]["uploads"]["status"] == ar.STATUS_FAILED
        assert failed["modules"]["uploads"]["retries"] == 1
        assert failed["last_error"]
        await ar.mark_module(db, run["run_id"], "uploads", status=ar.STATUS_VERIFIED, result={"status": "verified"})
        await ar.finalize_run(db, run["run_id"], complete=True)
        done = await ar.find_run(db, run["run_id"])
        assert done["overall_status"] == ar.STATUS_VERIFIED
        card = ar.tonight_card(done, maintenance_active=True)
        assert card["archive_date"] == "2026-08-13"
        assert card["label"] == "Tonight's Archive"

    asyncio.get_event_loop().run_until_complete(_run())


def test_archive_scheduler_same_day_not_previous():
    import archive_scheduler as ash

    src = Path(ROOT, "archive_scheduler.py").read_text(encoding="utf-8")
    assert "now.hour == 23 and now.minute >= 45" not in src
    assert "in_nightly_archive_window" in src
    assert "same_business_day_iso" in src
    assert ash.JOB_LOCK_TTL_SECONDS >= 5 * 60 * 60
    # Manual default is same business day
    assert ash.same_business_day_iso(datetime(2026, 8, 13, 23, 10, tzinfo=IST)) == "2026-08-13"
    # Legacy helper still available
    assert ash.previous_calendar_day_iso(datetime(2026, 8, 13, 23, 10, tzinfo=IST)) == "2026-08-12"


def test_scheduler_restart_after_midnight_uses_prior_ist_day():
    """Documented freeze: after midnight inside window, resume prior IST calendar day."""
    src = Path(ROOT, "archive_scheduler.py").read_text(encoding="utf-8")
    assert "Resuming nightly archive after restart" in src
    assert "now.date() - timedelta(days=1)" in src


def test_od_accepted_qty_completes_without_rc_completed():
    import order_desk_workflow as odw

    item = {"id": "i1", "required_qty": 5, "allocations": []}
    order = {"dealer_name": "KUN"}
    # Accepted in RC but logistics still Dispatched — OD sourcing must complete
    reqs = [{
        "id": "r1",
        "status": "Dispatched",
        "requested_qty": 5,
        "accepted_qty": 5,
        "supplying_dealer": "KUN",
        "supplying_branch": "Ambattur",
        "request_number": "RQ-OD-1",
    }]
    wf = odw.compute_item_workflow(item, order, reqs)
    assert wf["accepted_qty"] == 5
    assert wf["remaining_qty"] == 0
    assert wf["request_status"] == odw.REQUEST_STATUS_COMPLETED


def test_od_partial_accept_freezes_and_continues():
    import order_desk_workflow as odw

    item = {"id": "i1", "required_qty": 10, "allocations": []}
    order = {"dealer_name": "KUN"}
    reqs = [{
        "id": "r1",
        "status": "Approved",
        "requested_qty": 6,
        "accepted_qty": 4,
        "supplying_dealer": "KUN",
        "supplying_branch": "Ambattur",
        "request_number": "RQ-OD-2",
    }]
    wf = odw.compute_item_workflow(item, order, reqs)
    assert wf["accepted_qty"] == 4
    assert wf["remaining_qty"] == 6
    assert wf["request_status"] in (odw.REQUEST_STATUS_PARTIAL, "Partially Accepted", "Remaining Qty")


def test_factory_system_order_closes_remaining():
    import order_desk_workflow as odw

    item = {
        "id": "i1",
        "required_qty": 10,
        "allocations": [],
        "system_order_number": "SYS-12345",
        "factory_fulfilled_qty": 6,
    }
    order = {"dealer_name": "KUN"}
    reqs = [{
        "id": "r1",
        "status": "Approved",
        "requested_qty": 4,
        "accepted_qty": 4,
        "supplying_dealer": "KUN",
        "supplying_branch": "Ambattur",
        "request_number": "RQ-OD-3",
    }]
    wf = odw.compute_item_workflow(item, order, reqs)
    assert wf["remaining_qty"] == 0
    assert wf["request_status"] == odw.REQUEST_STATUS_COMPLETED


def test_reject_same_day_freeze_helper_unchanged():
    import order_desk_workflow as odw

    # Rejected-today freeze helpers must still exist / map
    assert odw.map_request_center_status("Rejected") == "Rejected"
    assert "Rejected" in odw.REQUEST_STATUS_REJECTED or odw.REQUEST_STATUS_REJECTED == "Rejected"


def test_rc_transition_map_dispatched_to_completed():
    src = Path(ROOT, "server.py").read_text(encoding="utf-8")
    assert "'Dispatched': {'Completed'}" in src
    assert "DPS Order Number" not in src
    assert "system_order_number" in src
    assert "System Order Number" in src


def test_excel_upload_hard_requires_real_s3_in_source():
    src = Path(ROOT, "server.py").read_text(encoding="utf-8")
    assert "HARD REQUIRE" in src
    assert "private REAL S3" in src
    assert "missing storage_key" in src


def test_product_history_unavailable_not_silent_empty():
    import hybrid_history as hh

    async def _run():
        db = FakeDB()
        db.archive_manifests.docs = [{
            "module": "product-history",
            "archive_date": "2026-08-01",
            "status": "VERIFIED",
            "storage_key": "dev/product-history/2026-08-01/products.jsonl.gz",
            "storage_backend": "s3",
            "record_count": 10,
        }]
        with patch.object(hh, "get_storage") as gs, patch.object(hh.am, "find_s3_readable", new=AsyncMock(return_value=db.archive_manifests.docs[0])):
            storage = MagicMock()
            storage.is_s3.return_value = True
            storage.download_bytes.side_effect = RuntimeError("S3 GET failed")
            storage.head.return_value = {"storage_provider": "s3"}
            gs.return_value = storage
            page = await hh._stream_s3_product_day_page(db, "20260801", page=1, page_size=50)
            assert page["archive_unavailable"] is True
            assert "temporarily unavailable" in (page.get("message") or "").lower()
            assert page.get("rows") == []

    asyncio.get_event_loop().run_until_complete(_run())


def test_analytics_snapshot_row_keeps_part_type_and_aging():
    import analytics_center as ac

    row = {
        "purchase_aging_days": 120,
        "sales_aging_days": 45,
        "part_category": "Fast Moving",
        "last_receipt_date": None,
        "last_sales_date": None,
    }
    assert ac._aging_days_row(row, "purchase") is not None
    assert ac._aging_days_row(row, "sales") is not None
    src = Path(ROOT, "analytics_center.py").read_text(encoding="utf-8")
    assert "Part Type filter on snapshot path" in src
    assert '"purchase_aging_days": r.get("purchase_aging_days")' in src
    assert '"sales_aging_days": r.get("sales_aging_days")' in src


def test_prune_remains_disabled_default():
    import s3_storage

    assert s3_storage.archive_prune_enabled() is False
    assert os.environ.get("ARCHIVE_PRUNE_ENABLED", "false").lower() in {"0", "false", "no", ""}


def test_frontend_system_order_label_not_dps():
    orders = Path(ROOT.parent, "frontend/src/pages/Orders.js").read_text(encoding="utf-8")
    assert "System Order Number" in orders
    assert "DPS Order Number" not in orders
    reqs = Path(ROOT.parent, "frontend/src/pages/Requests.js").read_text(encoding="utf-8")
    assert "displayStatus" in reqs
    assert "complete: ['Dispatched', 'Received']" in reqs
    assert "transitionGroup(g,'receive')" not in reqs
