"""Unit tests for Analytics daily snapshots v2 (fixtures only — no DB, no network)."""
from __future__ import annotations

import asyncio
import sys
from datetime import date, datetime
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import analytics_center as ac  # noqa: E402
import analytics_rollup as ar  # noqa: E402
import analytics_rollup_scheduler as ars  # noqa: E402

IST = ZoneInfo("Asia/Kolkata")
B = ar.AGING_BUCKETS


# ---------------- in-memory fake ----------------

def _cmp(a, b):
    return (str(a) if a is not None else "") , (str(b) if b is not None else "")


def _match(doc, query):
    for k, v in (query or {}).items():
        if k == "$and":
            if not all(_match(doc, q) for q in v):
                return False
            continue
        if k == "$or":
            if not any(_match(doc, q) for q in v):
                return False
            continue
        val = doc.get(k)
        if isinstance(v, dict):
            for op, arg in v.items():
                if op == "$in" and val not in arg:
                    return False
                if op == "$ne" and val == arg:
                    return False
                if op in ("$gte", "$lte", "$lt", "$gt"):
                    if isinstance(arg, (int, float)) and not isinstance(arg, bool):
                        num = float(val or 0)
                        ok = {"$gte": num >= arg, "$lte": num <= arg, "$lt": num < arg, "$gt": num > arg}[op]
                    else:
                        x, y = _cmp(val, arg)
                        ok = {"$gte": x >= y, "$lte": x <= y, "$lt": x < y, "$gt": x > y}[op]
                    if not ok:
                        return False
        elif val != v:
            return False
    return True


class Cursor:
    def __init__(self, rows):
        self.rows = [dict(r) for r in rows]

    def sort(self, key_or_list, direction=None):
        spec = [(key_or_list, direction)] if direction is not None else list(key_or_list)
        for key, d in reversed(spec):
            self.rows.sort(key=lambda r, k=key: str(r.get(k) or ""), reverse=int(d) < 0)
        return self

    def batch_size(self, _n):
        return self

    async def to_list(self, _n=None):
        return list(self.rows)

    def __aiter__(self):
        async def gen():
            for r in self.rows:
                yield r
        return gen()


class Coll:
    def __init__(self, rows=None):
        self.rows = [dict(r) for r in (rows or [])]

    def find(self, query=None, projection=None):
        return Cursor([r for r in self.rows if _match(r, query)])

    async def find_one(self, query=None, projection=None, sort=None):
        cur = self.find(query)
        if sort:
            cur.sort(sort)
        return cur.rows[0] if cur.rows else None

    async def count_documents(self, query):
        return len([r for r in self.rows if _match(r, query)])

    async def update_one(self, query, update, upsert=False):
        for i, r in enumerate(self.rows):
            if _match(r, query):
                self.rows[i] = {**r, **update.get("$set", {})}
                return SimpleNamespace(matched_count=1)
        if upsert:
            self.rows.append({**query, **update.get("$set", {})})
        return SimpleNamespace(matched_count=0)

    async def delete_many(self, query):
        before = len(self.rows)
        self.rows = [r for r in self.rows if not _match(r, query)]
        return SimpleNamespace(deleted_count=before - len(self.rows))

    async def insert_many(self, docs, ordered=True):
        self.rows.extend(dict(d) for d in docs)

    def aggregate(self, pipeline, **kwargs):
        # Supports the rollup branch listing: [$match, $group{_id:{..:"$f"}}].
        match, group = pipeline[0]["$match"], pipeline[1]["$group"]
        out = {}
        for r in self.rows:
            if not _match(r, match):
                continue
            ident = {k: r.get(v[1:]) for k, v in group["_id"].items()}
            key = tuple(sorted((k, str(v)) for k, v in ident.items()))
            out.setdefault(key, {"_id": ident, "n": 0})["n"] += 1
        return Cursor(list(out.values()))


class DB(dict):
    def __getitem__(self, name):
        if name not in self:
            self[name] = Coll()
        return dict.__getitem__(self, name)

    def __getattr__(self, name):
        return self[name]


async def _nosleep(_s):
    return None


def _product(part, qty, mav, *, branch="B1", receipt="", sales="", published="2026-10-10T10:00:00Z", **extra):
    return {
        "publish_status": "Published", "active_date_key": "20261010",
        "brand_name": "Hyundai", "dealer_name": "FPL", "branch": branch,
        "part_number": part, "quantity": qty, "mav": mav, "total_value": qty * mav,
        "last_receipt_date": receipt, "last_sales_date": sales,
        "published_at": published, "updated_at": published, **extra,
    }


def _run(coro):
    return asyncio.run(coro)


# ---------------- aging ----------------

@pytest.mark.parametrize(
    "days,bucket",
    [(0, B[0]), (90, B[0]), (91, B[1]), (180, B[1]), (181, B[2]), (270, B[2]),
     (271, B[3]), (361, B[3]), (362, B[4])],
)
def test_bucket_boundaries_from_source_dates(days, bucket):
    snap = date(2026, 10, 10)
    receipt = (snap.toordinal() - days)
    receipt_text = date.fromordinal(receipt).strftime("%d-%m-%Y")
    got = ar.aging_days_as_of({"last_receipt_date": receipt_text}, "purchase", snap)
    assert got == days
    assert ar._analytics_bucket(got) == bucket


def test_aging_is_measured_from_snapshot_date_not_today():
    row = {"last_receipt_date": "12-07-2026", "last_sales_date": "2026-10-01"}
    assert ar.aging_days_as_of(row, "purchase", date(2026, 10, 10)) == 90
    assert ar.aging_days_as_of(row, "sales", date(2026, 10, 10)) == 9
    assert ar.aging_days_as_of(row, "purchase", date(2026, 10, 11)) == 91


def test_stored_days_both_spellings_and_date_wins():
    snap = date(2026, 10, 10)
    assert ar.aging_days_as_of({"purchase_ageing_days": 125}, "purchase", snap) == 125
    assert ar.aging_days_as_of({"sales_ageing_days": "40 Days"}, "sales", snap) == 40
    assert ar.aging_days_as_of({"purchase_aging_days": 7}, "purchase", snap) == 7
    # A real source date beats a stored (upload-time) number.
    assert ar.aging_days_as_of({"last_receipt_date": "10-10-2026", "purchase_ageing_days": 999}, "purchase", snap) == 0


def test_missing_aging_goes_to_above_361_and_is_counted():
    g = ar.new_agg_group("2026-10-10", "H", "F", "B1", "Others", now="x")
    ar.add_part_to_group(g, 2.0, 100.0, None, 10)
    ar.add_part_to_group(g, 1.0, 50.0, 20, None)
    assert g["aging_purchase"][B[4]]["value"] == 100.0
    assert g["missing_aging_purchase"] == {"qty": 2.0, "value": 100.0, "items": 1.0}
    assert g["missing_aging_sales"] == {"qty": 1.0, "value": 50.0, "items": 1.0}
    for basis in ("purchase", "sales"):
        assert sum(s["value"] for s in g[f"aging_{basis}"].values()) == g["stock_value"] == 150.0


# ---------------- finalize ----------------

def _seed_products():
    return Coll([
        _product("P1", 2, 100, receipt="01-10-2026", sales="01-01-2025", published="2026-10-10T09:00:00Z"),
        _product("P1", 5, 100, receipt="01-10-2026", sales="01-01-2025", published="2026-10-10T18:00:00Z"),
        _product("P2", 3, 10, receipt="", sales="", part_category="Accessories"),
        _product("Q1", 1, 1000, branch="B2", receipt="01-05-2026", sales="15-09-2026"),
    ])


def test_finalize_v2_buckets_sum_to_closing_and_counts_missing():
    db = DB()
    db.products = _seed_products()
    res = _run(ar.finalize_analytics_rollup_for_date(db, "2026-10-10", sleep=_nosleep))
    assert res["status"] == ar.STATUS_SUCCESS and res["reconciled"] is True
    assert res["rollup_version"] == ar.ROLLUP_VERSION
    assert res["branch_count"] == 2
    assert res["snapshot_record_count"] == 3  # P1 deduped to newest
    assert abs(res["daily_agg_total_value"] - (500 + 30 + 1000)) < 1e-6
    aggs = db[ar.DAILY_AGG_COLLECTION].rows
    for g in aggs:
        for basis in ("purchase", "sales"):
            assert abs(sum(s["value"] for s in g[f"aging_{basis}"].values()) - g["stock_value"]) < 1e-6
    acc = next(g for g in aggs if g["category"] == "Accessories")
    assert acc["missing_aging_purchase"]["items"] == 1.0
    b1_oe = next(g for g in aggs if g["branch_name"] == "B1" and g["category"] != "Accessories")
    assert b1_oe["aging_purchase"][B[0]]["value"] == 500.0  # 9 days old on 2026-10-10
    assert b1_oe["aging_sales"][B[4]]["value"] == 500.0
    snaps = db[ar.SNAPSHOT_COLLECTION].rows
    assert all(s["build_id"] == res["build_id"] for s in snaps)
    assert next(s for s in snaps if s["part_number"] == "P1")["purchase_aging_days"] == 9


def test_finalize_idempotent_and_rebuild_removes_stale_rows():
    db = DB()
    db.products = _seed_products()
    # Legacy v1 state: success without version, a branch that no longer exists.
    db[ar.STATUS_COLLECTION] = Coll([{"rollup_date_ist": "2026-10-10", "status": "success"}])
    db[ar.DAILY_AGG_COLLECTION] = Coll([
        {"rollup_date_ist": "2026-10-10", "brand_name": "Hyundai", "dealer_name": "FPL",
         "branch_name": "GONE", "category": "Others", "stock_value": 99.0},
    ])
    db[ar.SNAPSHOT_COLLECTION] = Coll([
        {"snapshot_date_ist": "2026-10-10", "brand_name": "Hyundai", "dealer_name": "FPL",
         "branch_name": "GONE", "part_number": "X", "available_qty": 1, "total_value": 99.0},
    ])
    first = _run(ar.finalize_analytics_rollup_for_date(db, "2026-10-10", sleep=_nosleep))
    assert first["status"] == ar.STATUS_SUCCESS  # v1 success is rebuilt without force
    assert not any(r.get("branch_name") == "GONE" for r in db[ar.DAILY_AGG_COLLECTION].rows)
    assert not any(r.get("branch_name") == "GONE" for r in db[ar.SNAPSHOT_COLLECTION].rows)
    n_aggs, n_snaps = len(db[ar.DAILY_AGG_COLLECTION].rows), len(db[ar.SNAPSHOT_COLLECTION].rows)

    second = _run(ar.finalize_analytics_rollup_for_date(db, "2026-10-10", sleep=_nosleep))
    assert second.get("skipped") is True

    forced = _run(ar.finalize_analytics_rollup_for_date(db, "2026-10-10", force=True, sleep=_nosleep))
    assert forced["status"] == ar.STATUS_SUCCESS and forced["build_id"] != first["build_id"]
    assert len(db[ar.DAILY_AGG_COLLECTION].rows) == n_aggs
    assert len(db[ar.SNAPSHOT_COLLECTION].rows) == n_snaps
    assert {r["build_id"] for r in db[ar.SNAPSHOT_COLLECTION].rows} == {forced["build_id"]}


def test_no_source_never_writes_zero_and_keeps_existing_success():
    db = DB()
    db[ar.STATUS_COLLECTION] = Coll([{"rollup_date_ist": "2026-10-09", "status": "success"}])
    kept = _run(ar.finalize_analytics_rollup_for_date(db, "2026-10-09", sleep=_nosleep))
    assert kept["status"] == ar.STATUS_NO_SOURCE and kept.get("kept_existing") is True
    assert db[ar.STATUS_COLLECTION].rows[0]["status"] == "success"
    fresh = _run(ar.finalize_analytics_rollup_for_date(db, "2026-10-08", sleep=_nosleep))
    assert fresh["status"] == ar.STATUS_NO_SOURCE
    assert not db[ar.DAILY_AGG_COLLECTION].rows


# ---------------- screen series ----------------

def _agg(iso, value, qty, *, branch="B1", buckets=None):
    buckets = buckets or {B[0]: (value, qty)}
    aging = {b: {"qty": 0.0, "value": 0.0, "items": 0.0} for b in B}
    for b, (v, q) in buckets.items():
        aging[b] = {"qty": float(q), "value": float(v), "items": 1.0}
    return {
        "rollup_date_ist": iso, "brand_name": "Hyundai", "dealer_name": "FPL", "branch_name": branch,
        "category": "Others", "stock_qty": float(qty), "stock_value": float(value), "line_count": 3,
        "aging_purchase": aging, "aging_sales": aging,
        "missing_aging_purchase": {"qty": 0.0, "value": 0.0, "items": 0.0},
        "missing_aging_sales": {"qty": 0.0, "value": 0.0, "items": 0.0},
        "rollup_version": ar.ROLLUP_VERSION,
    }


def _ok(iso, version=ar.ROLLUP_VERSION):
    return {"rollup_date_ist": iso, "status": "success", "rollup_version": version}


SCOPE = {"brand": "Hyundai", "dealer": "FPL", "branch": "B1"}
USER = SimpleNamespace(role="master", location="", brand="", group="")


@pytest.fixture
def screen_db(monkeypatch):
    db = DB()
    db[ar.DAILY_AGG_COLLECTION] = Coll([
        _agg("2026-09-30", 1000, 10),
        _agg("2026-10-01", 1200, 12),
        _agg("2026-10-03", 900, 9),
    ])
    db[ar.STATUS_COLLECTION] = Coll([_ok("2026-09-30"), _ok("2026-10-01"), _ok("2026-10-03")])
    monkeypatch.setattr(ac, "db", db)
    return db


def test_trend_opening_previous_snapshot_across_month_and_missing_day_null(screen_db):
    keys = ["20261001", "20261002", "20261003"]
    out = _run(ac._stock_trend_payload(SCOPE, keys, "purchase", "value", None, USER))
    s = {p["date"]: p for p in out["series"]}
    assert s["2026-10-01"]["opening"] == 1000.0 and s["2026-10-01"]["closing"] == 1200.0
    assert s["2026-10-01"]["net_change"] == 200.0
    assert s["2026-10-02"]["closing"] is None and s["2026-10-02"]["data_status"] == "NO_UPLOAD"
    assert s["2026-10-03"]["opening"] == 1200.0 and s["2026-10-03"]["net_change"] == -300.0


def test_trend_first_ever_date_opening_is_na(screen_db):
    out = _run(ac._stock_trend_payload(SCOPE, ["20260930"], "purchase", "value", None, USER))
    assert out["series"][0]["closing"] == 1000.0
    assert out["series"][0]["opening"] is None


def test_other_branch_day_is_no_snapshot_not_zero(screen_db):
    screen_db[ar.DAILY_AGG_COLLECTION].rows.append(_agg("2026-10-02", 50, 1, branch="B2"))
    screen_db[ar.STATUS_COLLECTION].rows.append(_ok("2026-10-02"))
    out = _run(ac._stock_trend_payload(SCOPE, ["20261002"], "purchase", "value", None, USER))
    assert out["series"][0]["closing"] is None


def test_v1_days_are_not_current(screen_db):
    screen_db[ar.STATUS_COLLECTION].rows.append(_ok("2026-10-02", version=1))
    screen_db[ar.DAILY_AGG_COLLECTION].rows.append(_agg("2026-10-02", 77, 1))
    keys = ["20261001", "20261002"]
    out = _run(ac._stock_trend_payload(SCOPE, keys, "purchase", "value", None, USER))
    assert out["series"][1]["closing"] is None
    st = _run(ac._snapshot_status(keys, "2026-10-03"))
    assert st["snapshot_status"] == "rebuild_needed" and st["rebuild_needed_dates"] == ["2026-10-02"]


def test_snapshot_status_unavailable_vs_rebuild_needed(monkeypatch):
    db = DB()
    monkeypatch.setattr(ac, "db", db)
    assert _run(ac._snapshot_status([], None))["snapshot_status"] == "unavailable"
    db[ar.STATUS_COLLECTION] = Coll([_ok("2026-10-01", version=1)])
    assert _run(ac._snapshot_status([], None))["snapshot_status"] == "rebuild_needed"
    db[ar.STATUS_COLLECTION].rows.append(_ok("2026-10-02"))
    assert _run(ac._snapshot_status(["20261002"], "2026-10-02"))["snapshot_status"] == "ok"


def test_aging_item_wise_is_qty_and_total_equals_closing(screen_db):
    screen_db[ar.DAILY_AGG_COLLECTION].rows[1] = _agg(
        "2026-10-01", 1200, 12, buckets={B[0]: (700, 7), B[4]: (500, 5)}
    )
    for metric in ("value", "quantity"):
        trend = _run(ac._stock_trend_payload(SCOPE, ["20261001"], "purchase", metric, None, USER))
        aging = _run(ac.analytics_aging_trend(
            from_date="2026-10-01", to_date="2026-10-01", brand="Hyundai", dealer="FPL", branch="B1",
            metric_type=metric, current_user=USER,
        ))
        day = aging["daily"][0]
        assert day["total"] == trend["series"][0]["closing"] == (1200.0 if metric == "value" else 12.0)
        assert sum(day[b] for b in B) == day["total"]
        assert aging["snapshot_status"] == "ok"


# ---------------- window / scheduler / CLI guards ----------------

def test_today_is_excluded_from_window():
    now = datetime(2026, 10, 19, 23, 30, tzinfo=IST)
    effective, updated = ar.resolve_analytics_end_date("2026-10-19", last_success="2026-10-19", now=now)
    assert effective == "2026-10-18" and updated == "2026-10-19"


def test_scheduler_due_dates():
    assert ars.due_date(datetime(2026, 10, 19, 23, 0, tzinfo=IST)) == "2026-10-19"
    assert ars.due_date(datetime(2026, 10, 20, 1, 0, tzinfo=IST)) == "2026-10-19"
    assert ars.due_date(datetime(2026, 10, 19, 10, 0, tzinfo=IST)) is None


def test_scheduler_skips_finalized_date():
    db = DB()
    db[ar.STATUS_COLLECTION] = Coll([_ok("2026-10-19")])
    state = ars._State()
    res = _run(ars.run_once(db, state, now=datetime(2026, 10, 19, 23, 5, tzinfo=IST)))
    assert res["status"] == "skipped" and "2026-10-19" in state.done
    assert _run(ars.run_once(db, state, now=datetime(2026, 10, 19, 23, 6, tzinfo=IST))) is None


def test_scheduler_flag_default_off(monkeypatch):
    monkeypatch.delenv(ars.ENV_FLAG, raising=False)
    assert ars.scheduler_enabled() is False
    monkeypatch.setenv(ars.ENV_FLAG, "true")
    assert ars.scheduler_enabled() is True


def test_cli_refuses_outside_testing(monkeypatch):
    import analytics_snapshot as cli

    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("DB_NAME", "nmts_testing")
    with pytest.raises(SystemExit):
        cli._refuse_if_not_testing()
    monkeypatch.setenv("APP_ENV", "testing")
    monkeypatch.setenv("DB_NAME", "nmts")
    with pytest.raises(SystemExit):
        cli._refuse_if_not_testing()
    monkeypatch.setenv("DB_NAME", "nmts_testing")
    cli._refuse_if_not_testing()
    with pytest.raises(SystemExit):
        cli._past_only(cli._today_ist())
