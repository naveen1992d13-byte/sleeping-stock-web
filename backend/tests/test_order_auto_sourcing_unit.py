"""Unit tests for Order Desk sequential Auto Sourcing ranking + SLA."""
from __future__ import annotations

import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

import order_auto_sourcing as oas  # noqa: E402
import order_desk_workflow as odw  # noqa: E402


ORDER = {"brand_name": "Hyundai", "dealer_name": "FPL", "branch": "Vanagaram"}


def _src(dealer, branch, qty, aging=100, sales=10, stock_id=None):
    return {
        "dealer_name": dealer,
        "branch": branch,
        "available_qty": qty,
        "net_available_qty": qty,
        "purchase_aging_days": aging,
        "sales_aging_days": sales,
        "aging_days": aging,
        "stock_id": stock_id or f"{dealer}-{branch}",
    }


def _item(part, need, same=None, other=None, remaining=None):
    return {
        "id": f"i-{part}",
        "part_number": part,
        "required_qty": need,
        "remaining_qty": need if remaining is None else remaining,
        "same_dealer_sources": same or [],
        "other_dealer_sources": other or [],
        "allocations": [],
        "excluded_sources": [],
    }


def test_sla_fifteen_per_five_lines():
    assert oas.response_time_minutes_for_lines(1) == 15
    assert oas.response_time_minutes_for_lines(5) == 15
    assert oas.response_time_minutes_for_lines(6) == 30
    assert oas.response_time_minutes_for_lines(10) == 30
    assert oas.response_time_minutes_for_lines(11) == 45
    assert odw.response_time_minutes_for_lines(16) == 60


def test_full_coverage_beats_partial():
    items = [_item("A", 10, same=[
        _src("FPL", "Koyambedu", 4, aging=500),
        _src("FPL", "Retteri", 10, aging=50),
    ])]
    cands = oas.build_destination_candidates(items, ORDER, set(), levels=["branch"])
    best = oas.select_best_destination(cands, prefer_own=False)
    assert best["branch"] == "Retteri"


def test_same_dealer_before_other_dealer():
    items = [_item(
        "A", 5,
        same=[_src("FPL", "Koyambedu", 5, aging=10)],
        other=[_src("KUN", "Chrompet", 5, aging=999)],
    )]
    cands = oas.build_destination_candidates(items, ORDER, set(), levels=["branch", "dealer"])
    best = oas.select_best_destination(cands, prefer_own=False)
    assert best["dealer_name"] == "FPL"
    assert best["same_dealer"] is True


def test_min_pickup_prefers_destination_covering_more_lines():
    items = [
        _item("A", 2, same=[_src("FPL", "Koyambedu", 2, aging=40), _src("FPL", "Retteri", 2, aging=40)]),
        _item("B", 2, same=[_src("FPL", "Koyambedu", 2, aging=40)]),
    ]
    cands = oas.build_destination_candidates(items, ORDER, set(), levels=["branch"])
    best = oas.select_best_destination(cands, prefer_own=False)
    assert best["branch"] == "Koyambedu"
    assert len(best["lines"]) == 2


def test_own_branch_preferred_when_present():
    items = [_item("A", 3, same=[
        _src("FPL", "Vanagaram", 3, aging=5),
        _src("FPL", "Koyambedu", 30, aging=900),
    ])]
    cands = oas.build_destination_candidates(items, ORDER, set(), levels=["own", "branch"])
    best = oas.select_best_destination(cands, prefer_own=True)
    assert best["branch"] == "Vanagaram"
    assert best["is_own"] is True


def test_unique_holder_last_chance():
    items = [
        _item("ONLY1", 2, same=[], other=[_src("KUN", "Chrompet", 5, aging=80)]),
        _item("MULTI", 2, same=[_src("FPL", "Koyambedu", 2)], other=[_src("KUN", "Chrompet", 2)]),
    ]
    unique = oas.find_unique_holder_candidates(items, ORDER, set())
    assert len(unique) == 1
    only = list(unique.values())[0]
    assert only["branch"] == "Chrompet"
    assert only["lines"][0]["item"]["part_number"] == "ONLY1"


def test_factory_decision_message_constant():
    assert "Factory Decision Required" in oas.FACTORY_DECISION_MESSAGE
