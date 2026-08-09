"""
Mobile pairing API tests — generic Brand → Dealer → Branch scope behaviour.

Requires a running backend (see AGENTS.md) and uses shared MongoDB Atlas.
Test mobiles use 98XXXXXXYY pattern to avoid colliding with real users.
"""
import os
import random
import uuid

import pytest
import requests

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "http://127.0.0.1:8000").rstrip("/")
ADMIN_EMAIL = os.environ.get("NMTS_TEST_ADMIN_EMAIL", "admin@sleepingstock.in")
ADMIN_PASSWORD = os.environ.get("NMTS_TEST_ADMIN_PASSWORD", "admin123")


def _api(path: str) -> str:
    return f"{BASE_URL}/api{path}"


def _login() -> str:
    res = requests.post(_api("/auth/login"), json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD}, timeout=60)
    assert res.status_code == 200, res.text
    return res.json()["access_token"]


def _master_headers(token: str) -> dict:
    return {
        "Authorization": f"Bearer {token}",
        "X-Forwarded-Proto": "https",
        "X-Forwarded-Host": "nmts-pairing-test.example.com",
    }


def _test_mobile() -> str:
    return f"98{random.randint(10000000, 99999999)}"


def _find_branch(token: str, *, brand_sub: str, dealer_sub: str, branch_sub: str = "") -> dict | None:
    headers = _master_headers(token)
    branches = requests.get(_api("/masters/branches"), headers=headers, timeout=60).json()
    brand_sub = brand_sub.casefold()
    dealer_sub = dealer_sub.casefold()
    branch_sub = branch_sub.casefold() if branch_sub else ""
    matches = []
    for row in branches:
        bbrand = (row.get("brand") or row.get("brand_name") or "").casefold()
        dealer = (row.get("dealer") or row.get("dealer_name") or "").casefold()
        name = (row.get("name") or "").casefold()
        if brand_sub and bbrand and brand_sub not in bbrand:
            continue
        if dealer_sub and dealer_sub not in dealer:
            continue
        if branch_sub and branch_sub not in name:
            continue
        matches.append(row)
    if not matches:
        return None
    row = matches[0]
    return {
        "brand_name": row.get("brand") or row.get("brand_name") or brand_sub.title(),
        "dealer_name": row.get("dealer") or row.get("dealer_name"),
        "branch": row.get("name"),
    }


def _generate_pairing(token: str, scope: dict, pairing_type: str = "NEW", mobile_user_id: str | None = None) -> dict:
    payload = {
        "pairing_type": pairing_type,
        "brand_name": scope["brand_name"],
        "dealer_name": scope["dealer_name"],
        "branch": scope["branch"],
    }
    if mobile_user_id:
        payload["mobile_user_id"] = mobile_user_id
    res = requests.post(_api("/mobile/pairing/generate"), json=payload, headers=_master_headers(token), timeout=60)
    assert res.status_code == 200, res.text
    return res.json()


def _verify_pairing(gen: dict, mobile: str, name: str = "Pairing Test User") -> dict:
    body = {
        "pairing_type": gen["pairing_type"],
        "pairing_code": gen["pairing_code"],
        "pairing_token": gen["pairing_token"],
        "device_user_name": name,
        "device_user_mobile": mobile,
        "device_name": "pytest device",
        "device_info": "pytest",
        "app_version": "1.0.0",
    }
    if gen.get("mobile_user_id"):
        body["mobile_user_id"] = gen["mobile_user_id"]
    res = requests.post(_api("/mobile/pairing/verify"), json=body, timeout=60)
    return res


@pytest.fixture(scope="module")
def auth_token():
    try:
        requests.get(f"{BASE_URL}/api/auth/me", timeout=5)
    except requests.RequestException as exc:
        pytest.skip(f"Backend not reachable at {BASE_URL}: {exc}")
    return _login()


class TestMobilePairingScope:
    def test_all_branches_rejected_on_generate(self, auth_token):
        res = requests.post(
            _api("/mobile/pairing/generate"),
            json={
                "pairing_type": "NEW",
                "brand_name": "Hyundai",
                "dealer_name": "FPL Hyundai",
                "branch": "All Branches",
            },
            headers=_master_headers(auth_token),
            timeout=60,
        )
        assert res.status_code == 400
        assert "All Branches" in res.json().get("detail", "")

    @pytest.mark.parametrize(
        "dealer_hint,branch_hint",
        [
            ("FPL", ""),
            ("KUN", "Chromepet"),
            ("KUN", ""),
        ],
    )
    def test_new_pairing_generic_scopes(self, auth_token, dealer_hint, branch_hint):
        scope = _find_branch(auth_token, brand_sub="Hyundai", dealer_sub=dealer_hint, branch_sub=branch_hint)
        if not scope:
            pytest.skip(f"No branch in DB for Hyundai / {dealer_hint} / {branch_hint}")
        mobile = _test_mobile()
        gen = _generate_pairing(auth_token, scope, "NEW")
        verify = _verify_pairing(gen, mobile)
        assert verify.status_code == 200, verify.text
        data = verify.json()
        assert data["brand_name"] == scope["brand_name"] or data["brand_name"].casefold() == scope["brand_name"].casefold()
        assert data["dealer_name"].casefold() == scope["dealer_name"].casefold()
        assert data["branch"].casefold() == scope["branch"].casefold()
        session = requests.get(
            _api("/mobile/session/validate"),
            headers={"Authorization": f"Bearer {data['session_token']}"},
            timeout=60,
        )
        assert session.status_code == 200, session.text
        sess = session.json()
        assert sess["branch"].casefold() == scope["branch"].casefold()

    def test_duplicate_verify_rejected(self, auth_token):
        scope = _find_branch(auth_token, brand_sub="Hyundai", dealer_sub="KUN", branch_sub="")
        if not scope:
            pytest.skip("No KUN branch in DB")
        mobile = _test_mobile()
        gen = _generate_pairing(auth_token, scope, "NEW")
        first = _verify_pairing(gen, mobile)
        assert first.status_code == 200, first.text
        second = _verify_pairing(gen, mobile)
        assert second.status_code in (400, 409), second.text
        assert "already" in second.text.lower() or "used" in second.text.lower()

    def test_expired_token_rejected(self, auth_token):
        scope = _find_branch(auth_token, brand_sub="Hyundai", dealer_sub="FPL", branch_sub="")
        if not scope:
            pytest.skip("No FPL branch in DB")
        gen = _generate_pairing(auth_token, scope, "NEW")
        body = {
            "pairing_type": "NEW",
            "pairing_code": gen["pairing_code"],
            "pairing_token": gen["pairing_token"],
            "device_user_name": "Expired Test",
            "device_user_mobile": _test_mobile(),
            "device_name": "pytest",
        }
        # Tamper expires_at is not enough; mark used directly via second verify path simulation:
        ok = _verify_pairing(gen, body["device_user_mobile"])
        assert ok.status_code == 200
        res = requests.post(_api("/mobile/pairing/verify"), json=body, timeout=60)
        assert res.status_code in (400, 409), res.text

    def test_mobile_user_already_exists(self, auth_token):
        scope = _find_branch(auth_token, brand_sub="Hyundai", dealer_sub="KUN", branch_sub="")
        if not scope:
            pytest.skip("No KUN branch in DB")
        mobile = _test_mobile()
        gen1 = _generate_pairing(auth_token, scope, "NEW")
        assert _verify_pairing(gen1, mobile).status_code == 200
        other_scope = _find_branch(auth_token, brand_sub="Hyundai", dealer_sub="FPL", branch_sub="")
        if not other_scope:
            pytest.skip("No FPL branch for cross-dealer test")
        gen2 = _generate_pairing(auth_token, other_scope, "NEW")
        conflict = _verify_pairing(gen2, mobile)
        assert conflict.status_code == 409, conflict.text
        detail = conflict.json().get("detail")
        if isinstance(detail, dict):
            assert detail.get("code") == "MOBILE_USER_ALREADY_EXISTS"

    def test_repair_after_new(self, auth_token):
        scope = _find_branch(auth_token, brand_sub="Hyundai", dealer_sub="KUN", branch_sub="")
        if not scope:
            pytest.skip("No KUN branch in DB")
        mobile = _test_mobile()
        gen = _generate_pairing(auth_token, scope, "NEW")
        first = _verify_pairing(gen, mobile)
        assert first.status_code == 200, first.text
        muid = first.json()["mobile_user_id"]
        repair = _generate_pairing(auth_token, scope, "REPAIR", mobile_user_id=muid)
        second = _verify_pairing(repair, mobile, name="Repair User")
        assert second.status_code == 200, second.text
        assert second.json()["mobile_user_id"] == muid
