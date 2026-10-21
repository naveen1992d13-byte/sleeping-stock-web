import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import mobile_api as ma

FPL = ("Hyundai", "FPL automobiles pvt ltd", "Koyambedu")


def _user(role, brand="Hyundai", group="FPL automobiles pvt ltd", location="Koyambedu"):
    return SimpleNamespace(role=role, brand=brand, group=group, location=location)


def test_master_any_scope():
    ma._require_branch_scope(_user("master", "", "", ""), "Tata Motors", "Any", "Any")


def test_admin_own_dealer_any_branch_case_insensitive():
    ma._require_branch_scope(_user("admin"), "hyundai", "FPL Automobiles Pvt Ltd", "Vanagaram")


def test_admin_other_dealer_rejected():
    with pytest.raises(HTTPException) as exc:
        ma._require_branch_scope(_user("admin", group="KUN Auto Company Pvt Ltd"), *FPL)
    assert exc.value.status_code == 403


def test_user_other_branch_rejected():
    ma._require_branch_scope(_user("user"), *FPL)
    with pytest.raises(HTTPException) as exc:
        ma._require_branch_scope(_user("user"), "Hyundai", "FPL automobiles pvt ltd", "Vanagaram")
    assert exc.value.status_code == 403
