"""Live API tests for User Hub State Master / Brand Master source of truth.

Requires a running backend (see AGENTS.md) and talks to the shared Atlas DB.
Creates only TN/KL and a temporary brand, then deletes those same records.
"""
import os

import pytest
import requests
from pymongo import MongoClient
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "http://127.0.0.1:8000").rstrip("/")
ADMIN_EMAIL = "admin@sleepingstock.in"
ADMIN_PASSWORD = "admin123"
TEMP_BRAND_CODE = "ZT"
TEMP_BRAND_NAME = "ZTest Brand d6e5"
TEMP_BRAND_NAME_EDITED = "ZTest Brand Edited d6e5"


def _mongo():
    return MongoClient(os.environ["MONGO_URL"])[os.environ["DB_NAME"]]


@pytest.fixture(scope="module")
def auth_headers():
    response = requests.post(
        f"{BASE_URL}/api/auth/login",
        json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD},
        timeout=30,
    )
    assert response.status_code == 200, response.text
    token = response.json()["access_token"]
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


def _api_list(path, headers):
    response = requests.get(f"{BASE_URL}/api{path}", headers=headers, timeout=30)
    assert response.status_code == 200, response.text
    data = response.json()
    assert isinstance(data, list)
    return data


def _codes(rows):
    return sorted(str(row.get("code") or "") for row in rows)


def _delete_if_present(kind, code, headers):
    response = requests.delete(f"{BASE_URL}/api/masters/{kind}/{code}", headers=headers, timeout=30)
    assert response.status_code in (200, 404), response.text


@pytest.fixture(scope="module")
def clean_temp_masters(auth_headers):
    for code in ("TN", "KL"):
        _delete_if_present("states", code, auth_headers)
    _delete_if_present("brands", TEMP_BRAND_CODE, auth_headers)
    yield
    for code in ("TN", "KL"):
        _delete_if_present("states", code, auth_headers)
    _delete_if_present("brands", TEMP_BRAND_CODE, auth_headers)


class TestMasterDataSourceOfTruth:
    def test_empty_brand_list_is_not_hyundai_fallback(self, auth_headers, clean_temp_masters):
        db = _mongo()
        api_brands = _api_list("/masters/brands", auth_headers)
        mongo_brands = list(db.brands.find({}, {"_id": 0, "code": 1, "name": 1}))
        assert len(api_brands) == db.brands.count_documents({})
        assert not any(
            str(row.get("code") or "").upper() == "HY" or str(row.get("name") or "").lower() == "hyundai"
            for row in api_brands
        )
        assert mongo_brands == [] or all(row.get("code") != "HY" for row in mongo_brands)

        ghost = requests.delete(f"{BASE_URL}/api/masters/brands/HY", headers=auth_headers, timeout=30)
        assert ghost.status_code == 404
        assert ghost.json()["detail"] == "Brand not found"

    def test_state_crud_tn_kl_no_reseed(self, auth_headers, clean_temp_masters):
        db = _mongo()
        headers = auth_headers

        create_tn = requests.post(
            f"{BASE_URL}/api/masters/states",
            headers=headers,
            json={"code": "TN", "name": "Tamil Nadu"},
            timeout=30,
        )
        assert create_tn.status_code == 200, create_tn.text
        create_kl = requests.post(
            f"{BASE_URL}/api/masters/states",
            headers=headers,
            json={"code": "KL", "name": "Kerala"},
            timeout=30,
        )
        assert create_kl.status_code == 200, create_kl.text

        api_states = _api_list("/masters/states", headers)
        assert _codes(api_states) == ["KL", "TN"]
        assert db.states.count_documents({"code": {"$in": ["TN", "KL"]}}) == 2

        delete_tn = requests.delete(f"{BASE_URL}/api/masters/states/TN", headers=headers, timeout=30)
        assert delete_tn.status_code == 200, delete_tn.text
        api_states = _api_list("/masters/states", headers)
        assert "TN" not in _codes(api_states)
        assert "KL" in _codes(api_states)
        assert db.states.count_documents({"code": "TN"}) == 0
        assert db.states.count_documents({"code": "KL"}) == 1

        delete_kl = requests.delete(f"{BASE_URL}/api/masters/states/KL", headers=headers, timeout=30)
        assert delete_kl.status_code == 200, delete_kl.text
        api_states = _api_list("/masters/states", headers)
        assert "KL" not in _codes(api_states)
        assert "TN" not in _codes(api_states)
        assert db.states.count_documents({"code": {"$in": ["TN", "KL"]}}) == 0

    def test_brand_crud_matches_mongodb(self, auth_headers, clean_temp_masters):
        db = _mongo()
        headers = auth_headers

        created = requests.post(
            f"{BASE_URL}/api/masters/brands",
            headers=headers,
            json={"code": TEMP_BRAND_CODE, "name": TEMP_BRAND_NAME},
            timeout=30,
        )
        assert created.status_code == 200, created.text
        api_brands = _api_list("/masters/brands", headers)
        assert any(row.get("code") == TEMP_BRAND_CODE and row.get("name") == TEMP_BRAND_NAME for row in api_brands)
        assert db.brands.count_documents({"code": TEMP_BRAND_CODE}) == 1

        edited = requests.put(
            f"{BASE_URL}/api/masters/brands/{TEMP_BRAND_CODE}",
            headers=headers,
            json={"code": TEMP_BRAND_CODE, "name": TEMP_BRAND_NAME_EDITED},
            timeout=30,
        )
        assert edited.status_code == 200, edited.text
        mongo_brand = db.brands.find_one({"code": TEMP_BRAND_CODE}, {"_id": 0, "name": 1})
        api_brands = _api_list("/masters/brands", headers)
        assert mongo_brand["name"] == TEMP_BRAND_NAME_EDITED
        assert any(row.get("name") == TEMP_BRAND_NAME_EDITED for row in api_brands)

        deleted = requests.delete(
            f"{BASE_URL}/api/masters/brands/{TEMP_BRAND_CODE}",
            headers=headers,
            timeout=30,
        )
        assert deleted.status_code == 200, deleted.text
        api_brands = _api_list("/masters/brands", headers)
        assert all(row.get("code") != TEMP_BRAND_CODE for row in api_brands)
        assert db.brands.count_documents({"code": TEMP_BRAND_CODE}) == 0

    def test_user_id_uses_saved_master_codes_only(self, auth_headers, clean_temp_masters):
        headers = auth_headers
        missing = requests.get(
            f"{BASE_URL}/api/users/generate-id",
            headers=headers,
            params={"state_code": "TN", "brand_code": "HY"},
            timeout=30,
        )
        assert missing.status_code == 400, missing.text

        assert requests.post(
            f"{BASE_URL}/api/masters/states",
            headers=headers,
            json={"code": "TN", "name": "Tamil Nadu"},
            timeout=30,
        ).status_code == 200
        assert requests.post(
            f"{BASE_URL}/api/masters/brands",
            headers=headers,
            json={"code": TEMP_BRAND_CODE, "name": TEMP_BRAND_NAME},
            timeout=30,
        ).status_code == 200

        generated = requests.get(
            f"{BASE_URL}/api/users/generate-id",
            headers=headers,
            params={"state_code": "Tamil Nadu", "brand_code": TEMP_BRAND_NAME},
            timeout=30,
        )
        assert generated.status_code == 200, generated.text
        user_id = generated.json()["user_id"]
        assert user_id.startswith(f"SSTN{TEMP_BRAND_CODE}")

        _delete_if_present("states", "TN", headers)
        _delete_if_present("brands", TEMP_BRAND_CODE, headers)

    def test_master_admin_preserved(self):
        db = _mongo()
        admin = db.users.find_one({"email": ADMIN_EMAIL}, {"_id": 0, "role": 1, "email": 1})
        assert admin is not None
        assert admin["role"] == "master"
