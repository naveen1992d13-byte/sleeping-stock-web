"""Backend-resolved feature flags and Testing-route guard (fail closed).

SEQUENTIAL_AUTO_SOURCING_ENABLED:
  explicit env var wins (only true/1/yes = on; anything else = off);
  unset => off in every environment. This keeps unfinished UI fail-closed.

/api/testing/*: allowed only when is_testing_env() is True, otherwise 404,
regardless of DB_NAME / NMTS_STORAGE_ENV. GET /api/testing/runtime stays
reachable everywhere: Testing -> Production verification reads it and expects
{"is_testing": false} from Production.
"""
from __future__ import annotations

import os

from fastapi import HTTPException

try:
    from . import testing_runtime
except ImportError:
    import testing_runtime

ENV_SEQUENTIAL_AUTO_SOURCING = "SEQUENTIAL_AUTO_SOURCING_ENABLED"
_ON_VALUES = {"true", "1", "yes"}

TESTING_ROUTE_PREFIX = "/api/testing/"
TESTING_RUNTIME_PATH = "/api/testing/runtime"


def sequential_auto_sourcing_enabled() -> bool:
    raw = os.environ.get(ENV_SEQUENTIAL_AUTO_SOURCING)
    if raw is not None and str(raw).strip() != "":
        return str(raw).strip().lower() in _ON_VALUES
    return False


def require_sequential_auto_sourcing() -> None:
    if not sequential_auto_sourcing_enabled():
        raise HTTPException(status_code=403, detail="disabled")


def public_features() -> dict:
    return {"sequential_auto_sourcing": sequential_auto_sourcing_enabled()}


def testing_route_blocked(path: str, method: str = "GET") -> bool:
    """True when a /api/testing/* request must be refused (404) in this process."""
    p = str(path or "").rstrip("/")
    if not (p + "/").startswith(TESTING_ROUTE_PREFIX):
        return False
    if p == TESTING_RUNTIME_PATH and str(method or "GET").upper() in {"GET", "HEAD"}:
        return False
    return not testing_runtime.is_testing_env()
