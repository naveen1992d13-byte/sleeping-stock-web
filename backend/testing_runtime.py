"""Testing-environment runtime helpers.

Production stays on APP_ENV unset/production, DB_NAME=nmts, NMTS_STORAGE_ENV=dev.
These helpers never print secrets and never connect to a database themselves.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Mapping, Optional

TESTING_APP_ENV = "testing"
TESTING_DB_NAME = "nmts_testing"
TESTING_STORAGE_ENV = "testing"
PRODUCTION_DB_NAME = "nmts"
PRODUCTION_STORAGE_ENV = "dev"
TS_PREFIX = "TS-"
SNAPSHOT_META_FILENAME = "testing_snapshot_active.json"
DEPLOYMENT_FILENAME = "deployment.json"
DATA_ORIGIN_SNAPSHOT = "snapshot"
DATA_ORIGIN_TESTING = "testing"

_BACKEND_DIR = Path(__file__).resolve().parent


def _env(name: str, default: str = "") -> str:
    return str(os.getenv(name, default) or default).strip()


def app_env() -> str:
    return _env("APP_ENV").lower()


def is_testing_env() -> bool:
    return app_env() == TESTING_APP_ENV


def db_name() -> str:
    return _env("DB_NAME")


def storage_env_name() -> str:
    return _env("NMTS_STORAGE_ENV") or PRODUCTION_STORAGE_ENV


def assert_env_isolation() -> None:
    """Fail startup when testing/production database or S3 prefixes are crossed."""
    env = app_env()
    name = db_name()
    storage = storage_env_name()
    if env == TESTING_APP_ENV:
        if name != TESTING_DB_NAME:
            raise RuntimeError(
                f"APP_ENV=testing requires DB_NAME={TESTING_DB_NAME}, got {name!r}"
            )
        if storage != TESTING_STORAGE_ENV:
            raise RuntimeError(
                f"APP_ENV=testing requires NMTS_STORAGE_ENV={TESTING_STORAGE_ENV}, got {storage!r}"
            )
        return
    if name == TESTING_DB_NAME:
        raise RuntimeError(
            f"DB_NAME={TESTING_DB_NAME} is only allowed when APP_ENV={TESTING_APP_ENV}"
        )
    if storage == TESTING_STORAGE_ENV:
        raise RuntimeError(
            f"NMTS_STORAGE_ENV={TESTING_STORAGE_ENV} is only allowed when APP_ENV={TESTING_APP_ENV}"
        )


def _read_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def deployment_metadata_path() -> Path:
    override = _env("NMTS_DEPLOYMENT_JSON")
    if override:
        return Path(override)
    return _BACKEND_DIR / DEPLOYMENT_FILENAME


def load_deployment_metadata() -> dict[str, Any]:
    data = _read_json(deployment_metadata_path())
    if not is_testing_env():
        return {"environment": "production", "app_env": app_env() or "production"}
    if not data:
        return {"environment": TESTING_APP_ENV, "app_env": TESTING_APP_ENV}
    return data


def snapshot_metadata_path() -> Path:
    override = _env("NMTS_SNAPSHOT_META_JSON")
    if override:
        return Path(override)
    return _BACKEND_DIR / SNAPSHOT_META_FILENAME


def load_snapshot_metadata() -> dict[str, Any]:
    return _read_json(snapshot_metadata_path())


def snapshot_business_date_key() -> Optional[str]:
    if not is_testing_env():
        return None
    explicit = _env("TESTING_SNAPSHOT_DATE_KEY")
    if explicit:
        return "".join(ch for ch in explicit if ch.isdigit())[:8] or None
    meta = load_snapshot_metadata()
    if meta.get("status") not in {None, "", "active"}:
        raw = meta.get("previous_business_date_key") or meta.get("business_date_key")
    else:
        raw = meta.get("business_date_key")
    if not raw:
        return None
    digits = "".join(ch for ch in str(raw) if ch.isdigit())
    return digits[:8] or None


def active_snapshot_version() -> Optional[str]:
    if not is_testing_env():
        return None
    meta = load_snapshot_metadata()
    version = str(meta.get("snapshot_version") or "").strip()
    return version or None


def prefix_business_id(value: str) -> str:
    """Apply TS- only in the testing environment. Production IDs are unchanged."""
    text = str(value or "")
    if not is_testing_env() or not text:
        return text
    if text.startswith(TS_PREFIX):
        return text
    return f"{TS_PREFIX}{text}"


def testing_cancel_upload_no(old_no: str) -> str:
    """Convert PU/OU (including TS- prefixed) upload numbers to CN… / TS-CN…"""
    text = str(old_no or "")
    has_ts = text.startswith(TS_PREFIX)
    core = text[len(TS_PREFIX):] if has_ts else text
    if len(core) > 2:
        core = "CN" + core[2:]
    cancel_no = core or text
    if is_testing_env() or has_ts:
        return prefix_business_id(cancel_no)
    return cancel_no


def stamp_testing_origin(doc: Mapping[str, Any] | dict[str, Any]) -> dict[str, Any]:
    """Mark newly written testing records. No-op in production."""
    payload = dict(doc)
    if not is_testing_env():
        return payload
    payload.setdefault("data_origin", DATA_ORIGIN_TESTING)
    payload.setdefault("is_snapshot_reference", False)
    return payload


def origin_query(origin: Optional[str] = None) -> dict[str, Any]:
    """Product Hub / master-data origin clause. Empty in production."""
    if not is_testing_env():
        return {}
    version = active_snapshot_version()
    requested = str(origin or "all").strip().lower()
    snapshot_clause: dict[str, Any] = {"data_origin": DATA_ORIGIN_SNAPSHOT}
    if version:
        snapshot_clause["snapshot_version"] = version
    if requested in {"snapshot", "snapshot_reference", "reference"}:
        return snapshot_clause
    if requested in {"testing", "testing_created", "created"}:
        return {"data_origin": DATA_ORIGIN_TESTING}
    return {
        "$or": [
            {"data_origin": DATA_ORIGIN_TESTING},
            snapshot_clause,
        ]
    }


def merge_query(base: dict[str, Any], extra: dict[str, Any]) -> dict[str, Any]:
    if not extra:
        return base
    if not base:
        base.update(extra)
        return base
    if "$and" in base:
        base["$and"].append(extra)
        return base
    existing = {k: v for k, v in list(base.items())}
    base.clear()
    base["$and"] = [existing, extra]
    return base


def public_runtime_status() -> dict[str, Any]:
    """Non-secret testing status for the banner and origin filters."""
    if not is_testing_env():
        return {
            "is_testing": False,
            "app_env": app_env() or "production",
            "data_origin_filters": False,
        }
    meta = load_snapshot_metadata()
    deploy = load_deployment_metadata()
    return {
        "is_testing": True,
        "app_env": TESTING_APP_ENV,
        "data_origin_filters": True,
        "snapshot_business_date": snapshot_business_date_key(),
        "snapshot_version": active_snapshot_version(),
        "snapshot_copied_at": meta.get("copied_at"),
        "snapshot_source": meta.get("source_origin") or meta.get("source"),
        "deployment": {
            "pr_number": deploy.get("pr_number"),
            "git_branch": deploy.get("git_branch"),
            "commit": deploy.get("commit"),
            "commit_short": deploy.get("commit_short"),
            "deployed_at": deploy.get("deployed_at"),
            "deployed_at_ist": deploy.get("deployed_at_ist"),
        },
    }
