"""Order Desk sequential Auto Sourcing engine.

Approved ranking (after Own Branch):
  1) Full requested-quantity coverage
  2) Minimum pickup locations (prefer destinations covering more remaining lines)
  3) Same Dealer before Other Dealer
  4) Higher aging stock first
  5) Available quantity as tie-breaker

Routes to one supplier at a time. Reject / timeout / partial acceptance
continues only the remaining balance. Official request confirmation is set
only after acceptance; attempts remain audit-tagged.
"""
from __future__ import annotations

import logging
import math
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

try:
    from . import order_desk_workflow as odw
except ImportError:
    import order_desk_workflow as odw

logger = logging.getLogger("nmts.order_auto_sourcing")

AUTO_SOURCE_ACTIVE = "active"
AUTO_SOURCE_WAITING = "waiting_response"
AUTO_SOURCE_LAST_CHANCE = "last_chance"
AUTO_SOURCE_FACTORY_DECISION = "factory_decision_required"
AUTO_SOURCE_COMPLETED = "completed"
AUTO_SOURCE_CANCELLED = "cancelled"

FACTORY_DECISION_MESSAGE = "Auto Sourcing Completed – Factory Decision Required"

SOURCING_EVENT_COLLECTION = "order_sourcing_events"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _f(value: Any, default: float = 0.0) -> float:
    try:
        return float(value if value is not None else default)
    except (TypeError, ValueError):
        return float(default)


def response_time_minutes_for_lines(line_item_count: int) -> int:
    """15 minutes for every 5 part lines (1–5 → 15, 6–10 → 30, …)."""
    n = max(0, int(line_item_count or 0))
    if n <= 0:
        return 15
    return int(math.ceil(n / 5.0) * 15)


def aging_for(source: dict, aging_type: str = "purchase") -> float:
    if str(aging_type or "").lower() == "sales":
        return _f(source.get("sales_aging_days"))
    return _f(source.get("purchase_aging_days") or source.get("aging_days"))


def destination_rank_key(
    *,
    full_coverage_count: int,
    lines_covered: int,
    same_dealer: bool,
    max_aging: float,
    total_available: float,
    dealer: str,
    branch: str,
) -> tuple:
    """Ascending sort key — lower is better / earlier."""
    return (
        -int(full_coverage_count),
        -int(lines_covered),
        0 if same_dealer else 1,
        -float(max_aging or 0),
        -float(total_available or 0),
        _clean(dealer).lower(),
        _clean(branch).lower(),
    )


def single_source_rank_key(source: dict, need_qty: float, order: dict, aging_type: str = "purchase") -> tuple:
    """Rank one source for a single remaining line (used by Auto Suggest too)."""
    avail = _f(source.get("net_available_qty", source.get("available_qty")))
    covers_full = 1 if avail + 1e-9 >= _f(need_qty) else 0
    same = odw.is_own_ordering_branch(source, order) or (
        _clean(source.get("dealer_name")).lower() == _clean(order.get("dealer_name")).lower()
    )
    return destination_rank_key(
        full_coverage_count=covers_full,
        lines_covered=1,
        same_dealer=same,
        max_aging=aging_for(source, aging_type),
        total_available=avail,
        dealer=_clean(source.get("dealer_name")),
        branch=_clean(source.get("branch")),
    )


def build_destination_candidates(
    items: List[dict],
    order: dict,
    freezes: set,
    *,
    aging_type: str = "purchase",
    min_aging_days: float = 0.0,
    levels: Optional[List[str]] = None,
) -> Dict[Tuple[str, str], dict]:
    """Map (dealer, branch) → aggregate ranking stats + per-line takes."""
    levels = levels or ["own", "branch", "dealer"]
    order_dealer = _clean(order.get("dealer_name")).lower()
    out: Dict[Tuple[str, str], dict] = {}
    for item in items:
        need = _f(item.get("remaining_qty"))
        if need <= 0:
            continue
        if item.get("auto_source_closed"):
            continue
        seen_keys = set()
        for level in levels:
            if level == "own" and item.get("own_stage_exhausted"):
                continue
            if level == "branch" and item.get("branch_stage_exhausted"):
                continue
            if level == "dealer" and item.get("dealer_stage_exhausted"):
                continue
            pool = odw.eligible_pool(item, order, level, freezes, aging_type, min_aging_days)
            for source in pool:
                dealer = _clean(source.get("dealer_name"))
                branch = _clean(source.get("branch"))
                key = (dealer.lower(), branch.lower())
                if key in seen_keys:
                    continue
                seen_keys.add(key)
                avail = _f(source.get("net_available_qty", source.get("available_qty")))
                if avail <= 0:
                    continue
                take = min(need, avail)
                covers_full = take + 1e-9 >= need
                slot = out.get(key)
                if not slot:
                    slot = {
                        "dealer_name": dealer,
                        "branch": branch,
                        "level": level,
                        "same_dealer": dealer.lower() == order_dealer,
                        "is_own": odw.is_own_ordering_branch(source, order),
                        "lines": [],
                        "full_coverage_count": 0,
                        "max_aging": 0.0,
                        "total_available": 0.0,
                    }
                    out[key] = slot
                # Prefer richest source row if duplicate across pools
                slot["lines"].append({
                    "item": item,
                    "source": source,
                    "take_qty": take,
                    "covers_full": covers_full,
                    "level": level,
                    "aging": aging_for(source, aging_type),
                    "available": avail,
                })
                if covers_full:
                    slot["full_coverage_count"] += 1
                slot["max_aging"] = max(slot["max_aging"], aging_for(source, aging_type))
                slot["total_available"] += avail
                if odw.is_own_ordering_branch(source, order):
                    slot["is_own"] = True
                    slot["level"] = "own"
                elif slot.get("level") != "own" and level == "branch":
                    slot["level"] = "branch"
    return out


def select_best_destination(
    candidates: Dict[Tuple[str, str], dict],
    *,
    prefer_own: bool = True,
) -> Optional[dict]:
    if not candidates:
        return None
    rows = list(candidates.values())
    if prefer_own:
        own = [r for r in rows if r.get("is_own")]
        if own:
            rows = own
    rows.sort(
        key=lambda r: destination_rank_key(
            full_coverage_count=r.get("full_coverage_count") or 0,
            lines_covered=len(r.get("lines") or []),
            same_dealer=bool(r.get("same_dealer") or r.get("is_own")),
            max_aging=r.get("max_aging") or 0,
            total_available=r.get("total_available") or 0,
            dealer=r.get("dealer_name") or "",
            branch=r.get("branch") or "",
        )
    )
    return rows[0]


def find_unique_holder_candidates(
    items: List[dict],
    order: dict,
    freezes: set,
    *,
    aging_type: str = "purchase",
    min_aging_days: float = 0.0,
) -> Dict[Tuple[str, str], dict]:
    """Last Chance: destinations that uniquely hold at least one remaining part."""
    # part -> list of (dealer,branch,source,item)
    holders: Dict[str, List[dict]] = {}
    for item in items:
        need = _f(item.get("remaining_qty"))
        if need <= 0 or item.get("auto_source_closed"):
            continue
        part = _clean(item.get("part_number")).upper()
        seen = set()
        for level in ("own", "branch", "dealer"):
            for source in odw.eligible_pool(item, order, level, freezes, aging_type, min_aging_days):
                key = odw.source_key(source.get("dealer_name"), source.get("branch"))
                if key in seen:
                    continue
                seen.add(key)
                avail = _f(source.get("net_available_qty", source.get("available_qty")))
                if avail <= 0:
                    continue
                holders.setdefault(part, []).append({
                    "item": item,
                    "source": source,
                    "key": key,
                    "take_qty": min(need, avail),
                    "level": level,
                })
    unique_dest: Dict[Tuple[str, str], dict] = {}
    for part, rows in holders.items():
        keys = {r["key"] for r in rows}
        if len(keys) != 1:
            continue
        for r in rows:
            dealer = _clean(r["source"].get("dealer_name"))
            branch = _clean(r["source"].get("branch"))
            dkey = (dealer.lower(), branch.lower())
            slot = unique_dest.get(dkey)
            if not slot:
                slot = {
                    "dealer_name": dealer,
                    "branch": branch,
                    "level": r["level"],
                    "same_dealer": dealer.lower() == _clean(order.get("dealer_name")).lower(),
                    "is_own": odw.is_own_ordering_branch(r["source"], order),
                    "lines": [],
                    "full_coverage_count": 0,
                    "max_aging": 0.0,
                    "total_available": 0.0,
                    "last_chance": True,
                }
                unique_dest[dkey] = slot
            take = r["take_qty"]
            need = _f(r["item"].get("remaining_qty"))
            covers = take + 1e-9 >= need
            slot["lines"].append({
                "item": r["item"],
                "source": r["source"],
                "take_qty": take,
                "covers_full": covers,
                "level": r["level"],
                "aging": aging_for(r["source"], aging_type),
                "available": _f(r["source"].get("net_available_qty", r["source"].get("available_qty"))),
            })
            if covers:
                slot["full_coverage_count"] += 1
            slot["max_aging"] = max(slot["max_aging"], aging_for(r["source"], aging_type))
            slot["total_available"] += _f(r["source"].get("net_available_qty", r["source"].get("available_qty")))
    return unique_dest


async def append_sourcing_event(db, order: dict, action: str, details: Optional[dict] = None, actor=None):
    doc = {
        "id": str(uuid.uuid4()),
        "order_id": order.get("id"),
        "order_number": order.get("order_number"),
        "action": action,
        "details": details or {},
        "performed_by": getattr(actor, "id", None) if actor else (details or {}).get("performed_by"),
        "performed_user_name": getattr(actor, "username", None) if actor else None,
        "created_at": _now_iso(),
    }
    await db[SOURCING_EVENT_COLLECTION].insert_one(dict(doc))
    await odw.append_order_audit(db, order, action, actor or {"id": "system", "username": "Auto Sourcing"}, details)
    return doc


def remaining_items(items: List[dict]) -> List[dict]:
    return [i for i in items if _f(i.get("remaining_qty")) > 0 and not i.get("auto_source_closed")]


async def mark_factory_decision_required(db, order: dict, items: List[dict], actor=None) -> dict:
    now = _now_iso()
    for item in remaining_items(items):
        rem = _f(item.get("remaining_qty"))
        await db.order_items.update_one(
            {"id": item["id"]},
            {
                "$set": {
                    "no_further_stock": True,
                    "factory_order_qty": rem,
                    "factory_decision_required": True,
                    "request_status": odw.REQUEST_STATUS_FACTORY,
                    "status": odw.REQUEST_STATUS_FACTORY,
                    "auto_source_closed": False,
                    "updated_at": now,
                }
            },
        )
    await db.order_headers.update_one(
        {"id": order["id"]},
        {
            "$set": {
                "auto_sourcing_state": AUTO_SOURCE_FACTORY_DECISION,
                "auto_sourcing_message": FACTORY_DECISION_MESSAGE,
                "auto_sourcing_active": False,
                "factory_decision_required": True,
                "updated_at": now,
            }
        },
    )
    await append_sourcing_event(
        db, order, FACTORY_DECISION_MESSAGE,
        {"state": AUTO_SOURCE_FACTORY_DECISION},
        actor,
    )
    return {
        "state": AUTO_SOURCE_FACTORY_DECISION,
        "message": FACTORY_DECISION_MESSAGE,
    }
