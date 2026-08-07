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
from pymongo import ReturnDocument
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
    prefix = f"{month}-"
    rows = await db.stock_verification_history.find(
        {
            "brand_name": brand_name,
            "dealer_name": dealer_name,
            "branch": branch,
            "verification_type": {"$in": ["auto", "AUTO", "auto_perpetual", "physical", "physical_perpetual"]},
            "verified_at": {"$gte": datetime(int(month[:4]), int(month[5:7]), 1, tzinfo=IST)},
        },
        {"_id": 0, "part_number": 1},
    ).to_list(50000)
    return {str(r.get("part_number") or "").strip().upper() for r in rows if r.get("part_number")}


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
    for p in products:
        part = str(p.get("part_number") or "").strip().upper()
        if not part:
            continue
        loc = str(p.get("loc") or p.get("location") or "").strip()
        qty = float(p.get("available_qty_number") or p.get("quantity") or 0)
        status = "verified" if part in verified else "pending"
        if qty <= 0:
            status = "na"
        await db.auto_perpetual_pool.update_one(
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
                    "updated_at": now,
                },
            },
            upsert=True,
        )
        upserts += 1
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

    await sync_monthly_pool(
        db,
        brand_name=brand_name,
        dealer_name=dealer_name,
        branch=branch,
        active_date_key=active_date_key,
        month=month,
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
            "coverage_kind": "monthly",
            "status": "pending",
        },
        {"_id": 0},
    ).to_list(50000)

    pending_rows.sort(key=lambda r: (_loc_group(r.get("loc", "")), r.get("part_number", "")))

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
    idx = 0
    for u in active_users:
        mu_id = u["mobile_user_id"]
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
                    "coverage_kind": "monthly",
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
    }
    await db.auto_perpetual_daily_runs.update_one(run_key, {"$set": run_doc}, upsert=True)

    return {
        "duplicate": False,
        "assignments_created": assignments_created,
        "baseline_per_user": baseline,
        "active_users": len(active_users),
        "pending_pool": pending_count,
        "coverage_pct": round((verified_count / total_lines) * 100, 2) if total_lines else 0,
        "run": run_doc,
    }


async def branch_monthly_summary(db, *, brand_name: str, dealer_name: str, branch: str) -> Dict[str, Any]:
    month = month_key()
    total = await db.auto_perpetual_pool.count_documents({"month_key": month, "branch": branch})
    verified = await db.auto_perpetual_pool.count_documents({"month_key": month, "branch": branch, "status": "verified"})
    pending = await db.auto_perpetual_pool.count_documents({"month_key": month, "branch": branch, "status": {"$in": ["pending", "allocated"]}})
    return {
        "month_key": month,
        "total_stock_lines": total,
        "verified_unique_lines": verified,
        "pending_lines": pending,
        "monthly_coverage_pct": round((verified / total) * 100, 2) if total else 0,
        "days_remaining": _working_days_left_in_month(_ist_now()),
    }
