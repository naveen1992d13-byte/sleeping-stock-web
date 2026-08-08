"""Auto Perpetual draft suggestions (APS), 50/25/25 selection, send-to-mobile."""
from __future__ import annotations

import math
import re
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Set, Tuple

from fastapi import HTTPException
from pymongo.errors import DuplicateKeyError

from auto_perpetual import (
    IST,
    _attendance_active_users,
    _inventory_qty_map,
    _now,
    _previous_inventory_date_key,
    _verified_parts_this_month,
    _working_days_left_in_month,
    branch_code,
    get_or_create_auto_daily_session,
    ist_date_key,
    month_key,
    sync_monthly_pool,
    yymmdd_key,
)


def _natural_sort_key(text: str) -> Tuple:
    s = (text or "").strip().upper()
    parts = re.split(r"(\d+)", s)
    out: List[Any] = []
    for p in parts:
        if not p:
            continue
        out.append(int(p) if p.isdigit() else p)
    return tuple(out)


def _location_row(loc: str) -> str:
    loc = (loc or "").strip().upper()
    bits = loc.split("-")
    if len(bits) >= 2:
        return f"{bits[0]}-{bits[1]}"
    return loc or "UNKNOWN"


def _product_mav(product: Optional[dict]) -> float:
    if not product:
        return 0.0
    for k in ("mav", "MAV", "unit_value", "value"):
        try:
            return float(product.get(k) or 0)
        except (TypeError, ValueError):
            continue
    return 0.0


async def ensure_suggestion_indexes(db) -> None:
    await db.auto_perpetual_suggestions.create_index(
        [("allocation_date", 1), ("branch", 1), ("brand_name", 1), ("dealer_name", 1)],
        unique=True,
        name="uq_aps_branch_day",
    )
    await db.auto_perpetual_suggestions.create_index([("suggestion_number", 1)], unique=True, name="uq_aps_number")
    await db.auto_perpetual_suggestions.create_index([("branch", 1), ("month_key", 1), ("status", 1)])
    await db.auto_perpetual_branch_planner.create_index(
        [("month_key", 1), ("branch", 1), ("brand_name", 1), ("dealer_name", 1)],
        unique=True,
        name="uq_auto_planner_branch_month",
    )


async def _next_aps_number(db) -> str:
    date_key = yymmdd_key()
    counter_id = f"aps_suggestion_{date_key}"
    from pymongo import ReturnDocument

    counter = await db.counters.find_one_and_update(
        {"_id": counter_id},
        {"$inc": {"seq": 1}, "$setOnInsert": {"date_key": date_key}},
        upsert=True,
        return_document=ReturnDocument.AFTER,
    )
    seq = int(counter.get("seq", 1))
    if seq > 9999:
        raise HTTPException(status_code=500, detail="Daily APS serial exhausted")
    return f"APS{date_key}{seq:04d}"


async def _get_planner(db, *, month: str, brand_name: str, dealer_name: str, branch: str) -> dict:
    doc = await db.auto_perpetual_branch_planner.find_one(
        {"month_key": month, "branch": branch, "brand_name": brand_name, "dealer_name": dealer_name},
        {"_id": 0},
    )
    if doc:
        return doc
    return {
        "month_key": month,
        "branch": branch,
        "brand_name": brand_name,
        "dealer_name": dealer_name,
        "missed_generation_backlog": 0,
        "last_accrual_date": None,
    }


async def accrue_missed_generation_backlog(
    db, *, brand_name: str, dealer_name: str, branch: str, month: str
) -> int:
    """If prior IST days had no SENT suggestion, add their planned totals to backlog."""
    today = ist_date_key()
    planner = await _get_planner(db, month=month, brand_name=brand_name, dealer_name=dealer_name, branch=branch)
    last = planner.get("last_accrual_date") or today
    if last >= today:
        return int(planner.get("missed_generation_backlog") or 0)

    d = datetime.strptime(last, "%Y-%m-%d").date()
    end = datetime.strptime(today, "%Y-%m-%d").date()
    backlog_add = 0
    while d < end:
        dkey = d.isoformat()
        sent = await db.auto_perpetual_suggestions.find_one(
            {
                "allocation_date": dkey,
                "branch": branch,
                "brand_name": brand_name,
                "dealer_name": dealer_name,
                "status": {"$in": ["SENT", "IN_PROGRESS", "COMPLETED"]},
            },
            {"_id": 0, "total_items": 1},
        )
        if not sent:
            draft = await db.auto_perpetual_suggestions.find_one(
                {
                    "allocation_date": dkey,
                    "branch": branch,
                    "brand_name": brand_name,
                    "dealer_name": dealer_name,
                    "status": "DRAFT",
                },
                {"_id": 0, "planned_total_items": 1, "total_items": 1},
            )
            if draft:
                backlog_add += int(draft.get("planned_total_items") or draft.get("total_items") or 0)
            else:
                run = await db.auto_perpetual_daily_runs.find_one(
                    {
                        "allocation_date": dkey,
                        "branch": branch,
                        "brand_name": brand_name,
                        "dealer_name": dealer_name,
                    },
                    {"_id": 0, "baseline_per_user": 1, "active_users": 1},
                )
                if run:
                    n = len(run.get("active_users") or [])
                    backlog_add += int(run.get("baseline_per_user") or 0) * max(1, n)
        d += timedelta(days=1)

    new_backlog = int(planner.get("missed_generation_backlog") or 0) + backlog_add
    await db.auto_perpetual_branch_planner.update_one(
        {"month_key": month, "branch": branch, "brand_name": brand_name, "dealer_name": dealer_name},
        {
            "$set": {
                "missed_generation_backlog": new_backlog,
                "last_accrual_date": today,
                "updated_at": _now(),
            },
            "$setOnInsert": {"id": str(uuid.uuid4()), "created_at": _now()},
        },
        upsert=True,
    )
    return new_backlog


def recovery_extra_from_backlog(backlog: int, days_left: int) -> Tuple[int, int, bool]:
    """Returns (extra_per_user, recovery_days, month_end_pressure)."""
    if backlog <= 0:
        return 0, 0, False
    recovery_days = min(4, max(1, days_left))
    extra = min(5, max(1, -(-backlog // recovery_days)))  # ceil division, soft cap 5
    month_end_pressure = False
    if days_left <= 2 and backlog > extra * recovery_days * 2:
        month_end_pressure = True
        extra = min(max(extra, -(-backlog // max(1, days_left))), backlog)
    return extra, recovery_days, month_end_pressure


async def tag_movement_on_pool(
    db,
    *,
    brand_name: str,
    dealer_name: str,
    branch: str,
    month: str,
    current_date_key: str,
) -> None:
    """Tag monthly pool rows with movement hints. Never re-open verified parts (no recheck)."""
    prev_key = await _previous_inventory_date_key(
        db, brand_name=brand_name, dealer_name=dealer_name, branch=branch, current_key=current_date_key
    )
    if not prev_key:
        return
    verified = await _verified_parts_this_month(db, brand_name=brand_name, dealer_name=dealer_name, branch=branch, month=month)
    current = await _inventory_qty_map(
        db, brand_name=brand_name, dealer_name=dealer_name, branch=branch, active_date_key=current_date_key
    )
    previous = await _inventory_qty_map(
        db, brand_name=brand_name, dealer_name=dealer_name, branch=branch, active_date_key=prev_key
    )
    now = _now()
    for part, cur_q in current.items():
        if part in verified:
            continue
        if cur_q <= 0:
            await db.auto_perpetual_pool.update_one(
                {"month_key": month, "branch": branch, "part_number": part, "coverage_kind": "monthly"},
                {"$set": {"status": "na", "movement_note": "zero_qty", "updated_at": now}},
            )
            continue
        prev_q = previous.get(part, 0.0)
        movement = "location"
        if part not in previous:
            movement = "new_line"
        elif cur_q > prev_q:
            movement = "qty_increased"
        elif cur_q < prev_q:
            movement = "qty_decreased"
        await db.auto_perpetual_pool.update_one(
            {"month_key": month, "branch": branch, "part_number": part, "coverage_kind": "monthly"},
            {"$set": {"movement_reason": movement, "updated_at": now}},
        )


def _split_mix(total: int) -> Tuple[int, int, int]:
    if total <= 0:
        return 0, 0, 0
    loc = int(round(total * 0.5))
    inc = int(round(total * 0.25))
    dec = total - loc - inc
    return loc, inc, dec


def _pick_from_bucket(
    candidates: List[dict], need: int, used: Set[str], row_first: bool = True
) -> List[dict]:
    if need <= 0 or not candidates:
        return []
    if row_first:
        candidates = sorted(
            candidates,
            key=lambda r: (_location_row(r.get("system_location") or r.get("loc") or ""), _natural_sort_key(r.get("system_location") or r.get("loc") or "")),
        )
    else:
        candidates = sorted(candidates, key=lambda r: _natural_sort_key(r.get("system_location") or r.get("loc") or ""))
    picked: List[dict] = []
    for row in candidates:
        p = row["part_number"]
        if p in used:
            continue
        picked.append(row)
        used.add(p)
        if len(picked) >= need:
            break
    return picked


def select_items_502525(
    eligible: List[dict], total_needed: int, verified: Set[str]
) -> List[dict]:
    """50% location / 25% increased / 25% decreased; fill shortfalls; dedupe."""
    pool = [r for r in eligible if r["part_number"] not in verified and float(r.get("system_qty") or 0) > 0]
    inc_pool = [r for r in pool if r.get("movement") in ("qty_increased", "new_line")]
    dec_pool = [r for r in pool if r.get("movement") == "qty_decreased"]
    loc_pool = [r for r in pool if r.get("movement") in ("location", None, "new_line") or r not in inc_pool + dec_pool]
    need_loc, need_inc, need_dec = _split_mix(total_needed)
    used: Set[str] = set()
    out: List[dict] = []
    for chunk, need, row_first in (
        (loc_pool, need_loc, True),
        (inc_pool, need_inc, True),
        (dec_pool, need_dec, True),
    ):
        got = _pick_from_bucket(chunk, need, used, row_first=row_first)
        for g in got:
            g = dict(g)
            if g in loc_pool and g.get("movement") not in ("qty_increased", "qty_decreased"):
                g["suggestion_type"] = "LOCATION"
            elif g in inc_pool:
                g["suggestion_type"] = "INCREASED"
            elif g in dec_pool:
                g["suggestion_type"] = "DECREASED"
            else:
                g["suggestion_type"] = "LOCATION"
            out.append(g)
    remaining = total_needed - len(out)
    if remaining > 0:
        rest = [r for r in pool if r["part_number"] not in used]
        for g in _pick_from_bucket(rest, remaining, used, row_first=True):
            g = dict(g)
            g["suggestion_type"] = g.get("suggestion_type") or "LOCATION"
            out.append(g)
    out.sort(key=lambda r: _natural_sort_key(r.get("system_location") or r.get("loc") or ""))
    return out[:total_needed]


async def compute_user_targets(
    db,
    *,
    brand_name: str,
    dealer_name: str,
    branch: str,
    month: str,
    active_users: List[dict],
    pending_count: int,
    backlog: int,
    days_left: int,
) -> Tuple[Dict[str, int], int, int, bool]:
    n = max(1, len(active_users))
    baseline = max(1, -(-pending_count // max(1, n * days_left)))
    extra, rec_days, pressure = recovery_extra_from_backlog(backlog, days_left)
    targets: Dict[str, int] = {}
    for u in active_users:
        mu = u["mobile_user_id"]
        att_catch = min(int(u.get("auto_catch_up_pending") or 0), baseline * 2)
        targets[mu] = baseline + att_catch + extra
    return targets, baseline, extra, pressure


async def create_draft_suggestion(
    db,
    *,
    brand_name: str,
    dealer_name: str,
    branch: str,
    actor_user_id: str,
    active_date_key: str,
    force: bool = False,
) -> Dict[str, Any]:
    allocation_date = ist_date_key()
    month = month_key()
    scope = {
        "allocation_date": allocation_date,
        "branch": branch,
        "brand_name": brand_name,
        "dealer_name": dealer_name,
    }
    existing = await db.auto_perpetual_suggestions.find_one(scope, {"_id": 0})
    if existing and existing.get("status") != "DRAFT" and not force:
        return {"duplicate": True, "suggestion": existing}
    if existing and existing.get("status") == "DRAFT" and not force:
        return {"duplicate": True, "suggestion": existing}

    backlog = await accrue_missed_generation_backlog(
        db, brand_name=brand_name, dealer_name=dealer_name, branch=branch, month=month
    )
    sample_pool = await db.auto_perpetual_pool.find_one(
        {"month_key": month, "branch": branch, "brand_name": brand_name, "coverage_kind": "monthly"},
        {"inventory_date_key": 1},
    )
    if not sample_pool or sample_pool.get("inventory_date_key") != active_date_key:
        await sync_monthly_pool(
            db,
            brand_name=brand_name,
            dealer_name=dealer_name,
            branch=branch,
            active_date_key=active_date_key,
            month=month,
        )
    await tag_movement_on_pool(
        db,
        brand_name=brand_name,
        dealer_name=dealer_name,
        branch=branch,
        month=month,
        current_date_key=active_date_key,
    )

    active_users = await _attendance_active_users(
        db, brand_name=brand_name, dealer_name=dealer_name, branch=branch, attendance_date=allocation_date
    )
    if not active_users:
        raise HTTPException(status_code=400, detail="Mark at least one mobile user Active for today before generating Auto Perpetual")

    days_left = _working_days_left_in_month(datetime.now(tz=IST))
    pending_rows_pre = await db.auto_perpetual_pool.count_documents(
        {
            "month_key": month,
            "branch": branch,
            "brand_name": brand_name,
            "dealer_name": dealer_name,
            "coverage_kind": "monthly",
            "status": "pending",
        }
    )
    baseline_pre = max(1, -(-pending_rows_pre // max(1, len(active_users) * days_left)))
    active_ids = {u["mobile_user_id"] for u in active_users}
    inactive_today = await db.mobile_user_attendance.find(
        {"attendance_date": allocation_date, "branch": branch, "status": "inactive"},
        {"_id": 0, "mobile_user_id": 1},
    ).to_list(500)
    for row in inactive_today:
        mu_id = row["mobile_user_id"]
        if mu_id not in active_ids:
            await db.mobile_users.update_one(
                {"mobile_user_id": mu_id},
                {"$inc": {"auto_catch_up_pending": baseline_pre}},
            )

    verified = await _verified_parts_this_month(db, brand_name=brand_name, dealer_name=dealer_name, branch=branch, month=month)
    pending_rows = await db.auto_perpetual_pool.find(
        {
            "month_key": month,
            "branch": branch,
            "brand_name": brand_name,
            "dealer_name": dealer_name,
            "coverage_kind": "monthly",
            "status": "pending",
        },
        {"_id": 0},
    ).to_list(50000)

    eligible: List[dict] = []
    for row in pending_rows:
        part = str(row.get("part_number") or "").strip().upper()
        qty = float(row.get("system_qty") or 0)
        if not part or qty <= 0 or part in verified:
            continue
        loc = str(row.get("loc") or "").strip()
        eligible.append(
            {
                "part_number": part,
                "system_location": loc,
                "loc": loc,
                "system_qty": qty,
                "movement": row.get("movement_reason") or "location",
            }
        )

    days_left = _working_days_left_in_month(datetime.now(tz=IST))
    targets, baseline, recovery_extra, month_end_pressure = await compute_user_targets(
        db,
        brand_name=brand_name,
        dealer_name=dealer_name,
        branch=branch,
        month=month,
        active_users=active_users,
        pending_count=len(eligible),
        backlog=backlog,
        days_left=days_left,
    )
    total_needed = sum(targets.values())
    selected = select_items_502525(eligible, total_needed, verified)

    items: List[dict] = []
    loc_c = inc_c = dec_c = 0
    total_qty = 0.0
    total_value = 0.0
    for row in selected:
        product = await db.products.find_one(
            {
                "brand_name": brand_name,
                "dealer_name": dealer_name,
                "branch": branch,
                "part_number": row["part_number"],
                "publish_status": "Published",
            },
            {"_id": 0},
        )
        part_name = (
            (product or {}).get("part_name")
            or (product or {}).get("item_name")
            or (product or {}).get("description")
            or ""
        )
        mav = _product_mav(product)
        qty = float(row.get("system_qty") or 0)
        st = row.get("suggestion_type") or "LOCATION"
        if st == "INCREASED":
            inc_c += 1
        elif st == "DECREASED":
            dec_c += 1
        else:
            loc_c += 1
        total_qty += qty
        total_value += qty * mav
        items.append(
            {
                "part_number": row["part_number"],
                "part_name": part_name,
                "system_qty": qty,
                "system_location": row.get("system_location") or "",
                "suggestion_type": st,
                "mav": mav,
                "line_value": round(qty * mav, 2),
                "verification_status": "pending",
                "mobile_user_id": None,
                "batch_no": None,
            }
        )

    aps = await _next_aps_number(db)
    doc = {
        "id": str(uuid.uuid4()),
        "suggestion_number": aps,
        **scope,
        "month_key": month,
        "status": "DRAFT",
        "items": items,
        "total_items": len(items),
        "planned_total_items": total_needed,
        "total_qty": round(total_qty, 2),
        "total_value": round(total_value, 2),
        "location_count": loc_c,
        "increased_count": inc_c,
        "decreased_count": dec_c,
        "targets_by_user": targets,
        "baseline_per_user": baseline,
        "recovery_extra_per_user": recovery_extra,
        "missed_generation_backlog": backlog,
        "month_end_recovery_warning": month_end_pressure,
        "active_user_ids": [u["mobile_user_id"] for u in active_users],
        "inventory_date_key": active_date_key,
        "created_at": _now(),
        "created_by": actor_user_id,
    }
    try:
        await db.auto_perpetual_suggestions.insert_one(doc)
    except DuplicateKeyError:
        raced = await db.auto_perpetual_suggestions.find_one(scope, {"_id": 0})
        return {"duplicate": True, "suggestion": raced}

    await db.auto_perpetual_daily_runs.update_one(
        scope,
        {
            "$set": {
                "status": "draft",
                "suggestion_number": aps,
                "baseline_per_user": baseline,
                "planned_total_items": total_needed,
                "generated_at": _now(),
                "generated_by": actor_user_id,
            },
            "$setOnInsert": {"id": str(uuid.uuid4()), "month_key": month, "created_at": _now()},
        },
        upsert=True,
    )
    doc.pop("_id", None)
    return {"duplicate": False, "suggestion": doc}


BATCH_SIZE = 20


async def send_suggestion_to_mobile(
    db,
    *,
    suggestion_id: str,
    actor_user_id: str,
    notify_fn=None,
) -> Dict[str, Any]:
    sug = await db.auto_perpetual_suggestions.find_one({"id": suggestion_id}, {"_id": 0})
    if not sug:
        raise HTTPException(404, "Auto suggestion not found")
    if sug.get("status") != "DRAFT":
        raise HTTPException(400, f"Suggestion status is {sug.get('status')}; only DRAFT can be sent")

    brand_name = sug["brand_name"]
    dealer_name = sug["dealer_name"]
    branch = sug["branch"]
    month = sug["month_key"]
    allocation_date = sug["allocation_date"]
    verified = await _verified_parts_this_month(db, brand_name=brand_name, dealer_name=dealer_name, branch=branch, month=month)

    active_users = await _attendance_active_users(
        db, brand_name=brand_name, dealer_name=dealer_name, branch=branch, attendance_date=allocation_date
    )
    if not active_users:
        raise HTTPException(400, "No active mobile users for today")

    targets = sug.get("targets_by_user") or {}
    mu_order = [u["mobile_user_id"] for u in active_users if targets.get(u["mobile_user_id"], 0) > 0]
    if not mu_order:
        mu_order = [u["mobile_user_id"] for u in active_users]

    items = sorted(
        list(sug.get("items") or []),
        key=lambda r: _natural_sort_key(r.get("system_location") or ""),
    )
    valid_items: List[dict] = []
    for it in items:
        part = str(it.get("part_number") or "").upper()
        qty = float(it.get("system_qty") or 0)
        if part in verified or qty <= 0:
            continue
        pool = await db.auto_perpetual_pool.find_one(
            {"month_key": month, "branch": branch, "part_number": part, "coverage_kind": "monthly", "status": "pending"},
            {"_id": 0},
        )
        if not pool:
            continue
        valid_items.append(it)

    if not valid_items:
        raise HTTPException(400, "No eligible items remain to send (zero qty or already verified)")

    idx = 0
    assignments_by_user: Dict[str, int] = {}
    updated_items: List[dict] = []
    for mu in mu_order:
        need = int(targets.get(mu) or 0)
        if need <= 0:
            continue
        session_id = await get_or_create_auto_daily_session(
            db,
            mobile_user_id=mu,
            brand_name=brand_name,
            dealer_name=dealer_name,
            branch=branch,
        )
        await db.stock_verification_sessions.update_one(
            {"session_id": session_id},
            {
                "$set": {
                    "suggestion_id": sug["id"],
                    "suggestion_number": sug["suggestion_number"],
                    "updated_at": _now(),
                }
            },
        )
        batch_no = 1
        batch_count = 0
        assignments_by_user[mu] = 0
        while need > 0 and idx < len(valid_items):
            it = valid_items[idx]
            idx += 1
            part = it["part_number"]
            try:
                await db.auto_perpetual_assignments.insert_one(
                    {
                        "id": str(uuid.uuid4()),
                        "allocation_date": allocation_date,
                        "month_key": month,
                        "brand_name": brand_name,
                        "dealer_name": dealer_name,
                        "branch": branch,
                        "mobile_user_id": mu,
                        "part_number": part,
                        "loc": it.get("system_location"),
                        "coverage_kind": "monthly",
                        "session_id": session_id,
                        "status": "pending",
                        "suggestion_number": sug["suggestion_number"],
                        "suggestion_id": sug["id"],
                        "batch_no": batch_no,
                        "suggestion_type": it.get("suggestion_type"),
                        "assigned_at": _now(),
                        "assigned_by": actor_user_id,
                    }
                )
            except DuplicateKeyError:
                continue
            await db.auto_perpetual_pool.update_one(
                {"month_key": month, "branch": branch, "part_number": part, "coverage_kind": "monthly"},
                {
                    "$set": {
                        "status": "allocated",
                        "allocated_mobile_user_id": mu,
                        "allocated_date": allocation_date,
                        "updated_at": _now(),
                    }
                },
            )
            batch_count += 1
            assignments_by_user[mu] += 1
            need -= 1
            if batch_count >= BATCH_SIZE:
                batch_no += 1
                batch_count = 0
            updated_items.append({**it, "mobile_user_id": mu, "batch_no": batch_no, "verification_status": "pending"})

    for it in valid_items[idx:]:
        updated_items.append({**it, "verification_status": "skipped_unallocated"})

    sent_count = sum(assignments_by_user.values())
    await db.auto_perpetual_suggestions.update_one(
        {"id": suggestion_id},
        {
            "$set": {
                "status": "SENT" if sent_count else "DRAFT",
                "items": updated_items,
                "sent_at": _now(),
                "sent_by": actor_user_id,
                "assignments_created": sent_count,
                "assignments_by_user": assignments_by_user,
            }
        },
    )
    if sent_count:
        planner = await _get_planner(db, month=month, brand_name=brand_name, dealer_name=dealer_name, branch=branch)
        planned = int(sug.get("planned_total_items") or 0)
        backlog = max(0, int(planner.get("missed_generation_backlog") or 0) - planned)
        await db.auto_perpetual_branch_planner.update_one(
            {"month_key": month, "branch": branch, "brand_name": brand_name, "dealer_name": dealer_name},
            {"$set": {"missed_generation_backlog": backlog, "updated_at": _now()}},
            upsert=True,
        )
        await db.auto_perpetual_daily_runs.update_one(
            {
                "allocation_date": allocation_date,
                "branch": branch,
                "brand_name": brand_name,
                "dealer_name": dealer_name,
            },
            {"$set": {"status": "sent", "assignments_created": sent_count, "sent_at": _now()}},
            upsert=True,
        )

    if notify_fn and assignments_by_user:
        await notify_fn(assignments_by_user=assignments_by_user, branch=branch, allocation_date=allocation_date)

    return {
        "suggestion_number": sug["suggestion_number"],
        "assignments_created": sent_count,
        "assignments_by_user": assignments_by_user,
    }


async def refresh_suggestion_status(db, suggestion_id: str) -> None:
    sug = await db.auto_perpetual_suggestions.find_one({"id": suggestion_id}, {"_id": 0, "items": 1, "status": 1})
    if not sug or sug.get("status") not in ("SENT", "IN_PROGRESS"):
        return
    assigns = await db.auto_perpetual_assignments.count_documents({"suggestion_id": suggestion_id})
    done = await db.auto_perpetual_assignments.count_documents({"suggestion_id": suggestion_id, "status": "completed"})
    if assigns and done >= assigns:
        await db.auto_perpetual_suggestions.update_one({"id": suggestion_id}, {"$set": {"status": "COMPLETED", "completed_at": _now()}})
    elif done > 0:
        await db.auto_perpetual_suggestions.update_one({"id": suggestion_id}, {"$set": {"status": "IN_PROGRESS"}})


async def finish_auto_session_for_user(db, *, mobile_user_id: str, brand_name: str, dealer_name: str, branch: str) -> dict:
    allocation_date = ist_date_key()
    scope = {
        "session_kind": "auto_perpetual",
        "verification_date": allocation_date,
        "mobile_user_id": mobile_user_id,
        "brand_id": brand_name,
        "dealer_id": dealer_name,
        "branch_id": branch,
    }
    sess = await db.stock_verification_sessions.find_one(scope, {"_id": 0})
    if not sess:
        raise HTTPException(404, "No Auto Perpetual session for today")

    assigned = await db.auto_perpetual_assignments.count_documents(
        {"allocation_date": allocation_date, "mobile_user_id": mobile_user_id, "branch": branch}
    )
    completed = await db.auto_perpetual_assignments.count_documents(
        {"allocation_date": allocation_date, "mobile_user_id": mobile_user_id, "branch": branch, "status": "completed"}
    )
    pending = assigned - completed
    status = "COMPLETED" if assigned > 0 and pending == 0 else "PENDING"
    if assigned == 0:
        status = "PENDING"

    match_c = await db.stock_verification_history.count_documents(
        {
            "session_id": sess["session_id"],
            "verification_type": "auto",
            "quantity_status": "matched",
        }
    )
    shortage_c = await db.stock_verification_history.count_documents(
        {"session_id": sess["session_id"], "verification_type": "auto", "quantity_status": "shortage"}
    )
    excess_c = await db.stock_verification_history.count_documents(
        {"session_id": sess["session_id"], "verification_type": "auto", "quantity_status": "excess"}
    )
    loc_m = await db.stock_verification_history.count_documents(
        {"session_id": sess["session_id"], "verification_type": "auto", "location_status": "mismatch"}
    )

    await db.stock_verification_sessions.update_one(
        {"session_id": sess["session_id"]},
        {
            "$set": {
                "status": status,
                "finished_at": _now(),
                "assigned_count": assigned,
                "completed_count": completed,
                "pending_count": max(0, pending),
                "match_count": match_c,
                "shortage_count": shortage_c,
                "excess_count": excess_c,
                "location_mismatch_count": loc_m,
                "updated_at": _now(),
            }
        },
    )
    sid = sess.get("suggestion_id")
    if not sid:
        asn = await db.auto_perpetual_assignments.find_one(
            {"allocation_date": allocation_date, "mobile_user_id": mobile_user_id, "branch": branch},
            {"suggestion_id": 1},
        )
        sid = (asn or {}).get("suggestion_id")
    if sid:
        await refresh_suggestion_status(db, sid)
    return {
        "session_id": sess["session_id"],
        "status": status,
        "assigned_count": assigned,
        "completed_count": completed,
        "pending_count": max(0, pending),
    }
