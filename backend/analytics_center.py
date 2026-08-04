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
_num = rc._num
_text = rc._text
_dt = rc._dt
_bucket = rc._bucket
_aging_days = rc._aging_days
_scope = rc._scope
_query = rc._query
_and = rc._and


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
    if not date_keys:
        return {}
    q = _and(_query(scope), {"publish_status": "Published", "active_date_key": {"$in": date_keys}})
    cat = _category_clause(category)
    if cat:
        q = _and(q, cat)
    pipeline = [
        {"$match": q},
        {
            "$addFields": {
                "qty_n": {"$ifNull": ["$available_qty_number", {"$ifNull": ["$quantity", 0]}]},
                "unit_n": {
                    "$ifNull": [
                        "$unit_value_number",
                        {"$ifNull": ["$mav_value", {"$ifNull": ["$unit_value", 0]}]},
                    ]
                },
            }
        },
        {"$sort": {"active_date_key": 1, "published_at": -1, "updated_at": -1}},
        {
            "$group": {
                "_id": {
                    "date": "$active_date_key",
                    "brand": "$brand_name",
                    "dealer": "$dealer_name",
                    "branch": "$branch",
                    "part": "$part_number",
                },
                "brand_name": {"$first": "$brand_name"},
                "dealer_name": {"$first": "$dealer_name"},
                "branch": {"$first": "$branch"},
                "part_number": {"$first": "$part_number"},
                "part_name": {"$first": {"$ifNull": ["$item_name", {"$ifNull": ["$part_name", ""]}]}},
                "part_category": {"$first": {"$ifNull": ["$part_category", {"$ifNull": ["$category", "Uncategorized"]}]}},
                "qty": {"$first": "$qty_n"},
                "unit": {"$first": "$unit_n"},
                "purchase_aging_days": {"$first": "$purchase_aging_days"},
                "sales_aging_days": {"$first": "$sales_aging_days"},
                "last_receipt_date": {"$first": "$last_receipt_date"},
                "last_sales_date": {"$first": "$last_sales_date"},
            }
        },
    ]
    rows = await db.products.aggregate(pipeline, allowDiskUse=True).to_list(500000)
    by_date: Dict[str, Dict[Tuple[str, str, str, str], dict]] = defaultdict(dict)
    for r in rows:
        dk = r["_id"]["date"]
        pk = (
            _text(r.get("brand_name")).casefold(),
            _text(r.get("dealer_name")).casefold(),
            _text(r.get("branch")).casefold(),
            _text(r.get("part_number")).casefold(),
        )
        qty = _num(r.get("qty"))
        unit = _num(r.get("unit"))
        by_date[dk][pk] = {
            **r,
            "qty": qty,
            "unit": unit,
            "value": qty * unit,
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
    last_key = date_keys[-1]
    q = _and(_query(scope), {"publish_status": "Published", "active_date_key": last_key})
    pipeline = [
        {"$match": q},
        {
            "$group": {
                "_id": {"$ifNull": ["$part_category", {"$ifNull": ["$category", "Uncategorized"]}]},
            }
        },
        {"$sort": {"_id": 1}},
    ]
    rows = await db.products.aggregate(pipeline).to_list(5000)
    cats = sorted({_text(r.get("_id"), "Uncategorized") for r in rows if _text(r.get("_id"))})
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
    by_date = await _aggregate_latest_parts_for_date_keys(date_keys, scope, category)
    prev_key = _prev_date_key(date_keys[0])
    by_date_with_prev = await _aggregate_latest_parts_for_date_keys([prev_key] + date_keys, scope, category)
    prev_map = by_date_with_prev.get(prev_key, {})
    daily_series = []
    period_added_v = period_reduced_v = 0.0
    for dk in date_keys:
        pkey = _prev_date_key(dk)
        prev_m = by_date_with_prev.get(pkey, prev_map if dk == date_keys[0] else by_date_with_prev.get(_prev_date_key(dk), {}))
        curr_m = by_date.get(dk, {})
        mv = _compute_day_movement(prev_m, curr_m, aging_type, metric_type)
        period_added_v += mv["added_value"]
        period_reduced_v += mv["reduced_value"]
        daily_series.append(
            {
                "date": _date_key_to_iso(dk),
                "closing_stock_value": mv["closing_value"],
                "closing_stock_qty": mv["closing_qty"],
            }
        )
        prev_map = curr_m

    last_dk = date_keys[-1]
    prev_period_key = _prev_date_key(date_keys[0])
    curr_closing = sum(x["value"] for x in by_date.get(last_dk, {}).values())
    prev_day_key = _prev_date_key(last_dk)
    prev_day_val = sum(x["value"] for x in by_date_with_prev.get(prev_day_key, {}).values())
    comp_prev_val = sum(x["value"] for x in by_date_with_prev.get(prev_period_key, {}).values())
    comparison_base = prev_day_val if from_date == to_date else comp_prev_val

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

    for pt in daily_series:
        d_iso = pt["date"]
        day_orders = [_num(o.get("total_order_value")) for o in orders if (_dt(o.get("created_at")) or start).date().isoformat() == d_iso]
        pt["order_value"] = sum(day_orders)
        day_reqs = [r for r in reqs if (_dt(r.get("requested_at") or r.get("created_at")) or start).date().isoformat() == d_iso]
        pt["accepted_request_value"] = sum(_request_unit(r) * _approved_qty(r) for r in day_reqs)

    net = curr_closing - comparison_base
    return {
        "scope": scope,
        "summary": {
            "current_stock_value": curr_closing,
            "previous_stock_value": comparison_base,
            "stock_added_value": period_added_v,
            "stock_reduced_value": period_reduced_v,
            "net_change_value": net,
            "change_pct_value": _pct_change(curr_closing, comparison_base),
            "total_order_value": order_value,
            "nmts_sourced_value": nmts_v,
            "total_requested_value": requested_v,
            "total_accepted_value": accepted_v,
        },
        "comparison": {"mode": "previous_day" if from_date == to_date else "previous_period", "previous_value": comparison_base},
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
    payload = await _stock_trend_payload(scope, date_keys, aging_type, metric_type, category)
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
    if not date_keys:
        return {"categories": []}
    last = date_keys[-1]
    first_prev = _prev_date_key(date_keys[0])
    by_date = await _aggregate_latest_parts_for_date_keys([first_prev] + date_keys, scope, None)
    cats: Dict[str, dict] = defaultdict(lambda: {"current_value": 0.0, "current_qty": 0.0, "added_value": 0.0, "reduced_value": 0.0, "daily": []})
    for dk in date_keys:
        pk = _prev_date_key(dk)
        mv = _compute_day_movement(by_date.get(pk, {}), by_date.get(dk, {}), aging_type, metric_type)
        per_cat = defaultdict(lambda: {"added": 0.0, "reduced": 0.0, "closing": 0.0})
        for line in mv["lines"]:
            c = line["category"]
            per_cat[c]["added"] += line["added_value"] if metric_type == "value" else line["added_qty"]
            per_cat[c]["reduced"] += line["reduced_value"] if metric_type == "value" else line["reduced_qty"]
        for pk_part, row in by_date.get(dk, {}).items():
            c = _row_category(row)
            per_cat[c]["closing"] += row["value"] if metric_type == "value" else row["qty"]
        for c, vals in per_cat.items():
            cats[c]["daily"].append({"date": _date_key_to_iso(dk), **vals})
    for pk_part, row in by_date.get(last, {}).items():
        c = _row_category(row)
        if category and c.lower() != category.lower():
            continue
        cats[c]["current_value"] += row["value"]
        cats[c]["current_qty"] += row["qty"]
    for dk in date_keys:
        pk = _prev_date_key(dk)
        mv = _compute_day_movement(by_date.get(pk, {}), by_date.get(dk, {}), aging_type, metric_type)
        for line in mv["lines"]:
            c = line["category"]
            if category and c.lower() != category.lower():
                continue
            cats[c]["added_value"] += line["added_value"]
            cats[c]["reduced_value"] += line["reduced_value"]
    out = []
    for name, data in sorted(cats.items()):
        if category and name.lower() != category.lower():
            continue
        opening_est = data["current_value"] - data["added_value"] + data["reduced_value"]
        out.append(
            {
                "category": name,
                "current_value": data["current_value"],
                "current_qty": data["current_qty"],
                "added_value": data["added_value"],
                "reduced_value": data["reduced_value"],
                "net_change": data["added_value"] - data["reduced_value"],
                "change_pct": _pct_change(data["current_value"], opening_est),
                "daily": data["daily"],
            }
        )
    return {"categories": out}


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
        return {"buckets": [], "stacked": [], "daily": []}
    last = date_keys[-1]
    extended = date_keys
    by_date = await _aggregate_latest_parts_for_date_keys(extended, scope, category)
    bucket_totals = {b: {"value": 0.0, "qty": 0.0} for b in BUCKETS}
    stacked = defaultdict(lambda: {b: 0.0 for b in BUCKETS})
    for row in by_date.get(last, {}).values():
        days = _aging_days_row(row, aging_type)
        b = _bucket(days) or BUCKETS[0]
        bucket_totals[b]["value"] += row["value"]
        bucket_totals[b]["qty"] += row["qty"]
        cat = _row_category(row)
        metric = row["value"] if metric_type == "value" else row["qty"]
        stacked[cat][b] += metric
    cat_total = sum(x["value"] if metric_type == "value" else x["qty"] for x in by_date.get(last, {}).values()) or 1.0
    buckets_out = []
    for b in BUCKETS:
        v = bucket_totals[b]["value"]
        q = bucket_totals[b]["qty"]
        m = v if metric_type == "value" else q
        buckets_out.append({"bucket": b, "value": v, "quantity": q, "pct_of_total": _safe_pct(m, cat_total)})
    stacked_out = [{"category": c, **{b: stacked[c][b] for b in BUCKETS}} for c in sorted(stacked.keys())]
    daily = []
    for dk in date_keys:
        day_buckets = {b: 0.0 for b in BUCKETS}
        for row in by_date.get(dk, {}).values():
            b = _bucket(_aging_days_row(row, aging_type)) or BUCKETS[0]
            day_buckets[b] += row["value"] if metric_type == "value" else row["qty"]
        daily.append({"date": _date_key_to_iso(dk), **{b: day_buckets[b] for b in BUCKETS}})
    return {"buckets": buckets_out, "stacked": stacked_out, "daily": daily}


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
    scope = _resolve_scope_params(current_user, brand, dealer, branch, brand_id, dealer_id, branch_id)
    start, end, date_keys, _, metric_type, _ = _common_params(from_date, to_date, metric_type=metric_type)
    orders = await _orders_query(scope, start, end)
    order_ids = {o.get("id") for o in orders}
    reqs = await _requests_query(scope, start, end, "raised")
    linked = [r for r in reqs if r.get("order_id") in order_ids]
    total_orders = len(orders)
    total_order_value = sum(_num(o.get("total_order_value")) for o in orders)
    sourced_v = sourced_q = 0.0
    for r in linked:
        sq, sv = _nmts_sourced_qty_value(r)
        sourced_q += sq
        sourced_v += sv
    unfulfilled = max(0.0, total_order_value - sourced_v)
    series_map = defaultdict(lambda: {"order_count": 0, "order_value": 0.0, "sourced_value": 0.0, "sourced_qty": 0.0})
    for o in orders:
        d = (_dt(o.get("created_at")) or start).date().isoformat()
        series_map[d]["order_count"] += 1
        series_map[d]["order_value"] += _num(o.get("total_order_value"))
    for r in linked:
        d = (_dt(r.get("requested_at") or r.get("created_at")) or start).date().isoformat()
        sq, sv = _nmts_sourced_qty_value(r)
        series_map[d]["sourced_value"] += sv
        series_map[d]["sourced_qty"] += sq
    series = []
    for d in sorted(series_map.keys()):
        v = series_map[d]
        u = max(0.0, v["order_value"] - v["sourced_value"])
        series.append(
            {
                "date": d,
                "order_count": v["order_count"],
                "order_value": v["order_value"],
                "sourced_value": v["sourced_value"],
                "external_avoided_value": v["sourced_value"],
                "unfulfilled_value": u,
                "saving_pct": _safe_pct(v["sourced_value"], v["order_value"]),
            }
        )
    return {
        "summary": {
            "total_order_count": total_orders,
            "total_order_value": total_order_value,
            "nmts_sourced_value": sourced_v,
            "external_purchase_avoided_value": sourced_v,
            "unfulfilled_value": unfulfilled,
            "saving_pct": _safe_pct(sourced_v, total_order_value),
        },
        "series": series,
    }


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
    request_direction: str = "raised",
    metric_type: str = "value",
    current_user=Depends(_current_user),
):
    scope = _resolve_scope_params(current_user, brand, dealer, branch, brand_id, dealer_id, branch_id)
    start, end, _, _, metric_type, _ = _common_params(from_date, to_date, metric_type=metric_type)
    direction = (request_direction or "raised").lower()
    if direction not in {"raised", "received"}:
        raise HTTPException(400, "request_direction must be raised or received")
    reqs = await _requests_query(scope, start, end, direction)
    groups = defaultdict(list)
    for r in reqs:
        groups[_text(r.get("request_number"), r.get("id"))].append(r)
    total_requests = len(groups)
    total_requested_v = total_accepted_v = partial_v = rejected_v = pending_v = 0.0
    accepted_count = 0
    for _, items in groups.items():
        rq_v = sum(_request_unit(i) * _requested_qty(i) for i in items)
        ap_v = sum(_request_unit(i) * _approved_qty(i) for i in items)
        total_requested_v += rq_v
        total_accepted_v += ap_v
        st = _text(items[0].get("status")).lower()
        if ap_v >= rq_v and rq_v > 0:
            accepted_count += 1
        elif ap_v > 0:
            partial_v += ap_v
        elif st in {"rejected", "cancelled"}:
            rejected_v += rq_v
        else:
            pending_v += max(0.0, rq_v - ap_v)
    daily = defaultdict(lambda: {"requested": 0.0, "accepted": 0.0, "partial": 0.0, "rejected": 0.0, "pending": 0.0})
    for r in reqs:
        d = (_dt(r.get("requested_at") or r.get("created_at")) or start).date().isoformat()
        rq = _request_unit(r) * _requested_qty(r)
        ap = _request_unit(r) * _approved_qty(r)
        daily[d]["requested"] += rq
        daily[d]["accepted"] += ap
        st = _text(r.get("status")).lower()
        if 0 < ap < rq:
            daily[d]["partial"] += ap
        elif st in {"rejected", "cancelled"}:
            daily[d]["rejected"] += rq
        elif ap < rq:
            daily[d]["pending"] += rq - ap
    series = [{"date": d, **daily[d]} for d in sorted(daily.keys())]
    return {
        "summary": {
            "total_request_count": total_requests,
            "total_requested_value": total_requested_v,
            "accepted_request_count": accepted_count,
            "fully_accepted_value": total_accepted_v - partial_v,
            "partial_accepted_value": partial_v,
            "rejected_value": rejected_v,
            "pending_value": pending_v,
            "acceptance_pct": _safe_pct(total_accepted_v, total_requested_v),
        },
        "series": series,
    }


async def _stock_trend_payload(scope, date_keys, aging_type, metric_type, category):
    extended = [_prev_date_key(date_keys[0])] + date_keys
    by_date = await _aggregate_latest_parts_for_date_keys(extended, scope, category)
    series = []
    opening_period = closing_period = added_period = reduced_period = 0.0
    for i, dk in enumerate(date_keys):
        pk = _prev_date_key(dk)
        mv = _compute_day_movement(by_date.get(pk, {}), by_date.get(dk, {}), aging_type, metric_type)
        if i == 0:
            opening_period = mv["opening_value"] if metric_type == "value" else mv["opening_qty"]
        closing_period = mv["closing_value"] if metric_type == "value" else mv["closing_qty"]
        added_period += mv["metric_added"]
        reduced_period += mv["metric_reduced"]
        series.append(
            {
                "date": _date_key_to_iso(dk),
                "opening": mv["opening_value"] if metric_type == "value" else mv["opening_qty"],
                "added": mv["metric_added"],
                "reduced": mv["metric_reduced"],
                "closing": mv["closing_value"] if metric_type == "value" else mv["closing_qty"],
                "net_change": mv["metric_net"],
                "change_pct": mv["change_pct_value"] if metric_type == "value" else mv["change_pct_qty"],
            }
        )
    return {
        "summary": {
            "current_total": closing_period,
            "opening": opening_period,
            "closing": closing_period,
            "added": added_period,
            "reduced": reduced_period,
            "net_change": closing_period - opening_period,
            "change_pct": _pct_change(closing_period, opening_period),
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
    data = await _stock_trend_payload(scope, date_keys, aging_type, metric_type, category)
    series = [
        {
            "date": p["date"],
            "added": p["added"],
            "reduced": p["reduced"],
            "net": p["net_change"],
            "closing": p["closing"],
        }
        for p in data.get("series", [])
    ]
    sm = data.get("summary", {})
    return {
        "summary": {
            "period_added": sm.get("added", 0),
            "period_reduced": sm.get("reduced", 0),
            "net_change": sm.get("net_change", 0),
            "closing": sm.get("closing", 0),
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
    if dtype in {"added", "reduced"}:
        target = focus_date.replace("-", "") if focus_date else (date_keys[-1] if date_keys else _nmts_date_key())
        if len(target) == 10:
            target = target.replace("-", "")
        prev = _prev_date_key(target)
        by_date = await _aggregate_latest_parts_for_date_keys([prev, target], scope, category)
        mv = _compute_day_movement(by_date.get(prev, {}), by_date.get(target, {}), aging_type, metric_type)
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
