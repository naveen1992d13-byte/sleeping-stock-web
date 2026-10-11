"""Unit tests for User Hub mobile pairing and branch-transfer guards."""
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

import mobile_api as ma


def test_same_mobile_scope_is_case_insensitive():
    assert ma._same_mobile_scope(
        {"brand_name": "Tata Motors", "dealer_name": "FPL", "branch": "Retteri"},
        {"brand_name": "tata motors", "dealer_name": "fpl", "branch": "retteri"},
    )


def test_same_mobile_scope_rejects_empty_or_different_branch():
    assert not ma._same_mobile_scope(
        {"brand_name": "Tata Motors", "dealer_name": "FPL", "branch": "Retteri"},
        {"brand_name": "Tata Motors", "dealer_name": "FPL", "branch": "Vanagaram"},
    )
    assert not ma._same_mobile_scope(
        {"brand_name": "", "dealer_name": "", "branch": ""},
        {"brand_name": "", "dealer_name": "", "branch": ""},
    )


def test_require_master_or_admin_blocks_user():
    with pytest.raises(HTTPException) as exc:
        ma._require_master_or_admin(SimpleNamespace(role="user"), "change mobile user status")
    assert exc.value.status_code == 403


def test_require_master_or_admin_allows_admin_and_master():
    ma._require_master_or_admin(SimpleNamespace(role="admin"), "change mobile user status")
    ma._require_master_or_admin(SimpleNamespace(role="master"), "transfer a mobile user's branch")


def test_actor_scope_uses_website_assignment_fields():
    actor = ma._actor_scope(SimpleNamespace(brand="Hyundai", group="KUN Auto", location="Chrompet"))
    assert actor == {"brand_name": "Hyundai", "dealer_name": "KUN Auto", "branch": "Chrompet"}


def test_user_scoped_query_is_fixed_branch():
    q = ma._scoped_query_for_user(SimpleNamespace(role="user", brand="Hyundai", group="KUN Auto", location="Chrompet"))
    assert q == {"brand_name": "Hyundai", "dealer_name": "KUN Auto", "branch": "Chrompet"}


def test_user_repair_scope_requires_full_brand_dealer_branch():
    mobile_user = {"brand_name": "Hyundai", "dealer_name": "KUN Auto", "branch": "Chrompet"}
    assert ma._same_mobile_scope(
        mobile_user,
        {"brand_name": "Hyundai", "dealer_name": "KUN Auto", "branch": "Chrompet"},
    )
    assert not ma._same_mobile_scope(
        mobile_user,
        {"brand_name": "Hyundai", "dealer_name": "Other Dealer", "branch": "Chrompet"},
    )


def test_overlay_identifies_mobile_user_and_device_docs():
    from testing_overlay import doc_id

    assert doc_id({"mobile_user_id": "MUVAN2609190001", "branch": "Vanagaram"}) == "MUVAN2609190001"
    assert doc_id({"device_id": "dev-1", "mobile_user_id": "MUVAN2609190001"}) == "dev-1"
    assert doc_id({"id": "web-1", "mobile_user_id": "MUVAN2609190001"}) == "web-1"
