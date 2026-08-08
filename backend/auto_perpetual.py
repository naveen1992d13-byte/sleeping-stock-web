"""
Auto Perpetual — monthly branch line-item verification pool and daily allocation (AOPS).

Uses existing products (published, active today) and stock_verification_history for
verified coverage. Idempotent daily generation per branch (IST calendar day).
"""
from __future__ import annotations

import re
import uuid
from calendar import monthrange
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from fastapi import HTTPException
from pymongo import ReturnDocument, UpdateOne
from pymongo.errors import DuplicateKeyError
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")


def _now():
    return datetime.now(timezone.utc)


def _ist_now():
    return _now().astimezone(IST)


def month_key(dt: Optional[datetime] = None) -> str:
    d = (dt or _ist_now())
    return d.strftime("%Y-%m")


def ist_date_key(dt: Optional[datetime] = None) -> str:
    return (dt or _ist_now()).strftime("%Y-%m-%d")


def inventory_date_key(dt: Optional[datetime] = None) -> str:
    """Published inventory snapshot key (matches product active_date_key / upload date)."""
    return (dt or _ist_now()).strftime("%Y%m%d")


async def resolve_branch_inventory_date_key(
    db, *, brand_name: str, dealer_name: str, branch: str
) -> str:
    """Latest published inventory snapshot for the branch (falls back to IST today)."""
    row = await db.products.find_one(
        {
            "brand_name": brand_name,
            "dealer_name": dealer_name,
            "branch": branch,
            "publish_status": "Published",
            "is_active_today": True,
            "active_date_key": {"$exists": True, "$ne": ""},
        },
        {"_id": 0, "active_date_key": 1},
        sort=[("active_date_key", -1)],
    )
    return (row or {}).get("active_date_key") or inventory_date_key()


def yymmdd_key(dt: Optional[datetime] = None) -> str:
    return (dt or _ist_now()).strftime("%y%m%d")


def branch_code(branch_name: str) -> str:
    letters = re.sub(r"[^A-Za-z0-9]", "", (branch_name or "").upper())
    code = (letters[:3] or "GEN").ljust(3, "X")
    return code


async def ensure_auto_perpetual_indexes(db) -> None:
    await db.auto_perpetual_pool.create_index(
        [("month_key", 1), ("branch", 1), ("part_number", 1), ("coverage_kind", 1)],
        unique=True,
        name="uq_auto_pool_month_branch_part",
    )
    await db.auto_perpetual_pool.create_index([("month_key", 1), ("branch", 1), ("status", 1)])
    await db.auto_perpetual_pool.create_index([("month_key", 1), ("branch", 1), ("priority_score", -1)])
    await db.auto_perpetual_assignments.create_index(
        [("allocation_date", 1), ("branch", 1), ("mobile_user_id", 1), ("part_number", 1)],
        unique=True,
        name="uq_auto_assign_day_user_part",
    )
    await db.auto_perpetual_daily_runs.create_index(
        [("allocation_date", 1), ("branch", 1), ("brand_name", 1), ("dealer_name", 1)],
        unique=True,
        name="uq_auto_daily_run_branch",
    )
    await db.mobile_user_attendance.create_index(
        [("attendance_date", 1), ("mobile_user_id", 1)],
        unique=True,
        name="uq_mobile_attendance_day_user",
    )
    await db.mobile_push_delivery_logs.create_index([("mobile_user_id", 1), ("created_at", -1)])


async def _next_aops_session_id(db, branch_name: str) -> str:
    date_key = yymmdd_key()
    code = branch_code(branch_name)
    counter_id = f"aops_verification_session_{code}_{date_key}"
    counter = await db.counters.find_one_and_update(
        {"_id": counter_id},
        {"$inc": {"seq": 1}, "$setOnInsert": {"date_key": date_key}},
        upsert=True,
        return_document=ReturnDocument.AFTER,
    )
    seq = int(counter.get("seq", 1))
    if seq > 9999:
        raise HTTPException(status_code=500, detail="Daily AOPS session serial exhausted")
    return f"AOPS{code}{date_key}{seq:04d}"


async def get_or_create_auto_daily_session(
    db,
    *,
    mobile_user_id: str,
    brand_name: str,
    dealer_name: str,
    branch: str,
    device_id: str = "",
) -> str:
    verification_date = ist_date_key()
    scope = {
        "session_kind": "auto_perpetual",
        "verification_date": verification_date,
        "mobile_user_id": mobile_user_id,
        "brand_id": brand_name,
        "dealer_id": dealer_name,
        "branch_id": branch,
        "status": "ACTIVE",
    }
    existing = await db.stock_verification_sessions.find_one(scope, {"_id": 0, "session_id": 1})
    if existing and existing.get("session_id"):
        await db.stock_verification_sessions.update_one(
            {"session_id": existing["session_id"]},
            {"$set": {"updated_at": _now(), "device_id": device_id or None}},
        )
        return existing["session_id"]

    session_id = await _next_aops_session_id(db, branch)
    now = _now()
    session_doc = {
        "id": str(uuid.uuid4()),
        "session_id": session_id,
        "session_kind": "auto_perpetual",
        "verification_type": "auto",
        "verification_date": verification_date,
        "mobile_user_id": mobile_user_id,
        "brand_id": brand_name,
        "dealer_id": dealer_name,
        "branch_id": branch,
        "brand_name": brand_name,
        "dealer_name": dealer_name,
        "branch": branch,
        "device_id": device_id or None,
        "status": "ACTIVE",
        "total_items": 0,
        "source": "AUTO_PERPETUAL",
        "information_only": True,
        "affects_stock": False,
        "created_at": now,
        "updated_at": now,
    }
    try:
        await db.stock_verification_sessions.insert_one(session_doc)
    except DuplicateKeyError:
        raced = await db.stock_verification_sessions.find_one(scope, {"_id": 0, "session_id": 1})
        if raced and raced.get("session_id"):
            return raced["session_id"]
        raise
    return session_id


def _working_days_left_in_month(dt: datetime) -> int:
    last_day = monthrange(dt.year, dt.month)[1]
    return max(1, last_day - dt.day + 1)


async def _verified_parts_this_month(
    db, *, brand_name: str, dealer_name: str, branch: str, month: str
) -> set:
    month_start = datetime(int(month[:4]), int(month[5:7]), 1, tzinfo=IST)
    rows = await db.stock_verification_history.find(
        {
            "brand_name": brand_name,
            "dealer_name": dealer_name,
            "branch": branch,
            "verified_at": {"$gte": month_start},
            "coverage_kind": {"$ne": "recheck"},
            "verification_type": {"$in": ["auto", "physical", "AUTO", "auto_perpetual", "physical_perpetual"]},
        },
        {"_id": 0, "part_number": 1},
    ).to_list(50000)
    return {str(r.get("part_number") or "").strip().upper() for r in rows if r.get("part_number")}


async def _previous_inventory_date_key(db, *, brand_name: str, dealer_name: str, branch: str, current_key: str) -> Optional[str]:
    row = await db.products.find(
        {
            "brand_name": brand_name,
            "dealer_name": dealer_name,
            "branch": branch,
            "publish_status": "Published",
            "active_date_key": {"$lt": current_key},
        },
        {"_id": 0, "active_date_key": 1},
    ).sort("active_date_key", -1).limit(1).to_list(1)
    return row[0]["active_date_key"] if row else None


async def _inventory_qty_map(
    db, *, brand_name: str, dealer_name: str, branch: str, active_date_key: str
) -> Dict[str, float]:
    products = await db.products.find(
        {
            "brand_name": brand_name,
            "dealer_name": dealer_name,
            "branch": branch,
            "publish_status": "Published",
            "active_date_key": active_date_key,
        },
        {"_id": 0, "part_number": 1, "available_qty_number": 1, "quantity": 1},
    ).to_list(50000)
    out: Dict[str, float] = {}
    for p in products:
        part = str(p.get("part_number") or "").strip().upper()
        if not part:
            continue
        out[part] = float(p.get("available_qty_number") or p.get("quantity") or 0)
    return out


async def apply_stock_movement_priority(
    db,
    *,
    brand_name: str,
    dealer_name: str,
    branch: str,
    month: str,
    current_date_key: str,
) -> int:
    """Flag movement-driven recheck candidates (separate from monthly unique coverage)."""
    prev_key = await _previous_inventory_date_key(
        db, brand_name=brand_name, dealer_name=dealer_name, branch=branch, current_key=current_date_key
    )
    if not prev_key:
        return 0
    current = await _inventory_qty_map(db, brand_name=brand_name, dealer_name=dealer_name, branch=branch, active_date_key=current_date_key)
    previous = await _inventory_qty_map(db, brand_name=brand_name, dealer_name=dealer_name, branch=branch, active_date_key=prev_key)
    verified_monthly = await _verified_parts_this_month(db, brand_name=brand_name, dealer_name=dealer_name, branch=branch, month=month)
    updates = 0
    now = _now()
    all_parts = set(current) | set(previous)
    for part in all_parts:
        cur_q = current.get(part, 0.0)
        prev_q = previous.get(part, 0.0)
        movement = None
        score = 0
        if part not in previous and cur_q > 0:
            movement, score = "new_line", 100
        elif part not in current or cur_q <= 0:
            await db.auto_perpetual_pool.update_one(
                {"month_key": month, "branch": branch, "part_number": part, "coverage_kind": "monthly"},
                {"$set": {"status": "na", "movement_note": "line_removed_or_zero", "updated_at": now}},
            )
            continue
        elif cur_q > prev_q:
            movement, score = "qty_increased", 80
        elif cur_q < prev_q:
            movement, score = "qty_decreased", 70
        if not movement:
            continue
        if part in verified_monthly:
            await db.auto_perpetual_pool.update_one(
                {
                    "month_key": month,
                    "branch": branch,
                    "part_number": part,
                    "coverage_kind": "recheck",
                },
                {
                    "$setOnInsert": {
                        "id": str(uuid.uuid4()),
                        "brand_name": brand_name,
                        "dealer_name": dealer_name,
                        "loc": "",
                        "created_at": now,
                    },
                    "$set": {
                        "system_qty": cur_q,
                        "inventory_date_key": current_date_key,
                        "status": "pending",
                        "movement_reason": movement,
                        "priority_score": score,
                        "updated_at": now,
                    },
                },
                upsert=True,
            )
            updates += 1
        else:
            await db.auto_perpetual_pool.update_one(
                {"month_key": month, "branch": branch, "part_number": part, "coverage_kind": "monthly"},
                {"$set": {"movement_reason": movement, "priority_score": score, "updated_at": now}},
            )
            updates += 1
    return updates


async def sync_monthly_pool(
    db,
    *,
    brand_name: str,
    dealer_name: str,
    branch: str,
    active_date_key: str,
    month: str,
) -> int:
    """Upsert pending pool rows from today's published branch inventory."""
    products = await db.products.find(
        {
            "brand_name": brand_name,
            "dealer_name": dealer_name,
            "branch": branch,
            "publish_status": "Published",
            "is_active_today": True,
            "active_date_key": active_date_key,
        },
        {"_id": 0, "part_number": 1, "location": 1, "loc": 1, "available_qty_number": 1, "quantity": 1},
    ).to_list(50000)

    verified = await _verified_parts_this_month(db, brand_name=brand_name, dealer_name=dealer_name, branch=branch, month=month)
    upserts = 0
    now = _now()
    batch: List[UpdateOne] = []
    batch_parts: List[str] = []

    async def flush_batch():
        nonlocal upserts, batch, batch_parts
        if not batch_parts:
            return
        pri_rows = await db.auto_perpetual_pool.find(
            {
                "month_key": month,
                "branch": branch,
                "coverage_kind": "monthly",
                "part_number": {"$in": batch_parts},
            },
            {"_id": 0, "part_number": 1, "priority_score": 1},
        ).to_list(len(batch_parts))
        pri_map = {str(r["part_number"]).upper(): int(r.get("priority_score") or 0) for r in pri_rows}
        for part in batch_parts:
            pri = pri_map.get(part, 0)
            p = part_docs[part]
            loc = p["loc"]
            qty = p["qty"]
            status = "verified" if part in verified else "pending"
            if qty <= 0:
                status = "na"
            batch.append(
                UpdateOne(
                    {
                        "month_key": month,
                        "branch": branch,
                        "part_number": part,
                        "coverage_kind": "monthly",
                    },
                    {
                        "$setOnInsert": {
                            "id": str(uuid.uuid4()),
                            "brand_name": brand_name,
                            "dealer_name": dealer_name,
                            "loc": loc,
                            "created_at": now,
                        },
                        "$set": {
                            "system_qty": qty,
                            "inventory_date_key": active_date_key,
                            "status": status,
                            "priority_score": pri,
                            "updated_at": now,
                        },
                    },
                    upsert=True,
                )
            )
        if batch:
            await db.auto_perpetual_pool.bulk_write(batch, ordered=False)
            upserts += len(batch)
        batch = []
        batch_parts = []

    part_docs: Dict[str, Dict[str, Any]] = {}
    for p in products:
        part = str(p.get("part_number") or "").strip().upper()
        if not part:
            continue
        loc = str(p.get("loc") or p.get("location") or "").strip()
        qty = float(p.get("available_qty_number") or p.get("quantity") or 0)
        part_docs[part] = {"loc": loc, "qty": qty}
        batch_parts.append(part)
        if len(batch_parts) >= 500:
            await flush_batch()
    await flush_batch()
    return upserts


def _loc_group(loc: str) -> str:
    loc = (loc or "").strip().upper()
    if len(loc) >= 4:
        return loc[:4]
    return loc or "UNKNOWN"


async def _attendance_active_users(
    db, *, brand_name: str, dealer_name: str, branch: str, attendance_date: str
) -> List[dict]:
    users = await db.mobile_users.find(
        {
            "brand_name": brand_name,
            "dealer_name": dealer_name,
            "branch": branch,
            "status": "active",
            "deleted_at": {"$exists": False},
        },
        {"_id": 0},
    ).to_list(500)
    active_ids = set()
    att = await db.mobile_user_attendance.find(
        {"attendance_date": attendance_date, "branch": branch, "status": "active"},
        {"_id": 0, "mobile_user_id": 1},
    ).to_list(500)
    for a in att:
        active_ids.add(a["mobile_user_id"])
    return [u for u in users if u.get("mobile_user_id") in active_ids]


async def generate_auto_perpetual_for_branch(
    db,
    *,
    brand_name: str,
    dealer_name: str,
    branch: str,
    actor_user_id: str,
    recalc_pending: bool = False,
    active_date_key: str,
) -> Dict[str, Any]:
    """Idempotent daily allocation for one branch (IST day)."""
    allocation_date = ist_date_key()
    month = month_key()
    run_key = {
        "allocation_date": allocation_date,
        "branch": branch,
        "brand_name": brand_name,
        "dealer_name": dealer_name,
    }

    existing_run = await db.auto_perpetual_daily_runs.find_one(run_key, {"_id": 0})
    if existing_run and existing_run.get("status") == "completed" and not recalc_pending:
        return {"duplicate": True, "run": existing_run}
    if existing_run and existing_run.get("status") == "generating":
        started = existing_run.get("started_at")
        stale = False
        if isinstance(started, datetime):
            st = started if started.tzinfo else started.replace(tzinfo=timezone.utc)
            stale = (_now() - st.astimezone(timezone.utc)).total_seconds() > 900
        if not stale:
            raise HTTPException(status_code=409, detail="Auto Perpetual generation already in progress for this branch")

    await db.auto_perpetual_daily_runs.update_one(
        run_key,
        {
            "$setOnInsert": {"id": str(uuid.uuid4()), "month_key": month, "created_at": _now()},
            "$set": {"status": "generating", "started_at": _now()},
        },
        upsert=True,
    )

    try:
        sample_pool = await db.auto_perpetual_pool.find_one(
            {"month_key": month, "branch": branch, "brand_name": brand_name, "coverage_kind": "monthly"},
            {"_id": 0, "inventory_date_key": 1},
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
        await apply_stock_movement_priority(
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

        pending_rows = await db.auto_perpetual_pool.find(
            {
                "month_key": month,
                "branch": branch,
                "brand_name": brand_name,
                "dealer_name": dealer_name,
                "coverage_kind": {"$in": ["monthly", "recheck"]},
                "status": "pending",
            },
            {"_id": 0},
        ).to_list(50000)

        pending_rows.sort(
            key=lambda r: (
                -int(r.get("priority_score") or 0),
                _loc_group(r.get("loc", "")),
                r.get("coverage_kind") != "recheck",
                r.get("part_number", ""),
            )
        )

        total_lines = await db.auto_perpetual_pool.count_documents(
            {"month_key": month, "branch": branch, "coverage_kind": "monthly", "status": {"$ne": "na"}}
        )
        verified_count = await db.auto_perpetual_pool.count_documents(
            {"month_key": month, "branch": branch, "coverage_kind": "monthly", "status": "verified"}
        )
        pending_count = len(pending_rows)
        days_left = _working_days_left_in_month(_ist_now())
        n_users = len(active_users)
        baseline = max(1, -(-pending_count // max(1, n_users * days_left)))  # ceil division

        active_ids = {u["mobile_user_id"] for u in active_users}
        all_branch_users = await db.mobile_users.find(
            {
                "brand_name": brand_name,
                "dealer_name": dealer_name,
                "branch": branch,
                "status": "active",
                "deleted_at": {"$exists": False},
            },
            {"_id": 0, "mobile_user_id": 1},
        ).to_list(500)
        inactive_today = await db.mobile_user_attendance.find(
            {"attendance_date": allocation_date, "branch": branch, "status": "inactive"},
            {"_id": 0, "mobile_user_id": 1},
        ).to_list(500)
        inactive_ids = {r["mobile_user_id"] for r in inactive_today}
        catch_up_applied = (existing_run or {}).get("inactive_catch_up_applied") or []
        for u in all_branch_users:
            mu_id = u["mobile_user_id"]
            if mu_id in active_ids or mu_id not in inactive_ids:
                continue
            if mu_id in catch_up_applied:
                continue
            await db.mobile_users.update_one(
                {"mobile_user_id": mu_id},
                {"$inc": {"auto_catch_up_pending": baseline}},
            )
            catch_up_applied.append(mu_id)

        catch_up: Dict[str, int] = {}
        for u in active_users:
            missed = int(u.get("auto_catch_up_pending") or 0)
            catch_up[u["mobile_user_id"]] = min(missed, baseline * 2)

        if recalc_pending:
            await db.auto_perpetual_assignments.delete_many(
                {
                    "allocation_date": allocation_date,
                    "branch": branch,
                    "status": "pending",
                }
            )
            await db.auto_perpetual_pool.update_many(
                {"month_key": month, "branch": branch, "status": "allocated", "allocated_date": allocation_date},
                {"$set": {"status": "pending", "allocated_mobile_user_id": None, "allocated_date": None}},
            )

        assignments_created = 0
        assignments_by_user: Dict[str, int] = {}
        idx = 0
        for u in active_users:
            mu_id = u["mobile_user_id"]
            assignments_by_user[mu_id] = 0
            target = baseline + catch_up.get(mu_id, 0)
            session_id = await get_or_create_auto_daily_session(
                db,
                mobile_user_id=mu_id,
                brand_name=brand_name,
                dealer_name=dealer_name,
                branch=branch,
            )
            assigned_parts: List[str] = []
            while target > 0 and idx < len(pending_rows):
                row = pending_rows[idx]
                idx += 1
                part = row["part_number"]
                cov = row.get("coverage_kind") or "monthly"
                try:
                    await db.auto_perpetual_assignments.insert_one(
                        {
                            "id": str(uuid.uuid4()),
                            "allocation_date": allocation_date,
                            "month_key": month,
                            "brand_name": brand_name,
                            "dealer_name": dealer_name,
                            "branch": branch,
                            "mobile_user_id": mu_id,
                            "part_number": part,
                            "loc": row.get("loc"),
                            "coverage_kind": cov,
                            "session_id": session_id,
                            "status": "pending",
                            "assigned_at": _now(),
                            "assigned_by": actor_user_id,
                        }
                    )
                except DuplicateKeyError:
                    continue
                await db.auto_perpetual_pool.update_one(
                    {
                        "month_key": month,
                        "branch": branch,
                        "part_number": part,
                        "coverage_kind": cov,
                    },
                    {
                        "$set": {
                            "status": "allocated",
                            "allocated_mobile_user_id": mu_id,
                            "allocated_date": allocation_date,
                            "updated_at": _now(),
                        }
                    },
                )
                assigned_parts.append(part)
                assignments_created += 1
                assignments_by_user[mu_id] = assignments_by_user.get(mu_id, 0) + 1
                target -= 1

            if catch_up.get(mu_id, 0) > 0 and assigned_parts:
                await db.mobile_users.update_one(
                    {"mobile_user_id": mu_id},
                    {"$inc": {"auto_catch_up_pending": -min(catch_up[mu_id], len(assigned_parts))}},
                )

        run_doc = {
            **run_key,
            "id": str(uuid.uuid4()),
            "month_key": month,
            "status": "completed",
            "active_users": [u["mobile_user_id"] for u in active_users],
            "assignments_created": assignments_created,
            "baseline_per_user": baseline,
            "pending_at_generation": pending_count,
            "total_lines": total_lines,
            "verified_lines": verified_count,
            "generated_at": _now(),
            "generated_by": actor_user_id,
            "inactive_catch_up_applied": catch_up_applied,
            "assignments_by_user": assignments_by_user,
        }
        await db.auto_perpetual_daily_runs.update_one(run_key, {"$set": run_doc}, upsert=True)

        return {
            "duplicate": False,
            "assignments_created": assignments_created,
            "assignments_by_user": assignments_by_user,
            "baseline_per_user": baseline,
            "active_users": len(active_users),
            "pending_pool": pending_count,
            "coverage_pct": round((verified_count / total_lines) * 100, 2) if total_lines else 0,
            "run": run_doc,
        }
    except Exception:
        await db.auto_perpetual_daily_runs.update_one(run_key, {"$set": {"status": "failed", "failed_at": _now()}}, upsert=True)
        raise


async def branch_monthly_summary(db, *, brand_name: str, dealer_name: str, branch: str) -> Dict[str, Any]:
    month = month_key()
    month_start = datetime(int(month[:4]), int(month[5:7]), 1, tzinfo=IST)
    total = await db.auto_perpetual_pool.count_documents({"month_key": month, "branch": branch, "coverage_kind": "monthly"})
    verified = await db.auto_perpetual_pool.count_documents({"month_key": month, "branch": branch, "coverage_kind": "monthly", "status": "verified"})
    pending = await db.auto_perpetual_pool.count_documents(
        {"month_key": month, "branch": branch, "coverage_kind": "monthly", "status": {"$in": ["pending", "allocated"]}}
    )
    hist_q = {"brand_name": brand_name, "dealer_name": dealer_name, "branch": branch, "verified_at": {"$gte": month_start}}
    physical_count = await db.stock_verification_history.count_documents({**hist_q, "verification_type": "physical"})
    auto_count = await db.stock_verification_history.count_documents({**hist_q, "verification_type": "auto"})
    match_lines = await db.stock_verification_history.count_documents({**hist_q, "quantity_status": "matched", "coverage_kind": {"$ne": "recheck"}})
    shortage_lines = await db.stock_verification_history.count_documents({**hist_q, "quantity_status": "shortage"})
    excess_lines = await db.stock_verification_history.count_documents({**hist_q, "quantity_status": "excess"})
    damage_lines = await db.stock_verification_history.count_documents({**hist_q, "has_damage": True})

    agg = await db.stock_verification_history.aggregate(
        [
            {"$match": hist_q},
            {
                "$group": {
                    "_id": None,
                    "shortage_qty": {"$sum": {"$ifNull": ["$shortage_qty", 0]}},
                    "excess_qty": {"$sum": {"$ifNull": ["$excess_qty", 0]}},
                    "damage_qty": {"$sum": {"$ifNull": ["$damage_qty", 0]}},
                }
            },
        ]
    ).to_list(1)
    sums = agg[0] if agg else {}

    return {
        "month_key": month,
        "total_stock_lines": total,
        "verified_unique_lines": verified,
        "pending_lines": pending,
        "monthly_coverage_pct": round((verified / total) * 100, 2) if total else 0,
        "days_remaining": _working_days_left_in_month(_ist_now()),
        "match_lines": match_lines,
        "shortage_lines": shortage_lines,
        "shortage_qty": round(float(sums.get("shortage_qty") or 0), 2),
        "excess_lines": excess_lines,
        "excess_qty": round(float(sums.get("excess_qty") or 0), 2),
        "damage_lines": damage_lines,
        "damage_qty": round(float(sums.get("damage_qty") or 0), 2),
        "physical_verification_count": physical_count,
        "auto_verification_count": auto_count,
    }


async def user_performance_summary(
    db, *, brand_name: str, dealer_name: str, branch: str, month: Optional[str] = None
) -> List[Dict[str, Any]]:
    month = month or month_key()
    month_start = datetime(int(month[:4]), int(month[5:7]), 1, tzinfo=IST)
    days_left = _working_days_left_in_month(_ist_now())
    total_lines = await db.auto_perpetual_pool.count_documents({"month_key": month, "branch": branch, "coverage_kind": "monthly", "status": {"$ne": "na"}})
    users = await db.mobile_users.find(
        {"brand_name": brand_name, "dealer_name": dealer_name, "branch": branch, "status": "active", "deleted_at": {"$exists": False}},
        {"_id": 0},
    ).to_list(500)
    baseline_monthly = max(1, -(-max(0, total_lines) // max(1, len(users) * max(1, days_left)))) if users else 0
    rows: List[Dict[str, Any]] = []
    for u in users:
        mu = u["mobile_user_id"]
        assigned = await db.auto_perpetual_assignments.count_documents({"month_key": month, "branch": branch, "mobile_user_id": mu})
        completed = await db.auto_perpetual_assignments.count_documents({"month_key": month, "branch": branch, "mobile_user_id": mu, "status": "completed"})
        pending = await db.auto_perpetual_assignments.count_documents({"month_key": month, "branch": branch, "mobile_user_id": mu, "status": "pending"})
        catch_up = int(u.get("auto_catch_up_pending") or 0)
        normal_target = baseline_monthly
        monthly_target = normal_target + catch_up
        completion_pct = round((completed / monthly_target) * 100, 2) if monthly_target else (100.0 if completed else 0.0)
        verified_hist = await db.stock_verification_history.count_documents(
            {"mobile_user_id": mu, "branch": branch, "verified_at": {"$gte": month_start}, "coverage_kind": {"$ne": "recheck"}}
        )
        match_count = await db.stock_verification_history.count_documents(
            {"mobile_user_id": mu, "branch": branch, "verified_at": {"$gte": month_start}, "quantity_status": "matched"}
        )
        shortage_found = await db.stock_verification_history.count_documents(
            {"mobile_user_id": mu, "branch": branch, "verified_at": {"$gte": month_start}, "quantity_status": "shortage"}
        )
        excess_found = await db.stock_verification_history.count_documents(
            {"mobile_user_id": mu, "branch": branch, "verified_at": {"$gte": month_start}, "quantity_status": "excess"}
        )
        damage_found = await db.stock_verification_history.count_documents(
            {"mobile_user_id": mu, "branch": branch, "verified_at": {"$gte": month_start}, "has_damage": True}
        )
        carry_forward = max(0, monthly_target - completed)
        rows.append(
            {
                "mobile_user_id": mu,
                "name": u.get("name"),
                "monthly_target": monthly_target,
                "normal_target": normal_target,
                "catch_up_target": catch_up,
                "assigned": assigned,
                "completed": completed,
                "pending": pending,
                "carry_forward": carry_forward,
                "completion_pct": completion_pct,
                "verified_count": verified_hist,
                "match_count": match_count,
                "shortage_found": shortage_found,
                "excess_found": excess_found,
                "damage_found": damage_found,
            }
        )
    rows.sort(key=lambda r: (-r["completion_pct"], -r["completed"]))
    for i, r in enumerate(rows, start=1):
        r["rank"] = i
    return rows
