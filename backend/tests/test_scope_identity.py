"""Live API tests for Brand + Dealer Name identity and scoped master CRUD.

Creates only AUDITSI-* records and deletes them afterward. Does not modify
Hyundai / FPL / KUN production masters.
"""
import os
import time
from urllib.parse import quote

import pytest
import requests
from pymongo import MongoClient
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "http://127.0.0.1:8000").rstrip("/")
ADMIN_EMAIL = "admin@sleepingstock.in"
ADMIN_PASSWORD = "admin123"
PREFIX = "AUDITSI"
BRAND_A = f"{PREFIX} Brand A"
BRAND_B = f"{PREFIX} Brand B"
DEALER = "Test Automotive PVT LTD"
LEGAL_NAMES = [
    f"{PREFIX} Alpha PVT LTD",
    f"{PREFIX} Beta Private Limited",
    f"{PREFIX} Gamma Motors LLP",
    f"{PREFIX} Delta-Auto & Co",
]


def _mongo():
    return MongoClient(os.environ["MONGO_URL"])[os.environ["DB_NAME"]]


def _api(path: str) -> str:
    return f"{BASE_URL}/api{path}"


@pytest.fixture(scope="module")
def token():
    response = requests.post(
        _api("/auth/login"),
        json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD},
        timeout=30,
    )
    assert response.status_code == 200, response.text
    return response.json()["access_token"]


@pytest.fixture(scope="module")
def headers(token):
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


def _cleanup(headers):
    """Mongo-first wipe of AUDITSI rows so a failed setup cannot leave orphans."""
    db = _mongo()
    db.users.delete_many({"email": {"$regex": r"^auditsi\."}})
    db.users.delete_many({"name": {"$regex": rf"^{PREFIX}"}})
    db.branches.delete_many({"name": {"$regex": rf"^{PREFIX}"}})
    db.dealers.delete_many({"name": {"$regex": rf"^{PREFIX}"}})
    db.dealers.delete_many({"name": DEALER, "brand": {"$regex": rf"^{PREFIX}"}})
    db.brands.delete_many({"name": {"$regex": rf"^{PREFIX}"}})
    db.brands.delete_many({"code": {"$in": ["SA", "SB"]}})
    db.states.delete_many({"code": "SX"})
    # Best-effort API deletes in case any row was recreated concurrently.
    for row in list(db.branches.find({"name": {"$regex": rf"^{PREFIX}"}})):
        requests.delete(
            _api(f"/masters/branches/{quote(row['name'])}"),
            headers=headers,
            params={"dealer": row.get("dealer"), "brand": row.get("brand")},
            timeout=30,
        )
    for row in list(db.dealers.find({"$or": [{"name": {"$regex": rf"^{PREFIX}"}}, {"name": DEALER, "brand": {"$regex": rf"^{PREFIX}"}}]})):
        requests.delete(
            _api(f"/masters/dealers/{quote(row['name'])}"),
            headers=headers,
            params={"brand": row.get("brand")},
            timeout=30,
        )
    for row in list(db.brands.find({"name": {"$regex": rf"^{PREFIX}"}})):
        requests.delete(_api(f"/masters/brands/{quote(row.get('code') or '')}"), headers=headers, timeout=30)
    db.branches.delete_many({"name": {"$regex": rf"^{PREFIX}"}})
    db.dealers.delete_many({"name": {"$regex": rf"^{PREFIX}"}})
    db.dealers.delete_many({"name": DEALER, "brand": {"$regex": rf"^{PREFIX}"}})
    db.brands.delete_many({"name": {"$regex": rf"^{PREFIX}"}})
    db.brands.delete_many({"code": {"$in": ["SA", "SB"]}})


def _assert_ok(response, label):
    assert response.status_code == 200, f"{label}: {response.status_code} {response.text}"


@pytest.fixture(scope="module")
def masters(headers):
    _cleanup(headers)
    try:
        _assert_ok(requests.post(_api("/masters/brands"), headers=headers, json={"code": "SA", "name": BRAND_A}, timeout=30), "create brand A")
        _assert_ok(requests.post(_api("/masters/brands"), headers=headers, json={"code": "SB", "name": BRAND_B}, timeout=30), "create brand B")
        _assert_ok(requests.post(_api("/masters/dealers"), headers=headers, json={"name": DEALER, "brand": BRAND_A}, timeout=30), "create dealer A")
        _assert_ok(requests.post(_api("/masters/dealers"), headers=headers, json={"name": DEALER, "brand": BRAND_B}, timeout=30), "create dealer B")
        for name in ("AUDITSI A-Chennai", "AUDITSI A-Coimbatore"):
            _assert_ok(requests.post(_api("/masters/branches"), headers=headers, json={"name": name, "dealer": DEALER, "brand": BRAND_A}, timeout=30), f"create {name}")
        for name in ("AUDITSI B-Mumbai", "AUDITSI B-Pune"):
            _assert_ok(requests.post(_api("/masters/branches"), headers=headers, json={"name": name, "dealer": DEALER, "brand": BRAND_B}, timeout=30), f"create {name}")
        yield
    finally:
        _cleanup(headers)


def test_same_dealer_name_two_brands(headers, masters):
    db = _mongo()
    rows = list(db.dealers.find({"name": DEALER, "brand": {"$regex": rf"^{PREFIX}"}}, {"_id": 0, "name": 1, "brand": 1}))
    assert {(r["name"], r["brand"]) for r in rows} == {(DEALER, BRAND_A), (DEALER, BRAND_B)}
    dup = requests.post(_api("/masters/dealers"), headers=headers, json={"name": DEALER, "brand": BRAND_A}, timeout=30)
    assert dup.status_code == 400


def test_scope_options_keeps_brand_on_dealers(headers, masters):
    scope = requests.get(_api("/scope/options"), headers=headers, timeout=30).json()
    mapped = [d for d in scope["dealers"] if d.get("name") == DEALER and str(d.get("brand") or "").startswith(PREFIX)]
    assert len(mapped) == 2
    a_dealers = [d["name"] for d in scope["dealers"] if (d.get("brand") or d.get("brand_name")) == BRAND_A]
    b_dealers = [d["name"] for d in scope["dealers"] if (d.get("brand") or d.get("brand_name")) == BRAND_B]
    assert DEALER in a_dealers and DEALER in b_dealers
    a_branches = [b["name"] for b in scope["branches"] if (b.get("brand") or b.get("brand_name")) == BRAND_A and (b.get("dealer") or b.get("dealer_name")) == DEALER]
    b_branches = [b["name"] for b in scope["branches"] if (b.get("brand") or b.get("brand_name")) == BRAND_B and (b.get("dealer") or b.get("dealer_name")) == DEALER]
    assert set(a_branches) == {"AUDITSI A-Chennai", "AUDITSI A-Coimbatore"}
    assert set(b_branches) == {"AUDITSI B-Mumbai", "AUDITSI B-Pune"}


def test_branch_create_without_brand_is_rejected_when_name_is_ambiguous(headers, masters):
    res = requests.post(_api("/masters/branches"), headers=headers, json={"name": "AUDITSI Shared-X", "dealer": DEALER}, timeout=30)
    assert res.status_code == 400
    assert "Brand is required" in res.text or "multiple brands" in res.text


def test_rename_dealer_does_not_touch_other_brand(headers, masters):
    db = _mongo()
    res = requests.put(
        _api(f"/masters/dealers/{quote(DEALER)}"),
        headers=headers,
        params={"brand": BRAND_A},
        json={"name": DEALER, "brand": BRAND_A},
        timeout=30,
    )
    assert res.status_code == 200, res.text
    renamed = requests.put(
        _api(f"/masters/dealers/{quote(DEALER)}"),
        headers=headers,
        params={"brand": BRAND_A},
        json={"name": f"{PREFIX} Company A Renamed", "brand": BRAND_A},
        timeout=30,
    )
    assert renamed.status_code == 200, renamed.text
    a_doc = db.dealers.find_one({"brand": BRAND_A, "name": f"{PREFIX} Company A Renamed"})
    b_doc = db.dealers.find_one({"brand": BRAND_B, "name": DEALER})
    assert a_doc is not None
    assert b_doc is not None
    a_branches = list(db.branches.find({"brand": BRAND_A, "name": {"$regex": r"^AUDITSI A-"}}))
    b_branches = list(db.branches.find({"brand": BRAND_B, "name": {"$regex": r"^AUDITSI B-"}}))
    assert all(row.get("dealer") == f"{PREFIX} Company A Renamed" for row in a_branches)
    assert all(row.get("dealer") == DEALER for row in b_branches)
    restore = requests.put(
        _api(f"/masters/dealers/{quote(PREFIX + ' Company A Renamed')}"),
        headers=headers,
        params={"brand": BRAND_A},
        json={"name": DEALER, "brand": BRAND_A},
        timeout=30,
    )
    assert restore.status_code == 200, restore.text


def test_delete_branch_is_brand_scoped(headers, masters):
    db = _mongo()
    shared = "AUDITSI SharedBranch"
    assert requests.post(_api("/masters/branches"), headers=headers, json={"name": shared, "dealer": DEALER, "brand": BRAND_A}, timeout=30).status_code == 200
    assert requests.post(_api("/masters/branches"), headers=headers, json={"name": shared, "dealer": DEALER, "brand": BRAND_B}, timeout=30).status_code == 200
    deleted = requests.delete(
        _api(f"/masters/branches/{quote(shared)}"),
        headers=headers,
        params={"dealer": DEALER, "brand": BRAND_A},
        timeout=30,
    )
    assert deleted.status_code == 200, deleted.text
    remaining = list(db.branches.find({"name": shared}, {"_id": 0, "brand": 1, "dealer": 1, "brand_name": 1, "dealer_name": 1}))
    assert len(remaining) == 1
    assert remaining[0].get("brand") == BRAND_B or remaining[0].get("brand_name") == BRAND_B
    assert remaining[0].get("dealer") == DEALER or remaining[0].get("dealer_name") == DEALER
    requests.delete(
        _api(f"/masters/branches/{quote(shared)}"),
        headers=headers,
        params={"dealer": DEALER, "brand": BRAND_B},
        timeout=30,
    )


def test_legal_dealer_names_round_trip(headers, masters):
    db = _mongo()
    for name in LEGAL_NAMES:
        res = requests.post(_api("/masters/dealers"), headers=headers, json={"name": name, "brand": BRAND_A}, timeout=30)
        assert res.status_code == 200, f"{name}: {res.text}"
        stored = db.dealers.find_one({"name": name, "brand": BRAND_A})
        assert stored is not None
        assert stored["name"] == name


def test_brand_rename_cascades_mapped_fields_only(headers, masters):
    db = _mongo()
    res = requests.put(_api("/masters/brands/SA"), headers=headers, json={"code": "SA", "name": f"{PREFIX} Brand A Edited"}, timeout=30)
    assert res.status_code == 200, res.text
    assert db.dealers.find_one({"name": DEALER, "brand": f"{PREFIX} Brand A Edited"})
    assert db.dealers.find_one({"name": DEALER, "brand": BRAND_B})
    assert not db.dealers.find_one({"name": DEALER, "brand": BRAND_A})
    assert db.branches.find_one({"name": "AUDITSI A-Chennai", "brand": f"{PREFIX} Brand A Edited"})
    restore = requests.put(_api("/masters/brands/SA"), headers=headers, json={"code": "SA", "name": BRAND_A}, timeout=30)
    assert restore.status_code == 200, restore.text


def test_user_hub_edit_preserves_user_id(headers, masters):
    states = requests.get(_api("/masters/states"), headers=headers, timeout=30).json()
    assert states, "State Master must contain at least one state"
    ts = int(time.time())
    payload = {
        "userId": "",
        "name": f"{PREFIX} Edit User",
        "mobile": f"92000{ts % 100000:05d}",
        "email": f"auditsi.user.{ts}@example.invalid",
        "role": "user",
        "state": states[0]["name"],
        "brand": BRAND_A,
        "dealer": DEALER,
        "branch": "AUDITSI A-Chennai",
        "password": "AuditTest123!",
        "confirmPassword": "AuditTest123!",
        "status": "active",
        "permissions": [],
    }
    created = requests.post(_api("/users/create"), headers=headers, json=payload, timeout=30)
    assert created.status_code == 200, created.text
    db = _mongo()
    user = db.users.find_one({"email": payload["email"]}, {"_id": 0, "password": 0})
    assert user
    user_id_value = user.get("userId") or user.get("user_id")
    updated = requests.put(
        _api(f"/users/hub/{user['id']}"),
        headers=headers,
        json={"name": f"{PREFIX} Edit User Updated", "branch": "AUDITSI A-Coimbatore"},
        timeout=30,
    )
    assert updated.status_code == 200, updated.text
    after = db.users.find_one({"id": user["id"]}, {"_id": 0, "password": 0})
    assert after["name"] == f"{PREFIX} Edit User Updated"
    assert after.get("userId") == user_id_value or after.get("user_id") == user_id_value
    assert after.get("branch") == "AUDITSI A-Coimbatore"
    requests.delete(_api(f"/users/{user['id']}"), headers=headers, timeout=30)
    db.users.delete_many({"email": payload["email"]})


def test_mobile_pairing_rejects_cross_brand_same_dealer(headers, masters):
    res = requests.post(
        _api("/mobile/pairing/generate"),
        headers={**headers, "X-Forwarded-Proto": "https", "X-Forwarded-Host": "nmts-pairing-test.example.com"},
        json={
            "pairing_type": "NEW",
            "brand_name": BRAND_A,
            "dealer_name": DEALER,
            "branch": "AUDITSI B-Mumbai",
        },
        timeout=30,
    )
    assert res.status_code == 400, res.text
    ok = requests.post(
        _api("/mobile/pairing/generate"),
        headers={**headers, "X-Forwarded-Proto": "https", "X-Forwarded-Host": "nmts-pairing-test.example.com"},
        json={
            "pairing_type": "NEW",
            "brand_name": BRAND_A,
            "dealer_name": DEALER,
            "branch": "AUDITSI A-Chennai",
        },
        timeout=30,
    )
    assert ok.status_code == 200, ok.text
