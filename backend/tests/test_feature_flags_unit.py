"""Unit tests for feature flags and the /api/testing/* guard (no server, no DB)."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi import HTTPException

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import feature_flags as ff  # noqa: E402

FLAG = ff.ENV_SEQUENTIAL_AUTO_SOURCING


def _env(monkeypatch, *, app_env=None, db_name=None, storage=None, flag=None):
    for name, value in (("APP_ENV", app_env), ("DB_NAME", db_name), ("NMTS_STORAGE_ENV", storage), (FLAG, flag)):
        if value is None:
            monkeypatch.delenv(name, raising=False)
        else:
            monkeypatch.setenv(name, value)


def test_production_storage_unset_db_nmts_flag_off_and_testing_routes_404(monkeypatch):
    _env(monkeypatch, db_name="nmts")  # no APP_ENV, NMTS_STORAGE_ENV unset
    assert ff.sequential_auto_sourcing_enabled() is False
    assert ff.public_features() == {"sequential_auto_sourcing": False}
    for path, method in (
        ("/api/testing/cleanup", "POST"),
        ("/api/testing/cleanup/inventory", "GET"),
        ("/api/testing/github/merge", "POST"),
        ("/api/testing/github/status", "GET"),
        ("/api/testing/deployment", "GET"),
        ("/api/testing/overlay/reset", "POST"),
        ("/api/testing/anything-new", "GET"),
        ("/api/testing", "GET"),
        ("/api/testing/runtime", "POST"),
    ):
        assert ff.testing_route_blocked(path, method) is True, (path, method)
    assert ff.testing_route_blocked("/api/testing/runtime", "GET") is False
    assert ff.testing_route_blocked("/api/testing/runtime/", "GET") is False


def test_production_storage_dev_flag_off(monkeypatch):
    _env(monkeypatch, app_env="production", db_name="nmts", storage="dev")
    assert ff.sequential_auto_sourcing_enabled() is False
    assert ff.testing_route_blocked("/api/testing/cleanup", "POST") is True


def test_default_deny_without_positive_testing_detection(monkeypatch):
    # Testing-looking DB/storage values alone never open the routes or the flag.
    _env(monkeypatch, db_name="nmts_testing", storage="testing")
    assert ff.sequential_auto_sourcing_enabled() is False
    assert ff.testing_route_blocked("/api/testing/cleanup", "POST") is True


def test_testing_process_keeps_unfinished_feature_off_but_routes_allowed(monkeypatch):
    _env(monkeypatch, app_env="testing", db_name="nmts_testing", storage="testing")
    assert ff.sequential_auto_sourcing_enabled() is False
    assert ff.public_features() == {"sequential_auto_sourcing": False}
    assert ff.testing_route_blocked("/api/testing/cleanup", "POST") is False
    assert ff.testing_route_blocked("/api/testing/github/merge", "POST") is False


@pytest.mark.parametrize("value,expected", [
    ("true", True), ("1", True), ("yes", True), ("TRUE", True), (" Yes ", True),
    ("on", False), ("enabled", False), ("false", False), ("0", False), ("no", False),
])
def test_explicit_override_accepts_only_true_1_yes(monkeypatch, value, expected):
    _env(monkeypatch, db_name="nmts", flag=value)
    assert ff.sequential_auto_sourcing_enabled() is expected
    _env(monkeypatch, app_env="testing", db_name="nmts_testing", storage="testing", flag=value)
    assert ff.sequential_auto_sourcing_enabled() is expected


def test_empty_override_falls_back_to_default(monkeypatch):
    _env(monkeypatch, db_name="nmts", flag="")
    assert ff.sequential_auto_sourcing_enabled() is False
    _env(monkeypatch, app_env="testing", flag="  ")
    assert ff.sequential_auto_sourcing_enabled() is False


def test_require_raises_403_disabled_when_off(monkeypatch):
    _env(monkeypatch, db_name="nmts")
    with pytest.raises(HTTPException) as exc:
        ff.require_sequential_auto_sourcing()
    assert exc.value.status_code == 403 and exc.value.detail == "disabled"
    _env(monkeypatch, app_env="testing")
    with pytest.raises(HTTPException):
        ff.require_sequential_auto_sourcing()
    _env(monkeypatch, app_env="testing", flag="true")
    ff.require_sequential_auto_sourcing()  # explicit opt-in


def test_non_testing_paths_untouched(monkeypatch):
    _env(monkeypatch, db_name="nmts")
    for path in ("/api/features", "/api/testingx", "/api/orders", "/testing/cleanup", "/api/maintenance/status"):
        assert ff.testing_route_blocked(path, "POST") is False, path
