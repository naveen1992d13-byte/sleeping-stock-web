"""NMTS Analytics APIs — scope-aware dashboards using published Product Hub history."""
from __future__ import annotations

import re
from collections import defaultdict
from datetime import datetime, timedelta, timezone, date
from typing import Any, Dict, List, Optional, Tuple

from fastapi import APIRouter, Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

try:
    from . import reports_center as rc
except ImportError:
    import reports_center as rc

router = APIRouter(prefix="/analytics", tags=["Analytics"])
_security = HTTPBearer()
_AUTH_DEP = None
db = None
UserResponse = None
_nmts_date_key = None
_nmts_now = None

BUCKETS = rc.BUCKETS
# Analytics Stock Aging Analysis uses the final product buckets (distinct from Reports).
ANALYTICS_AGING_BUCKETS = [
    "0–90 Days",
    "91–180 Days",
    "181–270 Days",
    "271–361 Days",
    ">361 Days",
]
_num = rc._num
_text = rc._text
_dt = rc._dt
_bucket = rc._bucket
_aging_days = rc._aging_days
_scope = rc._scope
_query = rc._query
_and = rc._and


def _analytics_bucket(days) -> str:
    """Map aging days into the Analytics stacked-column buckets."""
    if days is None or days == "":
        return ANALYTICS_AGING_BUCKETS[0]
    try:
        d = float(days)
    except (TypeError, ValueError):
        return ANALYTICS_AGING_BUCKETS[0]
    if d <= 90:
        return ANALYTICS_AGING_BUCKETS[0]
    if d <= 180:
        return ANALYTICS_AGING_BUCKETS[1]
    if d <= 270:
        return ANALYTICS_AGING_BUCKETS[2]
    if d <= 361:
        return ANALYTICS_AGING_BUCKETS[3]
    return ANALYTICS_AGING_BUCKETS[4]


async def _current_user(credentials: HTTPAuthorizationCredentials = Depends(_security)):
    if _AUTH_DEP is None:
        raise HTTPException(500, "Analytics authentication is not initialized")
    return await _AUTH_DEP(credentials)


def init_analytics_center(database, get_current_user, user_model, nmts_date_key_fn, nmts_now_fn):
    globals()["db"] = database
    globals()["_AUTH_DEP"] = get_current_user
    globals()["UserResponse"] = user_model
    globals()["_nmts_date_key"] = nmts_date_key_fn
    globals()["_nmts_now"] = nmts_now_fn


def _pct_change(current: float, previous: float) -> float:
    if previous == 0:
        return 0.0 if current == 0 else 100.0
    return ((current - previous) / previous) * 100.0


def _safe_pct(num: float, den: float) -> float:
    if den <= 0:
        return 0.0
    return max(0.0, (num / den) * 100.0)


def _parse_period(from_date: str, to_date: str) -> Tuple[datetime, datetime, List[str]]:
    try:
        start = datetime.strptime(from_date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    except Exception:
        raise HTTPException(400, "Invalid From Date (use YYYY-MM-DD)")
    try:
        end_day = datetime.strptime(to_date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    except Exception:
        raise HTTPException(400, "Invalid To Date (use YYYY-MM-DD)")
    end = end_day + timedelta(days=1)
    if start >= end:
        raise HTTPException(400, "From Date must be on or before To Date")
    keys: List[str] = []
    d = start.date()
    while d < end.date():
        keys.append(d.strftime("%Y%m%d"))
        d += timedelta(days=1)
    return start, end, keys


def _resolve_scope_params(
    current_user,
    brand: Optional[str] = None,
    dealer: Optional[str] = None,
    branch: Optional[str] = None,
    brand_id: Optional[str] = None,
    dealer_id: Optional[str] = None,
    branch_id: Optional[str] = None,
) -> dict:
    b = _text(brand_id, brand)
    d = _text(dealer_id, dealer)
    br = _text(branch_id, branch)
    return _scope(current_user, b or None, d or None, br or None)


def _category_clause(category: Optional[str]) -> Optional[dict]:
    cat = _text(category)
    if not cat or cat.lower() in {"all", "all categories"}:
        return None
    return {
        "$or": [
            {"part_category": {"$regex": f"^{re.escape(cat)}$", "$options": "i"}},
            {"category": {"$regex": f"^{re.escape(cat)}$", "$options": "i"}},
        ]
    }


def _part_key(row: dict) -> Tuple[str, str, str, str]:
    return (
        _text(row.get("brand_name")).casefold(),
        _text(row.get("dealer_name")).casefold(),
        _text(row.get("branch")).casefold(),
        _text(row.get("part_number")).casefold(),
    )


def _row_category(row: dict) -> str:
    return _text(row.get("part_category"), row.get("category"), "Uncategorized") or "Uncategorized"


def _row_qty_unit(row: dict) -> Tuple[float, float]:
    qty = _num(row.get("available_qty_number"), _num(row.get("quantity"), 0))
    unit = _num(
        row.get("unit_value_number"),
        _num(row.get("mav_value"), _num(row.get("unit_value"), _num(row.get("price"), 0))),
    )
    return qty, unit


def _aging_days_row(row: dict, aging_type: str) -> Any:
    pseudo = {
        "purchase_aging_days": row.get("purchase_aging_days"),
        "sales_aging_days": row.get("sales_aging_days"),
        "last_receipt_date": row.get("last_receipt_date"),
        "last_sales_date": row.get("last_sales_date"),
    }
    return _aging_days(pseudo, "purchase" if aging_type == "purchase" else "sales")


async def _aggregate_latest_parts_for_date_keys(
    date_keys: List[str],
    scope: dict,
    category: Optional[str] = None,
) -> Dict[str, Dict[Tuple[str, str, str, str], dict]]:
    """Latest published part rows per date/branch, scoped and de-duplicated.

    Uses `$top` (no separate `$sort`) so Atlas shared-tier 32MB sort limits are
    avoided, and still processes one `active_date_key` at a time for safety.
    """
    if not date_keys:
        return {}
    cat = _category_clause(category)
    by_date: Dict[str, Dict[Tuple[str, str, str, str], dict]] = defaultdict(dict)
    seen = set()
    ordered_keys = []
    for dk in date_keys:
        if dk and dk not in seen:
            seen.add(dk)
            ordered_keys.append(dk)

    for dk in ordered_keys:
        q = _and(_query(scope), {"publish_status": "Published", "active_date_key": dk})
        if cat:
            q = _and(q, cat)
        pipeline = [
            {"$match": q},
            {
                "$project": {
                    "active_date_key": 1,
                    "published_at": 1,
                    "updated_at": 1,
                    "brand_name": 1,
                    "dealer_name": 1,
                    "branch": 1,
                    "part_number": 1,
                    "item_name": 1,
                    "part_name": 1,
                    "part_category": 1,
                    "category": 1,
                    "available_qty_number": 1,
                    "quantity": 1,
                    "unit_value_number": 1,
                    "mav_value": 1,
                    "unit_value": 1,
                    "purchase_aging_days": 1,
                    "sales_aging_days": 1,
                    "last_receipt_date": 1,
                    "last_sales_date": 1,
                }
            },
            {
                "$group": {
                    "_id": {
                        "date": "$active_date_key",
                        "brand": "$brand_name",
                        "dealer": "$dealer_name",
                        "branch": "$branch",
                        "part": "$part_number",
                    },
                    "doc": {
                        "$top": {
                            "output": "$$ROOT",
                            "sortBy": {"published_at": -1, "updated_at": -1},
                        }
                    },
                }
            },
            {
                "$replaceRoot": {
                    "newRoot": {
                        "$mergeObjects": [
                            "$doc",
                            {
                                "qty": {
                                    "$ifNull": [
                                        "$doc.available_qty_number",
                                        {"$ifNull": ["$doc.quantity", 0]},
                                    ]
                                },
                                "unit": {
                                    "$ifNull": [
                                        "$doc.unit_value_number",
                                        {"$ifNull": ["$doc.mav_value", {"$ifNull": ["$doc.unit_value", 0]}]},
                                    ]
                                },
                                "part_name": {
                                    "$ifNull": ["$doc.item_name", {"$ifNull": ["$doc.part_name", ""]}]
                                },
                                "part_category": {
                                    "$ifNull": [
                                        "$doc.part_category",
                                        {"$ifNull": ["$doc.category", "Uncategorized"]},
                                    ]
                                },
                            },
                        ]
                    }
                }
            },
        ]
        rows = await db.products.aggregate(pipeline, allowDiskUse=True).to_list(500000)
        for r in rows:
            row_dk = _text(r.get("active_date_key"))
            pk = (
                _text(r.get("brand_name")).casefold(),
                _text(r.get("dealer_name")).casefold(),
                _text(r.get("branch")).casefold(),
                _text(r.get("part_number")).casefold(),
            )
            qty = _num(r.get("qty"))
            unit = _num(r.get("unit"))
            by_date[row_dk][pk] = {
                "brand_name": r.get("brand_name"),
                "dealer_name": r.get("dealer_name"),
                "branch": r.get("branch"),
                "part_number": r.get("part_number"),
                "part_name": r.get("part_name"),
                "part_category": r.get("part_category"),
                "qty": qty,
                "unit": unit,
                "value": qty * unit,
                "purchase_aging_days": r.get("purchase_aging_days"),
                "sales_aging_days": r.get("sales_aging_days"),
                "last_receipt_date": r.get("last_receipt_date"),
                "last_sales_date": r.get("last_sales_date"),
            }
    return by_date


def _compute_day_movement(
    prev_map: Dict[Tuple[str, str, str, str], dict],
    curr_map: Dict[Tuple[str, str, str, str], dict],
    aging_type: str,
    metric_type: str,
) -> dict:
    added_q = added_v = reduced_q = reduced_v = 0.0
    opening_v = sum(x["value"] for x in prev_map.values())
    closing_v = sum(x["value"] for x in curr_map.values())
    opening_q = sum(x["qty"] for x in prev_map.values())
    closing_q = sum(x["qty"] for x in curr_map.values())
    lines: List[dict] = []
    all_keys = set(prev_map.keys()) | set(curr_map.keys())
    for pk in all_keys:
        prev = prev_map.get(pk, {"qty": 0.0, "unit": 0.0, "value": 0.0})
        curr = curr_map.get(pk, {"qty": 0.0, "unit": 0.0, "value": 0.0})
        p_qty = _num(prev.get("qty"))
        c_qty = _num(curr.get("qty"))
        unit = _num(curr.get("unit"), _num(prev.get("unit")))
        add_q = max(c_qty - p_qty, 0.0)
        red_q = max(p_qty - c_qty, 0.0)
        added_q += add_q
        reduced_q += red_q
        added_v += add_q * unit
        reduced_v += red_q * unit
        if add_q > 0 or red_q > 0:
            src = curr if c_qty >= p_qty else prev
            lines.append(
                {
                    "part_key": pk,
                    "brand_name": src.get("brand_name"),
                    "dealer_name": src.get("dealer_name"),
                    "branch": src.get("branch"),
                    "part_number": src.get("part_number"),
                    "part_name": src.get("part_name"),
                    "category": _row_category(src),
                    "previous_qty": p_qty,
                    "current_qty": c_qty,
                    "added_qty": add_q,
                    "reduced_qty": red_q,
                    "unit_value": unit,
                    "added_value": add_q * unit,
                    "reduced_value": red_q * unit,
                    "aging_days": _aging_days_row(src, aging_type),
                }
            )
    net_v = closing_v - opening_v
    net_q = closing_q - opening_q
    return {
        "opening_value": opening_v,
        "closing_value": closing_v,
        "opening_qty": opening_q,
        "closing_qty": closing_q,
        "added_value": added_v,
        "reduced_value": reduced_v,
        "added_qty": added_q,
        "reduced_qty": reduced_q,
        "net_change_value": net_v,
        "net_change_qty": net_q,
        "change_pct_value": _pct_change(closing_v, opening_v),
        "change_pct_qty": _pct_change(closing_q, opening_q),
        "lines": lines,
        "metric_opening": opening_v if metric_type != "quantity" else opening_q,
        "metric_closing": closing_v if metric_type != "quantity" else closing_q,
        "metric_added": added_v if metric_type != "quantity" else added_q,
        "metric_reduced": reduced_v if metric_type != "quantity" else reduced_q,
        "metric_net": net_v if metric_type != "quantity" else net_q,
    }


def _date_key_to_iso(dk: str) -> str:
    if len(dk) == 8:
        return f"{dk[0:4]}-{dk[4:6]}-{dk[6:8]}"
    return dk


def _prev_date_key(dk: str) -> str:
    d = datetime.strptime(dk, "%Y%m%d").date()
    return (d - timedelta(days=1)).strftime("%Y%m%d")


def _iso_to_date_key(iso: str) -> str:
    return (iso or "").replace("-", "")[:8]


def _nullable_pct(current: Optional[float], previous: Optional[float]) -> Optional[float]:
    if current is None or previous is None:
        return None
    return _pct_change(current, previous)


def _sum_map(part_map: Dict) -> Tuple[float, float]:
    v = sum(_num(x.get("value")) for x in part_map.values())
    q = sum(_num(x.get("qty")) for x in part_map.values())
    return v, q


def _filter_map_branches(part_map: Dict, allowed: Optional[set]) -> Dict:
    if not allowed:
        return {}
    return {k: v for k, v in part_map.items() if k[2] in allowed}


async def _expected_branch_names(scope: dict, current_user) -> List[str]:
    if scope.get("branch"):
        return [_text(scope["branch"])]
    if (current_user.role or "").lower() == "user":
        return [_text(current_user.location)]
    q: dict = {"$or": [{"status": "active"}, {"status": {"$exists": False}}, {"status": None}, {"status": ""}]}
    if scope.get("brand"):
        rx = {"$regex": f"^{re.escape(scope['brand'])}$", "$options": "i"}
        q = _and(q, {"$or": [{"brand": rx}, {"brand_name": rx}, {"brandName": rx}]})
    if scope.get("dealer"):
        rx = {"$regex": f"^{re.escape(scope['dealer'])}$", "$options": "i"}
        q = _and(q, {"$or": [{"dealer": rx}, {"dealer_name": rx}, {"dealerName": rx}]})
    rows = await db.branches.find(q, {"_id": 0, "name": 1}).sort("name", 1).to_list(5000)
    names = sorted({_text(r.get("name")) for r in rows if _text(r.get("name"))})
    if names:
        return names
    if (current_user.role or "").lower() == "admin":
        return [_text(current_user.location)] if _text(current_user.location) else []
    return []


async def _uploaded_branches_by_date(scope: dict, date_keys: List[str]) -> Tuple[Dict[str, set], Dict[str, Dict[str, dict]]]:
    """Branch-day upload detection from batch_summaries with published-products fallback."""
    uploaded: Dict[str, set] = defaultdict(set)
    meta: Dict[str, Dict[str, dict]] = defaultdict(dict)
    if not date_keys:
        return uploaded, meta
    q = _and(_query(scope), {"active_date_key": {"$in": date_keys}})
    for row in await db.batch_summaries.find(q, {"_id": 0}).to_list(50000):
        dk = _text(row.get("active_date_key"))
        br = _text(row.get("branch"))
        if not dk or not br:
            continue
        uploaded[dk].add(br.casefold())
        meta[dk][br.casefold()] = {
            "branch": br,
            "published_at": row.get("published_at"),
            "total_value": _num(row.get("total_value")),
        }
    pipeline = [
        {"$match": _and(_query(scope), {"publish_status": "Published", "active_date_key": {"$in": date_keys}})},
        {"$group": {"_id": {"date": "$active_date_key", "branch": "$branch"}, "rows": {"$sum": 1}}},
    ]
    for row in await db.products.aggregate(pipeline, allowDiskUse=True).to_list(50000):
        dk = _text(row.get("_id", {}).get("date"))
        br = _text(row.get("_id", {}).get("branch"))
        if dk and br:
            uploaded[dk].add(br.casefold())
            meta[dk].setdefault(
                br.casefold(),
                {"branch": br, "published_at": None, "total_value": None},
            )
    return uploaded, meta


async def _snapshots_enabled() -> bool:
    try:
        return await db.analytics_stock_daily_snapshots.estimated_document_count() > 0
    except Exception:
        return False


async def _aggregate_parts_for_dates(
    date_keys: List[str],
    scope: dict,
    category: Optional[str],
    use_snapshots: bool,
) -> Dict[str, Dict[Tuple[str, str, str, str], dict]]:
    if not date_keys:
        return {}
    if use_snapshots:
        snap_keys = []
        for dk in date_keys:
            snap_keys.append(dk)
            if len(dk) == 8:
                snap_keys.append(f"{dk[0:4]}-{dk[4:6]}-{dk[6:8]}")
        q = {"snapshot_date_ist": {"$in": list(set(snap_keys))}}
        if scope.get("brand"):
            q["brand_name"] = scope["brand"]
        if scope.get("dealer"):
            q["dealer_name"] = scope["dealer"]
        if scope.get("branch"):
            q["branch_name"] = scope["branch"]
        rows = await db.analytics_stock_daily_snapshots.find(q, {"_id": 0}).to_list(500000)
        if rows:
            by_date: Dict[str, Dict[Tuple[str, str, str, str], dict]] = defaultdict(dict)
            for r in rows:
                raw = _text(r.get("snapshot_date_ist")).replace("-", "")[:8]
                pk = (
                    _text(r.get("brand_name")).casefold(),
                    _text(r.get("dealer_name")).casefold(),
                    _text(r.get("branch_name")).casefold(),
                    _text(r.get("part_number")).casefold(),
                )
                qty = _num(r.get("available_qty"))
                unit = _num(r.get("unit_value"))
                by_date[raw][pk] = {
                    "brand_name": r.get("brand_name"),
                    "dealer_name": r.get("dealer_name"),
                    "branch": r.get("branch_name"),
                    "part_number": r.get("part_number"),
                    "part_name": r.get("part_name"),
                    "part_category": r.get("category"),
                    "qty": qty,
                    "unit": unit,
                    "value": _num(r.get("total_value"), qty * unit),
                }
            return by_date
    return await _aggregate_latest_parts_for_date_keys(date_keys, scope, category)


def _day_upload_status(
    dk: str,
    uploaded_by_date: Dict[str, set],
    expected_branches: List[str],
    consolidated: bool,
) -> dict:
    expected = len(expected_branches)
    exp_cf = {_text(b).casefold() for b in expected_branches}
    up = uploaded_by_date.get(dk, set())
    # When branch master list is empty, fall back to observed upload branches so
    # All Dealers / All Branches consolidation still surfaces available stock.
    up_in_scope = up & exp_cf if exp_cf else set(up)
    uploaded_count = len(up_in_scope)
    missing = sorted([b for b in expected_branches if b.casefold() not in up_in_scope])
    if expected_branches:
        uploaded_names = sorted([b for b in expected_branches if b.casefold() in up_in_scope])
    else:
        uploaded_names = sorted(up_in_scope)
    if uploaded_count == 0:
        status = "NO_UPLOAD"
    elif not consolidated:
        status = "AVAILABLE"
    elif expected == 0:
        # No branch master rows — treat observed uploads as complete for the day.
        status = "AVAILABLE"
        expected = uploaded_count
    elif uploaded_count >= expected:
        status = "AVAILABLE"
    else:
        status = "PARTIAL_UPLOAD"
    cov = _safe_pct(uploaded_count, expected) if expected else 0.0
    return {
        "data_status": status,
        "uploaded_branch_count": uploaded_count,
        "expected_branch_count": expected,
        "missing_branch_count": max(0, expected - uploaded_count),
        "coverage_percentage": cov,
        "uploaded_branches": uploaded_names,
        "missing_branches": missing,
        "allowed_branch_keys": up_in_scope,
    }


async def _find_last_upload_before(before_dk: str, scope: dict, expected_branches: List[str], consolidated: bool) -> Optional[str]:
    q = _and(_query(scope), {"active_date_key": {"$lt": before_dk}})
    keys = sorted(set(await db.batch_summaries.distinct("active_date_key", q)), reverse=True)
    if not keys:
        pipeline = [
            {"$match": _and(_query(scope), {"publish_status": "Published", "active_date_key": {"$lt": before_dk}})},
            {"$group": {"_id": "$active_date_key"}},
        ]
        keys = sorted([r["_id"] for r in await db.products.aggregate(pipeline, allowDiskUse=True).to_list(5000)], reverse=True)
    for dk in keys:
        up, _ = await _uploaded_branches_by_date(scope, [dk])
        st = _day_upload_status(dk, up, expected_branches, consolidated)
        if st["data_status"] in {"AVAILABLE", "PARTIAL_UPLOAD"}:
            return dk
    return None


def _comparison_type_for(prev_dk: Optional[str], curr_dk: str) -> str:
    if not prev_dk:
        return "NO_PREVIOUS_UPLOAD"
    if prev_dk == _prev_date_key(curr_dk):
        return "PREVIOUS_DAY"
    return "LAST_AVAILABLE_UPLOAD"


def _comparison_label(comparison_type: str) -> str:
    return {
        "PREVIOUS_DAY": "Previous Day",
        "LAST_AVAILABLE_UPLOAD": "Last Upload",
        "NO_PREVIOUS_UPLOAD": "No Previous Upload",
    }.get(comparison_type, "No Previous Upload")


def _empty_daily_row(dk: str, status_info: dict) -> dict:
    return {
        "date": _date_key_to_iso(dk),
        "data_status": status_info["data_status"],
        "stock_value": None,
        "stock_quantity": None,
        "added_value": None,
        "reduced_value": None,
        "net_change": None,
        "change_pct": None,
        "comparison_date": None,
        "comparison_type": None,
        "comparison_label": None,
        "uploaded_branch_count": status_info["uploaded_branch_count"],
        "expected_branch_count": status_info["expected_branch_count"],
        "missing_branch_count": status_info["missing_branch_count"],
        "coverage_percentage": status_info["coverage_percentage"],
        "uploaded_branches": status_info["uploaded_branches"],
        "missing_branches": status_info["missing_branches"],
    }


async def _build_daily_stock_series(
    scope: dict,
    date_keys: List[str],
    category: Optional[str],
    aging_type: str,
    metric_type: str,
    current_user,
) -> dict:
    expected_branches = await _expected_branch_names(scope, current_user)
    consolidated = not bool(scope.get("branch"))
    uploaded_by_date, upload_meta = await _uploaded_branches_by_date(scope, date_keys)
    use_snap = await _snapshots_enabled()
    prior_key = await _find_last_upload_before(date_keys[0], scope, expected_branches, consolidated)
    load_keys = list(date_keys)
    if prior_key and prior_key not in load_keys:
        load_keys.append(prior_key)
    if prior_key and prior_key not in load_keys:
        load_keys.append(prior_key)
    by_date = await _aggregate_parts_for_dates(load_keys, scope, category, use_snap)
    if prior_key:
        up_prior, meta_prior = await _uploaded_branches_by_date(scope, [prior_key])
        for dk, branches in up_prior.items():
            uploaded_by_date[dk] = uploaded_by_date.get(dk, set()) | branches
        for dk, m in meta_prior.items():
            upload_meta[dk] = {**upload_meta.get(dk, {}), **m}

    last_avail_key: Optional[str] = prior_key
    if prior_key:
        prior_status = _day_upload_status(prior_key, uploaded_by_date, expected_branches, consolidated)
        last_avail_map = _filter_map_branches(by_date.get(prior_key, {}), prior_status["allowed_branch_keys"])
    else:
        last_avail_map = {}

    daily: List[dict] = []
    full_days = partial_days = no_days = 0
    uploaded_branch_days = 0
    expected_branch_days = len(date_keys) * max(len(expected_branches), 1 if not consolidated else len(expected_branches) or 1)

    period_added = period_reduced = 0.0
    for dk in date_keys:
        status_info = _day_upload_status(dk, uploaded_by_date, expected_branches, consolidated)
        if status_info["data_status"] == "NO_UPLOAD":
            no_days += 1
            daily.append(_empty_daily_row(dk, status_info))
            continue
        if status_info["data_status"] == "PARTIAL_UPLOAD":
            partial_days += 1
        else:
            full_days += 1
        uploaded_branch_days += status_info["uploaded_branch_count"]
        curr_map = _filter_map_branches(by_date.get(dk, {}), status_info["allowed_branch_keys"])
        stock_v, stock_q = _sum_map(curr_map)
        row = _empty_daily_row(dk, status_info)
        row["stock_value"] = stock_v
        row["stock_quantity"] = stock_q
        if last_avail_key and last_avail_map is not None:
            mv = _compute_day_movement(last_avail_map, curr_map, aging_type, metric_type)
            ctype = _comparison_type_for(last_avail_key, dk)
            row["comparison_date"] = _date_key_to_iso(last_avail_key)
            row["comparison_type"] = ctype
            row["comparison_label"] = _comparison_label(ctype)
            row["added_value"] = mv["added_value"]
            row["reduced_value"] = mv["reduced_value"]
            row["net_change"] = mv["net_change_value"]
            row["added_quantity"] = mv["added_qty"]
            row["reduced_quantity"] = mv["reduced_qty"]
            row["net_change_quantity"] = mv["net_change_qty"]
            row["change_pct"] = _nullable_pct(stock_v, mv["opening_value"])
            period_added += mv["added_value"]
            period_reduced += mv["reduced_value"]
        else:
            row["comparison_type"] = "NO_PREVIOUS_UPLOAD"
            row["comparison_label"] = _comparison_label("NO_PREVIOUS_UPLOAD")
        daily.append(row)
        last_avail_key = dk
        last_avail_map = curr_map

    if not consolidated:
        coverage_pct = _safe_pct(full_days, len(date_keys)) if date_keys else 0.0
    else:
        coverage_pct = _safe_pct(uploaded_branch_days, expected_branch_days) if expected_branch_days else 0.0

    coverage = {
        "total_calendar_days": len(date_keys),
        "full_upload_days": full_days,
        "partial_upload_days": partial_days,
        "no_upload_days": no_days,
        "coverage_percentage": coverage_pct,
        "uploaded_branch_day_records": uploaded_branch_days,
        "expected_branch_day_records": expected_branch_days,
    }

    last_row = daily[-1] if daily else None
    last_avail = None
    for r in reversed(daily):
        if r["data_status"] in {"AVAILABLE", "PARTIAL_UPLOAD"}:
            last_avail = r
            break

    summary = {
        "data_status": last_row["data_status"] if last_row else "NO_UPLOAD",
        "current_stock_value": last_row["stock_value"] if last_row and last_row["data_status"] != "NO_UPLOAD" else None,
        "current_stock_quantity": last_row["stock_quantity"] if last_row and last_row["data_status"] != "NO_UPLOAD" else None,
        "previous_available_stock_value": None,
        "stock_added_value": last_row["added_value"] if last_row else None,
        "stock_reduced_value": last_row["reduced_value"] if last_row else None,
        "net_change_value": last_row["net_change"] if last_row else None,
        "change_pct_value": last_row["change_pct"] if last_row else None,
        "comparison_date": last_row.get("comparison_date") if last_row else None,
        "comparison_type": last_row.get("comparison_type") if last_row else None,
        "comparison_label": last_row.get("comparison_label") if last_row else None,
        "last_available_upload_date": last_avail["date"] if last_avail else None,
        "last_available_stock_value": last_avail["stock_value"] if last_avail else None,
        "period_added_value": period_added if last_row and last_row["data_status"] != "NO_UPLOAD" else None,
        "period_reduced_value": period_reduced if last_row and last_row["data_status"] != "NO_UPLOAD" else None,
    }
    if last_row:
        for k in ("uploaded_branch_count", "expected_branch_count", "missing_branch_count", "coverage_percentage"):
            summary[k] = last_row.get(k)
    if last_row and last_row.get("comparison_date") and last_row["stock_value"] is not None:
        # previous available from comparison row
        comp_key = _iso_to_date_key(last_row["comparison_date"])
        comp_map = _filter_map_branches(by_date.get(comp_key, {}), _day_upload_status(comp_key, uploaded_by_date, expected_branches, consolidated)["allowed_branch_keys"])
        summary["previous_available_stock_value"], _ = _sum_map(comp_map)

    return {
        "daily": daily,
        "coverage": coverage,
        "summary": summary,
        "by_date_parts": by_date,
        "upload_meta": upload_meta,
        "uploaded_by_date": uploaded_by_date,
        "expected_branches": expected_branches,
        "consolidated": consolidated,
    }


async def _orders_query(scope: dict, start: datetime, end: datetime):
    q = _and(_query(scope, ("brand_name", "dealer_name", "branch")), rc._date_clause(["created_at"], start, end))
    return await db.order_headers.find(q, {"_id": 0}).to_list(200000)


async def _requests_query(scope: dict, start: datetime, end: datetime, direction: str = "raised"):
    dateq = rc._date_clause(["requested_at", "created_at"], start, end)
    side = []
    if direction == "received":
        if scope.get("brand"):
            side.append({"supplying_brand": scope["brand"]})
        if scope.get("dealer"):
            side.append({"supplying_dealer": scope["dealer"]})
        if scope.get("branch"):
            side.append({"supplying_branch": scope["branch"]})
    else:
        if scope.get("brand"):
            side.append({"requesting_brand": scope["brand"]})
        if scope.get("dealer"):
            side.append({"requesting_dealer": scope["dealer"]})
        if scope.get("branch"):
            side.append({"requesting_branch": scope["branch"]})
    return await db.order_requests.find(_and(dateq, *side), {"_id": 0}).to_list(500000)


def _request_unit(row: dict) -> float:
    return _num(row.get("part_value"), _num(row.get("unit_value_at_request"), _num(row.get("unit_value"), 0)))


def _approved_qty(row: dict) -> float:
    return _num(row.get("accepted_qty"), _num(row.get("approved_qty"), 0))


def _requested_qty(row: dict) -> float:
    return _num(row.get("requested_qty"), _num(row.get("quantity"), 0))


def _is_completed_request(row: dict) -> bool:
    return _text(row.get("status")).lower() == "completed"


def _nmts_sourced_qty_value(row: dict) -> Tuple[float, float]:
    if not _is_completed_request(row):
        return 0.0, 0.0
    req_b = _text(row.get("requesting_branch")).casefold()
    sup_b = _text(row.get("supplying_branch")).casefold()
    if req_b and sup_b and req_b == sup_b:
        return 0.0, 0.0
    qty = _num(row.get("completed_qty"), _approved_qty(row))
    if qty <= 0:
        return 0.0, 0.0
    unit = _request_unit(row)
    return qty, qty * unit


def _common_params(
    from_date: str,
    to_date: str,
    brand: Optional[str] = None,
    dealer: Optional[str] = None,
    branch: Optional[str] = None,
    brand_id: Optional[str] = None,
    dealer_id: Optional[str] = None,
    branch_id: Optional[str] = None,
    category: Optional[str] = None,
    aging_type: str = "purchase",
    metric_type: str = "value",
):
    aging_type = (aging_type or "purchase").lower()
    if aging_type not in {"purchase", "sales"}:
        raise HTTPException(400, "aging_type must be purchase or sales")
    metric_type = (metric_type or "value").lower()
    if metric_type not in {"value", "quantity"}:
        raise HTTPException(400, "metric_type must be value or quantity")
    start, end, date_keys = _parse_period(from_date, to_date)
    return start, end, date_keys, aging_type, metric_type, category


@router.get("/categories")
async def analytics_categories(
    from_date: str,
    to_date: str,
    brand: Optional[str] = None,
    dealer: Optional[str] = None,
    branch: Optional[str] = None,
    brand_id: Optional[str] = None,
    dealer_id: Optional[str] = None,
    branch_id: Optional[str] = None,
    current_user=Depends(_current_user),
):
    scope = _resolve_scope_params(current_user, brand, dealer, branch, brand_id, dealer_id, branch_id)
    _, _, date_keys, _, _, _ = _common_params(from_date, to_date)
    if not date_keys:
        return {"categories": []}
    ctx = await _build_daily_stock_series(scope, date_keys, None, "purchase", "value", current_user)
    last_avail = None
    for row in reversed(ctx["daily"]):
        if row["data_status"] in {"AVAILABLE", "PARTIAL_UPLOAD"}:
            last_avail = row
            break
    if not last_avail:
        return {"categories": []}
    dk = _iso_to_date_key(last_avail["date"])
    status = _day_upload_status(dk, ctx["uploaded_by_date"], ctx["expected_branches"], ctx["consolidated"])
    part_map = _filter_map_branches(ctx["by_date_parts"].get(dk, {}), status["allowed_branch_keys"])
    cats = sorted({_row_category(v) for v in part_map.values()})
    return {"categories": cats}


@router.get("/overall")
async def analytics_overall(
    from_date: str,
    to_date: str,
    brand: Optional[str] = None,
    dealer: Optional[str] = None,
    branch: Optional[str] = None,
    brand_id: Optional[str] = None,
    dealer_id: Optional[str] = None,
    branch_id: Optional[str] = None,
    category: Optional[str] = None,
    aging_type: str = "purchase",
    metric_type: str = "value",
    current_user=Depends(_current_user),
):
    scope = _resolve_scope_params(current_user, brand, dealer, branch, brand_id, dealer_id, branch_id)
    start, end, date_keys, aging_type, metric_type, category = _common_params(
        from_date, to_date, category=category, aging_type=aging_type, metric_type=metric_type
    )
    ctx = await _build_daily_stock_series(scope, date_keys, category, aging_type, metric_type, current_user)
    daily = ctx["daily"]
    stk = ctx["summary"]

    orders = await _orders_query(scope, start, end)
    reqs = await _requests_query(scope, start, end, "raised")
    order_value = sum(_num(o.get("total_order_value")) for o in orders)
    nmts_v = 0.0
    order_ids = {o.get("id") for o in orders}
    for r in reqs:
        if r.get("order_id") not in order_ids:
            continue
        _, sv = _nmts_sourced_qty_value(r)
        nmts_v += sv
    requested_v = sum(_request_unit(r) * _requested_qty(r) for r in reqs)
    accepted_v = sum(_request_unit(r) * _approved_qty(r) for r in reqs)

    daily_series = []
    for pt in daily:
        d_iso = pt["date"]
        day_orders = sum(_num(o.get("total_order_value")) for o in orders if (_dt(o.get("created_at")) or start).date().isoformat() == d_iso)
        day_reqs = [r for r in reqs if (_dt(r.get("requested_at") or r.get("created_at")) or start).date().isoformat() == d_iso]
        closing = pt["stock_value"] if pt["data_status"] != "NO_UPLOAD" else None
        daily_series.append(
            {
                **pt,
                "closing_stock_value": closing,
                "closing_stock_qty": pt["stock_quantity"],
                "order_value": day_orders,
                "accepted_request_value": sum(_request_unit(r) * _approved_qty(r) for r in day_reqs),
            }
        )

    return {
        "scope": scope,
        "data_coverage": ctx["coverage"],
        "summary": {
            "current_stock_value": stk.get("current_stock_value"),
            "current_stock_quantity": stk.get("current_stock_quantity"),
            "previous_stock_value": stk.get("previous_available_stock_value"),
            "stock_added_value": stk.get("stock_added_value"),
            "stock_reduced_value": stk.get("stock_reduced_value"),
            "net_change_value": stk.get("net_change_value"),
            "change_pct_value": stk.get("change_pct_value"),
            "comparison_date": stk.get("comparison_date"),
            "comparison_type": stk.get("comparison_type"),
            "comparison_label": stk.get("comparison_label"),
            "data_status": stk.get("data_status"),
            "last_available_upload_date": stk.get("last_available_upload_date"),
            "last_available_stock_value": stk.get("last_available_stock_value"),
            "total_order_value": order_value,
            "nmts_sourced_value": nmts_v,
            "total_requested_value": requested_v,
            "total_accepted_value": accepted_v,
            "uploaded_branch_count": stk.get("uploaded_branch_count"),
            "expected_branch_count": stk.get("expected_branch_count"),
            "missing_branch_count": stk.get("missing_branch_count"),
            "coverage_percentage": stk.get("coverage_percentage"),
        },
        "series": daily_series,
    }


@router.get("/stock-trend")
async def analytics_stock_trend(
    from_date: str,
    to_date: str,
    brand: Optional[str] = None,
    dealer: Optional[str] = None,
    branch: Optional[str] = None,
    brand_id: Optional[str] = None,
    dealer_id: Optional[str] = None,
    branch_id: Optional[str] = None,
    category: Optional[str] = None,
    aging_type: str = "purchase",
    metric_type: str = "value",
    current_user=Depends(_current_user),
):
    scope = _resolve_scope_params(current_user, brand, dealer, branch, brand_id, dealer_id, branch_id)
    _, _, date_keys, aging_type, metric_type, category = _common_params(
        from_date, to_date, category=category, aging_type=aging_type, metric_type=metric_type
    )
    payload = await _stock_trend_payload(scope, date_keys, aging_type, metric_type, category, current_user)
    return payload


@router.get("/category-trend")
async def analytics_category_trend(
    from_date: str,
    to_date: str,
    brand: Optional[str] = None,
    dealer: Optional[str] = None,
    branch: Optional[str] = None,
    brand_id: Optional[str] = None,
    dealer_id: Optional[str] = None,
    branch_id: Optional[str] = None,
    category: Optional[str] = None,
    aging_type: str = "purchase",
    metric_type: str = "value",
    current_user=Depends(_current_user),
):
    scope = _resolve_scope_params(current_user, brand, dealer, branch, brand_id, dealer_id, branch_id)
    _, _, date_keys, aging_type, metric_type, category = _common_params(
        from_date, to_date, category=category, aging_type=aging_type, metric_type=metric_type
    )
    ctx = await _build_daily_stock_series(scope, date_keys, category, aging_type, metric_type, current_user)
    use_value = metric_type == "value"
    last_avail_key = None
    last_avail_map: Dict = {}
    cats: Dict[str, dict] = defaultdict(lambda: {"current_value": None, "current_qty": None, "added_value": None, "reduced_value": None, "daily": []})
    for pt in ctx["daily"]:
        dk = _iso_to_date_key(pt["date"])
        if pt["data_status"] == "NO_UPLOAD":
            for c in cats:
                cats[c]["daily"].append({"date": pt["date"], "data_status": "NO_UPLOAD", "closing": None, "added": None, "reduced": None})
            continue
        status = _day_upload_status(dk, ctx["uploaded_by_date"], ctx["expected_branches"], ctx["consolidated"])
        curr_map = _filter_map_branches(ctx["by_date_parts"].get(dk, {}), status["allowed_branch_keys"])
        per_cat = defaultdict(lambda: {"added": None, "reduced": None, "closing": None, "data_status": pt["data_status"]})
        for row in curr_map.values():
            c = _row_category(row)
            if category and c.lower() != category.lower():
                continue
            per_cat[c]["closing"] = (per_cat[c]["closing"] or 0) + (row["value"] if use_value else row["qty"])
        if last_avail_key and last_avail_map:
            mv = _compute_day_movement(last_avail_map, curr_map, aging_type, metric_type)
            for line in mv["lines"]:
                c = line["category"]
                if category and c.lower() != category.lower():
                    continue
                per_cat[c]["added"] = (per_cat[c]["added"] or 0) + (line["added_value"] if use_value else line["added_qty"])
                per_cat[c]["reduced"] = (per_cat[c]["reduced"] or 0) + (line["reduced_value"] if use_value else line["reduced_qty"])
        for c, vals in per_cat.items():
            cats[c]["daily"].append({"date": pt["date"], **vals, "comparison_type": pt.get("comparison_type")})
            if pt["date"] == ctx["daily"][-1]["date"]:
                cats[c]["current_value"] = vals["closing"] if use_value else vals["closing"]
                cats[c]["current_qty"] = vals["closing"] if not use_value else None
            if vals.get("added") is not None:
                cats[c]["added_value"] = (cats[c]["added_value"] or 0) + vals["added"]
            if vals.get("reduced") is not None:
                cats[c]["reduced_value"] = (cats[c]["reduced_value"] or 0) + vals["reduced"]
        last_avail_key = dk
        last_avail_map = curr_map
    out = []
    for name, data in sorted(cats.items()):
        if category and name.lower() != category.lower():
            continue
        cv = data["current_value"]
        net = None
        if data["added_value"] is not None and data["reduced_value"] is not None:
            net = data["added_value"] - data["reduced_value"]
        out.append(
            {
                "category": name,
                "current_value": data["current_value"],
                "current_qty": data["current_qty"],
                "added_value": data["added_value"],
                "reduced_value": data["reduced_value"],
                "net_change": net,
                "data_status": ctx["summary"].get("data_status"),
                "comparison_type": ctx["summary"].get("comparison_type"),
                "comparison_label": ctx["summary"].get("comparison_label"),
                "daily": data["daily"],
            }
        )
    return {"categories": out, "data_coverage": ctx["coverage"]}


@router.get("/aging-trend")
async def analytics_aging_trend(
    from_date: str,
    to_date: str,
    brand: Optional[str] = None,
    dealer: Optional[str] = None,
    branch: Optional[str] = None,
    brand_id: Optional[str] = None,
    dealer_id: Optional[str] = None,
    branch_id: Optional[str] = None,
    category: Optional[str] = None,
    aging_type: str = "purchase",
    metric_type: str = "value",
    current_user=Depends(_current_user),
):
    scope = _resolve_scope_params(current_user, brand, dealer, branch, brand_id, dealer_id, branch_id)
    _, _, date_keys, aging_type, metric_type, category = _common_params(
        from_date, to_date, category=category, aging_type=aging_type, metric_type=metric_type
    )
    if not date_keys:
        return {
            "buckets": [],
            "bucket_keys": list(ANALYTICS_AGING_BUCKETS),
            "stacked": [],
            "daily": [],
            "data_coverage": {},
        }
    ctx = await _build_daily_stock_series(scope, date_keys, category, aging_type, metric_type, current_user)
    use_value = metric_type == "value"
    # Item Wise = distinct part-line count (quantity of line items), Value Wise = stock value.
    use_items = metric_type == "quantity"
    last_row = ctx["daily"][-1]
    bucket_totals = {b: {"value": None, "quantity": None, "items": None} for b in ANALYTICS_AGING_BUCKETS}
    stacked = defaultdict(lambda: {b: None for b in ANALYTICS_AGING_BUCKETS})
    if last_row["data_status"] != "NO_UPLOAD":
        dk = _iso_to_date_key(last_row["date"])
        status = _day_upload_status(dk, ctx["uploaded_by_date"], ctx["expected_branches"], ctx["consolidated"])
        curr_map = _filter_map_branches(ctx["by_date_parts"].get(dk, {}), status["allowed_branch_keys"])
        bucket_totals = {b: {"value": 0.0, "quantity": 0.0, "items": 0.0} for b in ANALYTICS_AGING_BUCKETS}
        stacked = defaultdict(lambda: {b: 0.0 for b in ANALYTICS_AGING_BUCKETS})
        for row in curr_map.values():
            days = _aging_days_row(row, aging_type)
            b = _analytics_bucket(days)
            bucket_totals[b]["value"] += row["value"]
            bucket_totals[b]["quantity"] += row["qty"]
            bucket_totals[b]["items"] += 1.0
            cat = _row_category(row)
            metric = row["value"] if use_value else (1.0 if use_items else row["qty"])
            stacked[cat][b] += metric
        cat_total = sum(
            (x["value"] if use_value else 1.0) for x in curr_map.values()
        ) or 1.0
    else:
        cat_total = 1.0
    buckets_out = []
    for b in ANALYTICS_AGING_BUCKETS:
        v = bucket_totals[b]["value"]
        q = bucket_totals[b]["quantity"]
        items = bucket_totals[b]["items"]
        m = v if use_value else items
        buckets_out.append(
            {
                "bucket": b,
                "value": v,
                "quantity": q,
                "items": items,
                "metric": m,
                "pct_of_total": _safe_pct(m, cat_total) if m is not None else None,
                "data_status": last_row["data_status"],
            }
        )
    stacked_out = [
        {"category": c, **{b: stacked[c][b] for b in ANALYTICS_AGING_BUCKETS}}
        for c in sorted(stacked.keys())
    ]
    daily = []
    for pt in ctx["daily"]:
        if pt["data_status"] == "NO_UPLOAD":
            daily.append(
                {
                    "date": pt["date"],
                    "data_status": "NO_UPLOAD",
                    "total": None,
                    **{b: None for b in ANALYTICS_AGING_BUCKETS},
                }
            )
            continue
        dk = _iso_to_date_key(pt["date"])
        status = _day_upload_status(dk, ctx["uploaded_by_date"], ctx["expected_branches"], ctx["consolidated"])
        curr_map = _filter_map_branches(ctx["by_date_parts"].get(dk, {}), status["allowed_branch_keys"])
        day_buckets = {b: 0.0 for b in ANALYTICS_AGING_BUCKETS}
        for row in curr_map.values():
            b = _analytics_bucket(_aging_days_row(row, aging_type))
            day_buckets[b] += row["value"] if use_value else 1.0
        total = sum(day_buckets.values())
        daily.append(
            {
                "date": pt["date"],
                "data_status": pt["data_status"],
                "total": total,
                **{b: day_buckets[b] for b in ANALYTICS_AGING_BUCKETS},
            }
        )
    return {
        "buckets": buckets_out,
        "bucket_keys": list(ANALYTICS_AGING_BUCKETS),
        "stacked": stacked_out,
        "daily": daily,
        "data_coverage": ctx["coverage"],
        "comparison_type": ctx["summary"].get("comparison_type"),
        "comparison_label": ctx["summary"].get("comparison_label"),
        "metric_type": metric_type,
    }


@router.get("/order-saving")
async def analytics_order_saving(
    from_date: str,
    to_date: str,
    brand: Optional[str] = None,
    dealer: Optional[str] = None,
    branch: Optional[str] = None,
    brand_id: Optional[str] = None,
    dealer_id: Optional[str] = None,
    branch_id: Optional[str] = None,
    category: Optional[str] = None,
    metric_type: str = "value",
    current_user=Depends(_current_user),
):
    """Orders & Savings Analysis.

    Final / Net Order = Original Order - Reduced / Cut (NMTS-sourced network supply).
    """
    scope = _resolve_scope_params(current_user, brand, dealer, branch, brand_id, dealer_id, branch_id)
    start, end, date_keys, _, metric_type, _ = _common_params(from_date, to_date, metric_type=metric_type)
    orders = await _orders_query(scope, start, end)
    order_ids = {o.get("id") for o in orders if o.get("id")}
    reqs = await _requests_query(scope, start, end, "raised")
    linked = [r for r in reqs if r.get("order_id") in order_ids]

    original_value = sum(_num(o.get("total_order_value")) for o in orders)
    original_items = sum(_num(o.get("item_count"), _num(o.get("total_required_qty"), 0)) for o in orders)
    reduced_value = 0.0
    reduced_items = 0.0
    for r in linked:
        sq, sv = _nmts_sourced_qty_value(r)
        reduced_value += sv
        reduced_items += sq
    # Cap reduced so Final never goes negative from noisy source rows.
    reduced_value = min(reduced_value, original_value) if original_value > 0 else reduced_value
    reduced_items = min(reduced_items, original_items) if original_items > 0 else reduced_items
    final_value = max(0.0, original_value - reduced_value)
    final_items = max(0.0, original_items - reduced_items)

    series_map = defaultdict(
        lambda: {
            "original_value": 0.0,
            "original_items": 0.0,
            "reduced_value": 0.0,
            "reduced_items": 0.0,
            "order_count": 0,
        }
    )
    for o in orders:
        d = (_dt(o.get("created_at")) or start).date().isoformat()
        series_map[d]["order_count"] += 1
        series_map[d]["original_value"] += _num(o.get("total_order_value"))
        series_map[d]["original_items"] += _num(o.get("item_count"), _num(o.get("total_required_qty"), 0))
    for r in linked:
        d = (_dt(r.get("requested_at") or r.get("created_at")) or start).date().isoformat()
        sq, sv = _nmts_sourced_qty_value(r)
        series_map[d]["reduced_value"] += sv
        series_map[d]["reduced_items"] += sq

    series = []
    for d in sorted(series_map.keys()):
        v = series_map[d]
        ov = v["original_value"]
        rv = min(v["reduced_value"], ov) if ov > 0 else v["reduced_value"]
        oi = v["original_items"]
        ri = min(v["reduced_items"], oi) if oi > 0 else v["reduced_items"]
        fv = max(0.0, ov - rv)
        fi = max(0.0, oi - ri)
        series.append(
            {
                "date": d,
                "order_count": v["order_count"],
                "original_order_value": ov,
                "reduced_value": rv,
                "final_order_value": fv,
                "reduction_pct": _safe_pct(rv, ov),
                "original_order_items": oi,
                "reduced_items": ri,
                "final_order_items": fi,
                # Backward-compatible aliases used by older UI fragments.
                "order_value": ov,
                "sourced_value": rv,
                "external_avoided_value": rv,
                "unfulfilled_value": fv,
                "saving_pct": _safe_pct(rv, ov),
                "original": ov if metric_type == "value" else oi,
                "reduced": rv if metric_type == "value" else ri,
                "final": fv if metric_type == "value" else fi,
            }
        )

    return {
        "summary": {
            "total_order_count": len(orders),
            "original_order_value": original_value,
            "reduced_value": reduced_value,
            "final_order_value": final_value,
            "reduction_pct": _safe_pct(reduced_value, original_value),
            "original_order_items": original_items,
            "reduced_items": reduced_items,
            "final_order_items": final_items,
            # Aliases
            "total_order_value": original_value,
            "nmts_sourced_value": reduced_value,
            "external_purchase_avoided_value": reduced_value,
            "unfulfilled_value": final_value,
            "saving_pct": _safe_pct(reduced_value, original_value),
        },
        "series": series,
        "metric_type": metric_type,
        "scope": scope,
    }


def _fulfilled_qty_value(row: dict) -> Tuple[float, float]:
    """Qty/value actually supplied against a request line."""
    qty = _num(row.get("completed_qty"), _approved_qty(row))
    if qty <= 0:
        return 0.0, 0.0
    return qty, qty * _request_unit(row)


def _is_branch_fulfillment(row: dict) -> bool:
    """Same dealer network → branch supply; different dealer → co-dealer supply."""
    req_d = _text(row.get("requesting_dealer")).casefold()
    sup_d = _text(row.get("supplying_dealer")).casefold()
    if req_d and sup_d:
        return req_d == sup_d
    # Fallback: same brand + different branch treated as branch fulfillment.
    return _text(row.get("requesting_brand")).casefold() == _text(row.get("supplying_brand")).casefold()


@router.get("/request-acceptance")
async def analytics_request_acceptance(
    from_date: str,
    to_date: str,
    brand: Optional[str] = None,
    dealer: Optional[str] = None,
    branch: Optional[str] = None,
    brand_id: Optional[str] = None,
    dealer_id: Optional[str] = None,
    branch_id: Optional[str] = None,
    request_direction: str = "received",
    metric_type: str = "value",
    current_user=Depends(_current_user),
):
    """Request Fulfillment Analysis for requests received by the selected scope.

    Total Request = Fulfilled + Not Fulfilled
    Fulfilled = Given to Branches + Given to Dealers / Co-Dealers
    """
    scope = _resolve_scope_params(current_user, brand, dealer, branch, brand_id, dealer_id, branch_id)
    start, end, _, _, metric_type, _ = _common_params(from_date, to_date, metric_type=metric_type)
    direction = (request_direction or "received").lower()
    if direction not in {"raised", "received"}:
        raise HTTPException(400, "request_direction must be raised or received")
    # Fulfillment view defaults to requests received by the selected supplying scope.
    reqs = await _requests_query(scope, start, end, direction)

    received_v = fulfilled_v = branch_v = dealer_v = 0.0
    received_i = fulfilled_i = branch_i = dealer_i = 0.0
    groups = defaultdict(list)
    for r in reqs:
        groups[_text(r.get("request_number"), r.get("id"))].append(r)
        rq = _requested_qty(r)
        ru = _request_unit(r)
        received_v += ru * rq
        received_i += rq
        fq, fv = _fulfilled_qty_value(r)
        # Cap fulfillment to requested on the line.
        if fq > rq > 0:
            fv = ru * rq
            fq = rq
        fulfilled_v += fv
        fulfilled_i += fq
        if fq > 0:
            if _is_branch_fulfillment(r):
                branch_v += fv
                branch_i += fq
            else:
                dealer_v += fv
                dealer_i += fq

    not_fulfilled_v = max(0.0, received_v - fulfilled_v)
    not_fulfilled_i = max(0.0, received_i - fulfilled_i)

    daily = defaultdict(
        lambda: {
            "request_received_value": 0.0,
            "fulfilled_value": 0.0,
            "branch_value": 0.0,
            "dealer_value": 0.0,
            "request_received_items": 0.0,
            "fulfilled_items": 0.0,
            "branch_items": 0.0,
            "dealer_items": 0.0,
        }
    )
    for r in reqs:
        d = (_dt(r.get("requested_at") or r.get("created_at")) or start).date().isoformat()
        rq = _requested_qty(r)
        ru = _request_unit(r)
        daily[d]["request_received_value"] += ru * rq
        daily[d]["request_received_items"] += rq
        fq, fv = _fulfilled_qty_value(r)
        if fq > rq > 0:
            fv = ru * rq
            fq = rq
        daily[d]["fulfilled_value"] += fv
        daily[d]["fulfilled_items"] += fq
        if fq > 0:
            if _is_branch_fulfillment(r):
                daily[d]["branch_value"] += fv
                daily[d]["branch_items"] += fq
            else:
                daily[d]["dealer_value"] += fv
                daily[d]["dealer_items"] += fq

    series = []
    for d in sorted(daily.keys()):
        v = daily[d]
        rv = v["request_received_value"]
        fv = v["fulfilled_value"]
        ri = v["request_received_items"]
        fi = v["fulfilled_items"]
        series.append(
            {
                "date": d,
                "request_received_value": rv,
                "given_to_branches_value": v["branch_value"],
                "given_to_dealers_value": v["dealer_value"],
                "total_fulfilled_value": fv,
                "not_fulfilled_value": max(0.0, rv - fv),
                "fulfillment_pct": _safe_pct(fv, rv),
                "request_received_items": ri,
                "given_to_branches_items": v["branch_items"],
                "given_to_dealers_items": v["dealer_items"],
                "total_fulfilled_items": fi,
                "not_fulfilled_items": max(0.0, ri - fi),
                # Metric-selected convenience fields for charts
                "request_received": rv if metric_type == "value" else ri,
                "given_to_branches": v["branch_value"] if metric_type == "value" else v["branch_items"],
                "given_to_dealers": v["dealer_value"] if metric_type == "value" else v["dealer_items"],
                "total_fulfilled": fv if metric_type == "value" else fi,
                "not_fulfilled": (max(0.0, rv - fv) if metric_type == "value" else max(0.0, ri - fi)),
                # Legacy aliases
                "requested": rv,
                "accepted": fv,
            }
        )

    return {
        "summary": {
            "total_request_count": len(groups),
            "request_received_value": received_v,
            "total_fulfilled_value": fulfilled_v,
            "not_fulfilled_value": not_fulfilled_v,
            "given_to_branches_value": branch_v,
            "given_to_dealers_value": dealer_v,
            "fulfillment_pct": _safe_pct(fulfilled_v, received_v),
            "request_received_items": received_i,
            "total_fulfilled_items": fulfilled_i,
            "not_fulfilled_items": not_fulfilled_i,
            "given_to_branches_items": branch_i,
            "given_to_dealers_items": dealer_i,
            # Legacy aliases for older cards
            "total_requested_value": received_v,
            "accepted_request_count": sum(1 for g in groups.values() if sum(_approved_qty(i) for i in g) > 0),
            "fully_accepted_value": fulfilled_v,
            "partial_accepted_value": 0.0,
            "rejected_value": 0.0,
            "pending_value": not_fulfilled_v,
            "acceptance_pct": _safe_pct(fulfilled_v, received_v),
        },
        "series": series,
        "metric_type": metric_type,
        "request_direction": direction,
        "scope": scope,
    }


async def _stock_trend_payload(scope, date_keys, aging_type, metric_type, category, current_user):
    ctx = await _build_daily_stock_series(scope, date_keys, category, aging_type, metric_type, current_user)
    use_value = metric_type == "value"
    series = []
    for pt in ctx["daily"]:
        if pt["data_status"] == "NO_UPLOAD":
            series.append({**pt, "opening": None, "added": None, "reduced": None, "closing": None, "net_change": None, "change_pct": None})
            continue
        opening = None
        if pt.get("comparison_date"):
            comp_key = _iso_to_date_key(pt["comparison_date"])
            comp_status = _day_upload_status(comp_key, ctx["uploaded_by_date"], ctx["expected_branches"], ctx["consolidated"])
            comp_map = _filter_map_branches(ctx["by_date_parts"].get(comp_key, {}), comp_status["allowed_branch_keys"])
            ov, oq = _sum_map(comp_map)
            opening = ov if use_value else oq
        series.append(
            {
                **pt,
                "opening": opening,
                "added": pt["added_value"] if use_value else pt.get("added_quantity"),
                "reduced": pt["reduced_value"] if use_value else pt.get("reduced_quantity"),
                "closing": pt["stock_value"] if use_value else pt["stock_quantity"],
                "net_change": pt["net_change"] if use_value else pt.get("net_change_quantity"),
                "change_pct": pt["change_pct"],
            }
        )
    stk = ctx["summary"]
    return {
        "data_coverage": ctx["coverage"],
        "summary": {
            "data_status": stk.get("data_status"),
            "current_total": stk.get("current_stock_value") if use_value else stk.get("current_stock_quantity"),
            "opening": stk.get("previous_available_stock_value"),
            "closing": stk.get("current_stock_value") if use_value else stk.get("current_stock_quantity"),
            "added": stk.get("stock_added_value"),
            "reduced": stk.get("stock_reduced_value"),
            "net_change": stk.get("net_change_value"),
            "change_pct": stk.get("change_pct_value"),
            "comparison_date": stk.get("comparison_date"),
            "comparison_type": stk.get("comparison_type"),
            "comparison_label": stk.get("comparison_label"),
            "last_available_upload_date": stk.get("last_available_upload_date"),
            "last_available_stock_value": stk.get("last_available_stock_value"),
            "uploaded_branch_count": stk.get("uploaded_branch_count"),
            "expected_branch_count": stk.get("expected_branch_count"),
            "missing_branch_count": stk.get("missing_branch_count"),
            "coverage_percentage": stk.get("coverage_percentage"),
        },
        "series": series,
    }


@router.get("/stock-movement")
async def analytics_stock_movement(
    from_date: str,
    to_date: str,
    brand: Optional[str] = None,
    dealer: Optional[str] = None,
    branch: Optional[str] = None,
    brand_id: Optional[str] = None,
    dealer_id: Optional[str] = None,
    branch_id: Optional[str] = None,
    category: Optional[str] = None,
    aging_type: str = "purchase",
    metric_type: str = "value",
    current_user=Depends(_current_user),
):
    scope = _resolve_scope_params(current_user, brand, dealer, branch, brand_id, dealer_id, branch_id)
    _, _, date_keys, aging_type, metric_type, category = _common_params(
        from_date, to_date, category=category, aging_type=aging_type, metric_type=metric_type
    )
    data = await _stock_trend_payload(scope, date_keys, aging_type, metric_type, category, current_user)
    series = []
    for p in data.get("series", []):
        series.append(
            {
                **p,
                "added": p.get("added"),
                "reduced": p.get("reduced"),
                "net": p.get("net_change"),
                "closing": p.get("closing"),
            }
        )
    sm = data.get("summary", {})
    return {
        "data_coverage": data.get("data_coverage"),
        "summary": {
            "data_status": sm.get("data_status"),
            "period_added": sm.get("added"),
            "period_reduced": sm.get("reduced"),
            "net_change": sm.get("net_change"),
            "closing": sm.get("closing"),
            "comparison_type": sm.get("comparison_type"),
            "comparison_label": sm.get("comparison_label"),
            "uploaded_branch_count": sm.get("uploaded_branch_count"),
            "expected_branch_count": sm.get("expected_branch_count"),
            "missing_branch_count": sm.get("missing_branch_count"),
            "coverage_percentage": sm.get("coverage_percentage"),
        },
        "series": series,
    }


@router.get("/drilldown")
async def analytics_drilldown(
    from_date: str,
    to_date: str,
    drilldown_type: str,
    brand: Optional[str] = None,
    dealer: Optional[str] = None,
    branch: Optional[str] = None,
    brand_id: Optional[str] = None,
    dealer_id: Optional[str] = None,
    branch_id: Optional[str] = None,
    category: Optional[str] = None,
    aging_type: str = "purchase",
    metric_type: str = "value",
    focus_date: Optional[str] = None,
    page: int = 1,
    page_size: int = 50,
    request_direction: str = "raised",
    current_user=Depends(_current_user),
):
    scope = _resolve_scope_params(current_user, brand, dealer, branch, brand_id, dealer_id, branch_id)
    start, end, date_keys, aging_type, metric_type, category = _common_params(
        from_date, to_date, category=category, aging_type=aging_type, metric_type=metric_type
    )
    page = max(1, page)
    page_size = min(max(1, page_size), 200)
    dtype = (drilldown_type or "").lower()
    if dtype in {"missing_upload", "upload_status", "day_info"}:
        target = focus_date.replace("-", "") if focus_date else (date_keys[-1] if date_keys else _nmts_date_key())
        if len(target) == 10:
            target = target.replace("-", "")
        expected = await _expected_branch_names(scope, current_user)
        consolidated = not bool(scope.get("branch"))
        up, meta = await _uploaded_branches_by_date(scope, [target])
        st = _day_upload_status(target, up, expected, consolidated)
        branch_rows = []
        for b in expected:
            bf = b.casefold()
            branch_rows.append(
                {
                    "branch": b,
                    "uploaded": bf in up.get(target, set()),
                    "published_at": (meta.get(target, {}).get(bf) or {}).get("published_at"),
                    "total_value": (meta.get(target, {}).get(bf) or {}).get("total_value"),
                }
            )
        return {
            "records": branch_rows,
            "total": len(branch_rows),
            "page": 1,
            "page_size": page_size,
            "data_status": st["data_status"],
            "date": _date_key_to_iso(target),
            "uploaded_branches": st["uploaded_branches"],
            "missing_branches": st["missing_branches"],
            "message": "No stock upload was published for the selected scope on this date."
            if st["data_status"] == "NO_UPLOAD"
            else None,
        }

    if dtype in {"added", "reduced"}:
        target = focus_date.replace("-", "") if focus_date else (date_keys[-1] if date_keys else _nmts_date_key())
        if len(target) == 10:
            target = target.replace("-", "")
        expected = await _expected_branch_names(scope, current_user)
        consolidated = not bool(scope.get("branch"))
        up, _ = await _uploaded_branches_by_date(scope, [target])
        st = _day_upload_status(target, up, expected, consolidated)
        if st["data_status"] == "NO_UPLOAD":
            return {
                "records": [],
                "total": 0,
                "page": page,
                "page_size": page_size,
                "data_status": "NO_UPLOAD",
                "message": "No stock upload was published for the selected scope on this date.",
            }
        prior_key = await _find_last_upload_before(target, scope, expected, consolidated)
        load = [target]
        if prior_key:
            load.append(prior_key)
        by_date = await _aggregate_parts_for_dates(load, scope, category, await _snapshots_enabled())
        curr_map = _filter_map_branches(by_date.get(target, {}), st["allowed_branch_keys"])
        prev_map = {}
        if prior_key:
            pst = _day_upload_status(prior_key, await _uploaded_branches_by_date(scope, [prior_key])[0], expected, consolidated)
            prev_map = _filter_map_branches(by_date.get(prior_key, {}), pst["allowed_branch_keys"])
        mv = _compute_day_movement(prev_map, curr_map, aging_type, metric_type)
        lines = [x for x in mv["lines"] if (dtype == "added" and x["added_qty"] > 0) or (dtype == "reduced" and x["reduced_qty"] > 0)]
        total = len(lines)
        start_i = (page - 1) * page_size
        slice_rows = lines[start_i : start_i + page_size]
        records = []
        for x in slice_rows:
            records.append(
                {
                    "date": _date_key_to_iso(target),
                    "brand": x.get("brand_name"),
                    "dealer": x.get("dealer_name"),
                    "branch": x.get("branch"),
                    "part_number": x.get("part_number"),
                    "part_name": x.get("part_name"),
                    "category": x.get("category"),
                    "previous_quantity": x.get("previous_qty"),
                    "current_quantity": x.get("current_qty"),
                    "added_quantity": x.get("added_qty"),
                    "reduced_quantity": x.get("reduced_qty"),
                    "unit_value": x.get("unit_value"),
                    "added_value": x.get("added_value"),
                    "reduced_value": x.get("reduced_value"),
                    "aging_days": x.get("aging_days"),
                }
            )
        return {"records": records, "total": total, "page": page, "page_size": page_size}

    if dtype == "order_saving":
        orders = await _orders_query(scope, start, end)
        order_ids = {o.get("id") for o in orders}
        om = {o.get("id"): o for o in orders}
        reqs = [r for r in await _requests_query(scope, start, end, "raised") if r.get("order_id") in order_ids]
        rows = []
        for r in reqs:
            sq, sv = _nmts_sourced_qty_value(r)
            if sv <= 0 and sq <= 0:
                continue
            o = om.get(r.get("order_id"), {})
            rows.append(
                {
                    "order_number": o.get("order_number"),
                    "order_date": o.get("created_at"),
                    "part_number": r.get("part_number"),
                    "part_name": r.get("part_name", r.get("description")),
                    "ordered_quantity": _requested_qty(r),
                    "nmts_sourced_quantity": sq,
                    "unit_value": _request_unit(r),
                    "sourced_value": sv,
                    "source_branch": r.get("supplying_branch"),
                    "destination_branch": r.get("requesting_branch"),
                    "request_number": r.get("request_number"),
                    "transfer_status": r.get("status"),
                }
            )
        total = len(rows)
        start_i = (page - 1) * page_size
        return {"records": rows[start_i : start_i + page_size], "total": total, "page": page, "page_size": page_size}

    if dtype == "request":
        reqs = await _requests_query(scope, start, end, (request_direction or "raised").lower())
        rows = []
        for r in reqs:
            rq = _requested_qty(r)
            ap = _approved_qty(r)
            unit = _request_unit(r)
            rows.append(
                {
                    "request_number": r.get("request_number"),
                    "request_date": r.get("requested_at") or r.get("created_at"),
                    "requested_by": r.get("requested_user_name", r.get("requested_by")),
                    "requested_to": r.get("supplying_branch"),
                    "part_number": r.get("part_number"),
                    "requested_quantity": rq,
                    "approved_quantity": ap,
                    "requested_value": rq * unit,
                    "accepted_value": ap * unit,
                    "request_status": r.get("status"),
                    "remarks": r.get("remarks"),
                }
            )
        total = len(rows)
        start_i = (page - 1) * page_size
        return {"records": rows[start_i : start_i + page_size], "total": total, "page": page, "page_size": page_size}

    raise HTTPException(400, "Unknown drilldown_type")


async def ensure_analytics_indexes():
    await db.products.create_index([("active_date_key", 1), ("brand_name", 1), ("dealer_name", 1), ("branch", 1)])
    await db.order_headers.create_index([("created_at", -1), ("brand_name", 1), ("dealer_name", 1), ("branch", 1)])
    await db.analytics_stock_daily_snapshots.create_index(
        [("snapshot_date_ist", 1), ("brand_id", 1), ("dealer_id", 1), ("branch_id", 1), ("part_number", 1)],
        unique=True,
        name="uq_analytics_daily_snapshot",
    )
