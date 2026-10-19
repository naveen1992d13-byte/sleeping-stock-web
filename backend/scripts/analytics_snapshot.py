#!/usr/bin/env python3
"""Testing-only Analytics daily snapshot tool (bounded memory, throttled).

  list-dates   [--from YYYY-MM-DD] [--to YYYY-MM-DD]
  backfill     --from YYYY-MM-DD --to YYYY-MM-DD [--force] [--s3-fallback]
  finalize-day --date YYYY-MM-DD [--force] [--s3-fallback]
  reconcile    --dates D1,D2,D3
  status       [--date YYYY-MM-DD]

Reads nmts_testing through the same overlay handle as the Testing API
(Production base is read-only). Writes only analytics_daily_aggs,
analytics_stock_daily_snapshots and analytics_rollup_status (Testing-local).
Works one branch at a time (0.25 s pause) and one date at a time (2 s pause).
Today (IST) is never finalized. Refuses unless APP_ENV=testing and
DB_NAME=nmts_testing. Never prints .env values.
"""
from __future__ import annotations

import argparse
import asyncio
import gzip
import json
import os
import sys
from collections import defaultdict
from datetime import date, datetime, timedelta
from io import BytesIO
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))
IST = ZoneInfo("Asia/Kolkata")
MAX_ARCHIVE_COMPRESSED_BYTES = 120 * 1024 * 1024


def _say(msg: str) -> None:
    print(msg, flush=True)


def _refuse_if_not_testing() -> None:
    if os.getenv("APP_ENV", "").strip().lower() != "testing":
        raise SystemExit("Refusing: APP_ENV must be testing")
    if os.getenv("DB_NAME", "").strip() != "nmts_testing":
        raise SystemExit("Refusing: DB_NAME must be nmts_testing")


def _iso(value: str) -> str:
    try:
        return datetime.strptime(value, "%Y-%m-%d").date().isoformat()
    except ValueError:
        raise SystemExit(f"Invalid date {value!r} (use YYYY-MM-DD)")


def _today_ist() -> str:
    return datetime.now(IST).date().isoformat()


def _past_only(iso: str) -> str:
    if iso >= _today_ist():
        raise SystemExit(f"Refusing {iso}: today/future IST dates are never finalized")
    return iso


def _max_rss_mb() -> Optional[float]:
    try:
        import resource

        return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024.0  # Linux: KiB
    except Exception:
        return None


def _connect():
    from dotenv import load_dotenv
    from motor.motor_asyncio import AsyncIOMotorClient

    load_dotenv(os.getenv("NMTS_ENV_FILE") or "/opt/nmts-testing/backend/.env", override=True)
    _refuse_if_not_testing()

    import testing_runtime
    from mongo_connection import build_mongo_client_args, resolve_mongo_url

    testing_runtime.assert_env_isolation()
    url, kwargs = build_mongo_client_args(resolve_mongo_url())
    client = AsyncIOMotorClient(url, **kwargs)
    raw = client["nmts_testing"]
    if testing_runtime.overlay_mode_enabled():
        import testing_overlay

        return testing_overlay.wrap_testing_database(client, raw, url, kwargs), "overlay"
    return raw, "raw"


async def _upload_dates(db, from_iso: Optional[str], to_iso: Optional[str]) -> List[dict]:
    match: Dict[str, Any] = {}
    rng: Dict[str, str] = {}
    if from_iso:
        rng["$gte"] = from_iso.replace("-", "")
    if to_iso:
        rng["$lte"] = to_iso.replace("-", "")
    if rng:
        match["active_date_key"] = rng
    pipeline = [
        {"$match": match or {"active_date_key": {"$gte": "20200101"}}},
        {"$group": {"_id": "$active_date_key", "branches": {"$sum": 1}, "value": {"$sum": "$total_value"}}},
    ]
    rows = await db.batch_summaries.aggregate(pipeline, allowDiskUse=True).to_list(5000)
    out = []
    for r in rows:
        dk = str(r.get("_id") or "").replace("-", "")[:8]
        if len(dk) == 8 and dk.isdigit():
            out.append({"date": f"{dk[0:4]}-{dk[4:6]}-{dk[6:8]}", "branches": r.get("branches"), "value": r.get("value")})
    return sorted(out, key=lambda x: x["date"])


async def cmd_list_dates(db, args) -> int:
    import analytics_rollup as ar

    rows = await _upload_dates(db, args.from_date, args.to_date)
    _say(f"{'date':<11} {'branches':>8} {'upload_value':>18}  rollup")
    for r in rows:
        st = await ar.get_rollup_status(db, r["date"]) or {}
        state = st.get("status") or "-"
        ver = ar.rollup_version_of(st) if st else "-"
        _say(f"{r['date']:<11} {int(r['branches'] or 0):>8} {float(r['value'] or 0):>18,.2f}  {state} v{ver} {st.get('finalized_at') or ''}")
    _say(f"dates={len(rows)} today_ist={_today_ist()} (today is never finalized)")
    return 0


# ---------------- S3 fallback (CLI only, streamed line by line) ----------------

async def _archive_source(db, iso: str):
    """Return (branches, rows_for_branch) from that date's verified archives, or None."""
    import analytics_rollup as ar
    import s3_storage

    s3_storage.load_storage_dotenv()
    storage = s3_storage.get_storage()
    if not storage.is_s3():
        _say(f"  {iso}: S3 fallback unavailable (storage is not real S3)")
        return None
    mans = await db.archive_manifests.find(
        {"module": "product-history", "archive_date": iso, "status": {"$in": ["VERIFIED", "PRUNED"]}},
        {"_id": 0, "storage_key": 1},
    ).to_list(5000)
    keys = []
    for m in mans:
        key = str(m.get("storage_key") or "")
        if key and "/cancelled/" not in key and ("/current/" in key or f"/{iso}/" in key):
            keys.append(key)
    if not keys:
        _say(f"  {iso}: no verified product-history archive")
        return None
    blobs: List[bytes] = []
    total = 0
    for key in sorted(set(keys)):
        try:
            data, _ctype = storage.download_bytes(key)
        except Exception as exc:
            _say(f"  {iso}: archive download failed key=...{key[-40:]} err={type(exc).__name__}")
            continue
        total += len(data)
        if total > MAX_ARCHIVE_COMPRESSED_BYTES:
            raise SystemExit(f"Refusing {iso}: archives exceed {MAX_ARCHIVE_COMPRESSED_BYTES} compressed bytes")
        blobs.append(data)
    if not blobs:
        return None

    def iter_rows():
        for data in blobs:
            with gzip.GzipFile(fileobj=BytesIO(data), mode="rb") as gz:
                for line in gz:
                    text = line.strip()
                    if not text:
                        continue
                    r = json.loads(text)
                    uno = str(r.get("upload_no") or "").strip()
                    if uno and not ar.upload_matches_date(uno, iso):
                        continue
                    if str(r.get("publish_status") or "Published") != "Published":
                        continue
                    yield r

    seen: Dict[Tuple[str, str, str], Tuple[Any, Any, Any]] = {}
    for r in iter_rows():
        raw = (r.get("brand_name"), r.get("dealer_name"), r.get("branch"))
        seen.setdefault(tuple(str(x or "").strip() for x in raw), raw)
    branches = [seen[k] for k in sorted(seen)]

    async def rows_for_branch(branch_key):
        # One pass per branch keeps memory at one branch of rows.
        want = tuple(str(x or "").strip() for x in branch_key)
        rows = [r for r in iter_rows() if tuple(str(r.get(f) or "").strip() for f in ("brand_name", "dealer_name", "branch")) == want]
        rows.sort(key=lambda r: (str(r.get("published_at") or ""), str(r.get("updated_at") or "")), reverse=True)
        for r in rows:
            yield r

    _say(f"  {iso}: S3 fallback archives={len(blobs)} compressed_bytes={total} branches={len(branches)}")
    return branches, rows_for_branch


async def _finalize(db, iso: str, *, force: bool, s3_fallback: bool) -> dict:
    import analytics_rollup as ar

    result = await ar.finalize_analytics_rollup_for_date(db, iso, force=force, progress=_say)
    if result.get("status") == ar.STATUS_NO_SOURCE and s3_fallback:
        src = await _archive_source(db, iso)
        if src:
            branches, rows_for_branch = src
            result = await ar.finalize_analytics_rollup_for_date(
                db, iso, force=True, branches=branches, rows_for_branch=rows_for_branch,
                source_kind="s3_archive", progress=_say,
            )
    _say(
        f"{iso}: status={result.get('status')} skipped={bool(result.get('skipped'))} "
        f"branches={result.get('branch_count')} parts={result.get('snapshot_record_count')} "
        f"value={result.get('daily_agg_total_value')} source={result.get('source_kind')} "
        f"error={result.get('error') or '-'}"
    )
    return result


async def cmd_finalize_day(db, args) -> int:
    import analytics_rollup as ar

    iso = _past_only(_iso(args.date))
    result = await _finalize(db, iso, force=args.force, s3_fallback=args.s3_fallback)
    return 0 if result.get("status") in {ar.STATUS_SUCCESS, ar.STATUS_NO_SOURCE} else 1


async def cmd_backfill(db, args) -> int:
    import analytics_rollup as ar

    start, end = _iso(args.from_date), _past_only(_iso(args.to_date))
    if end < start:
        raise SystemExit("--to must be on or after --from")
    dates = [r["date"] for r in await _upload_dates(db, start, end)]
    _say(f"backfill {start}..{end}: {len(dates)} upload date(s) {', '.join(dates)}")
    failed = 0
    for i, iso in enumerate(dates, start=1):
        if i > 1:
            await asyncio.sleep(ar.DATE_PAUSE_SECONDS)
        _say(f"[{i}/{len(dates)}] {iso}")
        result = await _finalize(db, iso, force=args.force, s3_fallback=args.s3_fallback)
        if result.get("status") not in {ar.STATUS_SUCCESS, ar.STATUS_NO_SOURCE}:
            failed += 1
    _say(f"backfill done: dates={len(dates)} failed={failed} max_rss_mb={_max_rss_mb()}")
    return 1 if failed else 0


async def _source_recount(db, iso: str) -> Dict[str, dict]:
    """Recount Published source rows of that date per branch (streamed)."""
    import analytics_rollup as ar

    snap_date = datetime.strptime(iso, "%Y-%m-%d").date()
    out: Dict[str, dict] = {}
    rows_for = ar.db_rows_for_branch(db, iso)
    for bk in await ar.list_source_branches(db, iso):
        name = "/".join(str(x or "").strip() for x in bk)
        acc = _empty_acc()
        seen = set()
        async for r in rows_for(bk):
            part = str(r.get("part_number") or "").strip().casefold()
            if not part or part in seen:
                continue
            seen.add(part)
            qty, _unit, value = ar._row_qty_unit_value(r)
            acc["closing"] += value
            acc["parts"] += 1
            for basis in ar.AGING_BASES:
                days = ar.aging_days_as_of(r, basis, snap_date)
                acc[basis][ar._analytics_bucket(days)] += value
                if days is None:
                    acc[f"missing_{basis}"] += value
        out[name] = acc
        await asyncio.sleep(ar.BRANCH_PAUSE_SECONDS)
    return out


def _empty_acc() -> dict:
    import analytics_rollup as ar

    return {
        "closing": 0.0, "parts": 0,
        "purchase": {b: 0.0 for b in ar.AGING_BUCKETS}, "sales": {b: 0.0 for b in ar.AGING_BUCKETS},
        "missing_purchase": 0.0, "missing_sales": 0.0,
    }


async def _stored_by_branch(db, iso: str) -> Dict[str, dict]:
    import analytics_rollup as ar

    rows = await db[ar.DAILY_AGG_COLLECTION].find({"rollup_date_ist": iso}, {"_id": 0}).to_list(50000)
    out: Dict[str, dict] = defaultdict(_empty_acc)
    for r in rows:
        name = "/".join(str(r.get(f) or "").strip() for f in ("brand_name", "dealer_name", "branch_name"))
        acc = out[name]
        acc["closing"] += float(r.get("stock_value") or 0)
        acc["parts"] += int(r.get("line_count") or 0)
        for basis in ar.AGING_BASES:
            for b in ar.AGING_BUCKETS:
                acc[basis][b] += float(((r.get(f"aging_{basis}") or {}).get(b) or {}).get("value") or 0)
            acc[f"missing_{basis}"] += float((r.get(f"missing_aging_{basis}") or {}).get("value") or 0)
    return dict(out)


def _total(accs: Dict[str, dict]) -> dict:
    t = _empty_acc()
    for a in accs.values():
        t["closing"] += a["closing"]
        t["parts"] += a["parts"]
        for basis in ("purchase", "sales"):
            for b in a[basis]:
                t[basis][b] += a[basis][b]
            t[f"missing_{basis}"] += a[f"missing_{basis}"]
    return t


async def cmd_reconcile(db, args) -> int:
    import analytics_rollup as ar

    dates = [_iso(d.strip()) for d in str(args.dates).split(",") if d.strip()]
    master_rows = await db.branches.find({}, {"_id": 0, "name": 1, "status": 1}).to_list(20000)
    master = {
        str(r.get("name") or "").strip().casefold()
        for r in master_rows
        if str(r.get("status") or "active").lower() in {"active", "", "none"}
    }
    bad = 0
    for iso in dates:
        _say(f"=== {iso}")
        st = await ar.get_rollup_status(db, iso) or {}
        _say(f"status={st.get('status')} v{ar.rollup_version_of(st) if st else '-'} finalized_at={st.get('finalized_at')}")
        stored = await _stored_by_branch(db, iso)
        source = await _source_recount(db, iso)
        s_tot, src_tot = _total(stored), _total(source)
        prev = await ar.last_successful_rollup_date(
            db,
            on_or_before=(datetime.strptime(iso, "%Y-%m-%d").date() - timedelta(days=1)).isoformat(),
            min_version=ar.ROLLUP_VERSION,
        )
        opening = _total(await _stored_by_branch(db, prev))["closing"] if prev else None
        closing = s_tot["closing"]
        change = (closing - opening) if opening is not None else None
        _say(f"Opening={'N/A' if opening is None else f'{opening:,.2f}'} (from {prev or '-'})  "
             f"Closing={closing:,.2f}  Change={'N/A' if change is None else f'{change:,.2f}'}")

        diffs = []

        def chk(label, a, b):
            d = round(a - b, 2)
            if abs(d) > 0.01:
                diffs.append(f"{label}: snapshot={a:,.2f} source={b:,.2f} diff={d:,.2f}")

        chk("Closing", s_tot["closing"], src_tot["closing"])
        chk("Parts", float(s_tot["parts"]), float(src_tot["parts"]))
        for basis in ("purchase", "sales"):
            for b in ar.AGING_BUCKETS:
                chk(f"{basis} {b}", s_tot[basis][b], src_tot[basis][b])
            chk(f"{basis} missing aging", s_tot[f"missing_{basis}"], src_tot[f"missing_{basis}"])
            bucket_sum = sum(s_tot[basis].values())
            if abs(bucket_sum - closing) > 0.01:
                diffs.append(f"{basis} buckets sum {bucket_sum:,.2f} != Closing {closing:,.2f}")
        for name in sorted(set(stored) | set(source)):
            a = stored.get(name, _empty_acc())["closing"]
            b = source.get(name, _empty_acc())["closing"]
            if abs(a - b) > 0.01:
                diffs.append(f"branch {name}: snapshot={a:,.2f} source={b:,.2f}")
        for basis in ("purchase", "sales"):
            _say(f"{basis}: " + "  ".join(f"{b}={s_tot[basis][b]:,.2f}" for b in ar.AGING_BUCKETS)
                 + f"  missing={s_tot[f'missing_{basis}']:,.2f}")

        not_in_master = sorted(
            (name, acc["closing"]) for name, acc in stored.items()
            if name.split("/")[-1].casefold() not in master
        )
        if not_in_master:
            _say("Branches NOT in branches master (hidden from All-branch screen totals):")
            for name, val in not_in_master:
                _say(f"  {name}  value={val:,.2f}")
        upload_rows = await db.batch_summaries.find(
            {"active_date_key": {"$in": [iso.replace('-', ''), iso]}},
            {"_id": 0, "brand_name": 1, "dealer_name": 1, "branch": 1, "total_value": 1},
        ).to_list(20000)
        upload_total = sum(float(r.get("total_value") or 0) for r in upload_rows)
        _say(f"batch_summaries upload total={upload_total:,.2f} (info: upload-time totals, before latest-row dedupe)")

        if diffs:
            bad += 1
            _say(f"DIFFERENCES ({len(diffs)}):")
            for d in diffs:
                _say(f"  {d}")
        else:
            _say("DIFFERENCES: 0")
    _say(f"reconcile done: dates={len(dates)} with_differences={bad} max_rss_mb={_max_rss_mb()}")
    return 1 if bad else 0


async def cmd_status(db, args) -> int:
    import analytics_rollup as ar

    iso = _iso(args.date) if args.date else await ar.last_successful_rollup_date(db, min_version=ar.ROLLUP_VERSION)
    if not iso:
        _say("No current-version (v2) successful rollup yet")
        return 1
    st = await ar.get_rollup_status(db, iso)
    if not st:
        _say(f"{iso}: no rollup status row")
        return 1
    keep = (
        "rollup_date_ist", "status", "rollup_version", "source_kind", "branch_count",
        "snapshot_record_count", "daily_agg_record_count", "daily_agg_total_qty", "daily_agg_total_value",
        "reconciled", "started_at", "finalized_at", "error",
    )
    for k in keep:
        _say(f"{k}: {st.get(k)}")
    return 0 if ar.is_current_success(st) else 1


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("list-dates")
    s.add_argument("--from", dest="from_date")
    s.add_argument("--to", dest="to_date")
    s = sub.add_parser("backfill")
    s.add_argument("--from", dest="from_date", required=True)
    s.add_argument("--to", dest="to_date", required=True)
    s.add_argument("--force", action="store_true", help="rebuild dates already finalized at v2")
    s.add_argument("--s3-fallback", action="store_true", help="stream verified S3 archives when DB rows are gone")
    s = sub.add_parser("finalize-day")
    s.add_argument("--date", required=True)
    s.add_argument("--force", action="store_true")
    s.add_argument("--s3-fallback", action="store_true")
    s = sub.add_parser("reconcile")
    s.add_argument("--dates", required=True, help="comma separated YYYY-MM-DD")
    s = sub.add_parser("status")
    s.add_argument("--date")
    args = p.parse_args()

    async def run() -> int:
        db, mode = _connect()
        _say(f"db=nmts_testing mode={mode} today_ist={_today_ist()}")
        handler = {
            "list-dates": cmd_list_dates,
            "backfill": cmd_backfill,
            "finalize-day": cmd_finalize_day,
            "reconcile": cmd_reconcile,
            "status": cmd_status,
        }[args.cmd]
        return await handler(db, args)

    return asyncio.run(run())


if __name__ == "__main__":
    raise SystemExit(main())
