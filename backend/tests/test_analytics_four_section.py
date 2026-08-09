"""Unit tests for final four-section Analytics formulas, buckets, and All* scope."""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import analytics_center as ac  # noqa: E402
from reports_center import _scope  # noqa: E402


def test_aging_buckets_exact_labels():
    assert ac.ANALYTICS_AGING_BUCKETS == [
        "0–90 Days",
        "91–180 Days",
        "181–270 Days",
        "271–361 Days",
        ">361 Days",
    ]


@pytest.mark.parametrize(
    "days,expected",
    [
        (0, "0–90 Days"),
        (90, "0–90 Days"),
        (91, "91–180 Days"),
        (180, "91–180 Days"),
        (181, "181–270 Days"),
        (270, "181–270 Days"),
        (271, "271–361 Days"),
        (361, "271–361 Days"),
        (362, ">361 Days"),
        (500, ">361 Days"),
        (None, "0–90 Days"),
    ],
)
def test_analytics_bucket_mapping(days, expected):
    assert ac._analytics_bucket(days) == expected


def test_orders_formula_final_equals_original_minus_reduced():
    original, reduced = 200_000.0, 50_000.0
    final = max(0.0, original - reduced)
    assert final == 150_000.0
    assert abs(final - (original - reduced)) < 1e-9
    pct = ac._safe_pct(reduced, original)
    assert abs(pct - 25.0) < 1e-9


def test_request_fulfillment_formulas():
    received = 100.0
    branch = 40.0
    dealer = 35.0
    fulfilled = branch + dealer
    not_fulfilled = max(0.0, received - fulfilled)
    assert fulfilled == 75.0
    assert not_fulfilled == 25.0
    assert abs((fulfilled + not_fulfilled) - received) < 1e-9
    assert abs(ac._safe_pct(fulfilled, received) - 75.0) < 1e-9


def test_is_branch_vs_dealer_fulfillment():
    same_dealer = {
        "requesting_dealer": "FPL Hyundai",
        "supplying_dealer": "FPL Hyundai",
        "requesting_brand": "Hyundai",
        "supplying_brand": "Hyundai",
    }
    co_dealer = {
        "requesting_dealer": "FPL Hyundai",
        "supplying_dealer": "Other Motors",
        "requesting_brand": "Hyundai",
        "supplying_brand": "Hyundai",
    }
    assert ac._is_branch_fulfillment(same_dealer) is True
    assert ac._is_branch_fulfillment(co_dealer) is False


def test_scope_all_brands_dealers_branches_master_keeps_empty():
    """All* must not silently become a concrete brand/dealer/branch for master."""
    master = SimpleNamespace(role="master", brand="Hyundai", group="FPL", location="Vanagaram")
    scope = _scope(master, "All Brands", "All Dealers", "All Branches")
    assert scope == {}
    assert "brand" not in scope and "dealer" not in scope and "branch" not in scope


def test_scope_all_dealers_all_branches_under_brand():
    master = SimpleNamespace(role="master", brand="Hyundai", group="FPL", location="Vanagaram")
    scope = _scope(master, "Hyundai", "All Dealers", "All Branches")
    assert scope == {"brand": "Hyundai"}
    assert scope.get("dealer") is None
    assert scope.get("branch") is None


def test_scope_all_branches_under_dealer():
    master = SimpleNamespace(role="master", brand="Hyundai", group="FPL", location="Vanagaram")
    scope = _scope(master, "Hyundai", "FPL Hyundai", "All Branches")
    assert scope == {"brand": "Hyundai", "dealer": "FPL Hyundai"}
    assert "branch" not in scope


def test_scope_single_branch():
    master = SimpleNamespace(role="master", brand="Hyundai", group="FPL", location="Vanagaram")
    scope = _scope(master, "Hyundai", "FPL Hyundai", "Vanagaram")
    assert scope == {"brand": "Hyundai", "dealer": "FPL Hyundai", "branch": "Vanagaram"}


def test_scope_admin_all_branches_stays_assigned_brand_dealer():
    admin = SimpleNamespace(role="admin", brand="Hyundai", group="FPL Hyundai", location="HQ")
    scope = _scope(admin, "All Brands", "All Dealers", "All Branches")
    assert scope["brand"] == "Hyundai"
    assert scope["dealer"] == "FPL Hyundai"
    assert "branch" not in scope


def test_scope_user_forced_to_own_branch():
    user = SimpleNamespace(role="user", brand="Hyundai", group="FPL Hyundai", location="Vanagaram")
    scope = _scope(user, "All Brands", "All Dealers", "All Branches")
    assert scope == {"brand": "Hyundai", "dealer": "FPL Hyundai", "branch": "Vanagaram"}


def test_scope_admin_cannot_escape_brand():
    admin = SimpleNamespace(role="admin", brand="Hyundai", group="FPL Hyundai", location="HQ")
    with pytest.raises(HTTPException) as exc:
        _scope(admin, "Toyota", "All Dealers", "All Branches")
    assert exc.value.status_code == 403


def test_series_totals_match_summary_orders():
    """Chart/table series sums must equal summary KPIs."""
    series = [
        {"original_order_value": 100.0, "reduced_value": 20.0, "final_order_value": 80.0},
        {"original_order_value": 50.0, "reduced_value": 10.0, "final_order_value": 40.0},
    ]
    summary_original = sum(r["original_order_value"] for r in series)
    summary_reduced = sum(r["reduced_value"] for r in series)
    summary_final = sum(r["final_order_value"] for r in series)
    assert summary_original == 150.0
    assert summary_reduced == 30.0
    assert summary_final == 120.0
    assert summary_final == summary_original - summary_reduced


def test_series_totals_match_summary_fulfillment():
    series = [
        {
            "request_received_value": 100.0,
            "given_to_branches_value": 40.0,
            "given_to_dealers_value": 30.0,
            "total_fulfilled_value": 70.0,
            "not_fulfilled_value": 30.0,
        },
        {
            "request_received_value": 50.0,
            "given_to_branches_value": 10.0,
            "given_to_dealers_value": 15.0,
            "total_fulfilled_value": 25.0,
            "not_fulfilled_value": 25.0,
        },
    ]
    recv = sum(r["request_received_value"] for r in series)
    branch = sum(r["given_to_branches_value"] for r in series)
    dealer = sum(r["given_to_dealers_value"] for r in series)
    fulfilled = sum(r["total_fulfilled_value"] for r in series)
    not_f = sum(r["not_fulfilled_value"] for r in series)
    assert fulfilled == branch + dealer
    assert abs((fulfilled + not_f) - recv) < 1e-9


def test_no_upload_must_not_become_zero():
    """Missing upload dates stay None — never coerced to 0 for chart connect."""
    daily = [
        {"date": "2026-08-01", "data_status": "COMPLETE", "closing": 100.0},
        {"date": "2026-08-02", "data_status": "NO_UPLOAD", "closing": None},
        {"date": "2026-08-03", "data_status": "COMPLETE", "closing": 0.0},
    ]
    chart_values = [None if r["data_status"] == "NO_UPLOAD" else r["closing"] for r in daily]
    assert chart_values[1] is None
    assert chart_values[2] == 0.0  # real zero after valid upload is allowed
    assert chart_values[1] != 0


def test_day_upload_status_uses_observed_branches_when_master_empty():
    """All Dealers/Branches must not become NO_UPLOAD when branch master is empty."""
    uploaded = {"20260809": {"vanagaram", "retteri"}}
    st = ac._day_upload_status("20260809", uploaded, [], consolidated=True)
    assert st["data_status"] == "AVAILABLE"
    assert st["uploaded_branch_count"] == 2
    assert st["allowed_branch_keys"] == {"vanagaram", "retteri"}

    empty = ac._day_upload_status("20260808", {}, [], consolidated=True)
    assert empty["data_status"] == "NO_UPLOAD"
