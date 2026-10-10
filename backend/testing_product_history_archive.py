"""Testing-only Product Hub History archive selection.

Never wraps Production overlay. Never archives mirrored Production rows.
Operator entry: scripts/testing_oct10_product_history_archive.py
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Iterable, Mapping, Optional
from zoneinfo import ZoneInfo

TESTING_ORIGIN = "testing"
TS_PREFIX = "TS-"
ARCHIVE_DAY = "20261010"
IST = ZoneInfo("Asia/Kolkata")
EXCLUDED_UPLOAD_NOS = frozenset({"PUHY261010001"})
EXCLUDED_BRANCHES = frozenset({"chrompet", "chromepet"})
EXCLUDED_DEALER_MARKERS = ("kun auto",)
_UPLOAD_TS_FIELDS = ("published_at", "uploaded_at", "created_at")


def upload_center_ist_date(doc: Mapping[str, Any]) -> Optional[str]:
    """Upload Center created/published calendar day in IST. Ignores frozen date_key."""
    for field in _UPLOAD_TS_FIELDS:
        raw = doc.get(field)
        if not raw:
            continue
        text = str(raw).strip()
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        try:
            value = datetime.fromisoformat(text)
        except ValueError:
            continue
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(IST).strftime("%Y%m%d")
    return None


def is_testing_owned(doc: Mapping[str, Any]) -> bool:
    origin = str(doc.get("data_origin") or "").strip().lower()
    env = str(doc.get("environment") or "").strip().lower()
    uno = str(doc.get("upload_no") or "")
    return origin == TESTING_ORIGIN and uno.startswith(TS_PREFIX) and env in {"", TESTING_ORIGIN}


def is_oct10_testing_upload(doc: Mapping[str, Any]) -> bool:
    if not is_testing_owned(doc):
        return False
    if str(doc.get("publish_status") or "") != "Published":
        return False
    return upload_center_ist_date(doc) == ARCHIVE_DAY


def production_leak_reason(doc: Mapping[str, Any]) -> str | None:
    """Return a reason if this row must abort the archive. None if Testing-owned."""
    uno = str(doc.get("upload_no") or "")
    branch = str(doc.get("branch") or doc.get("branch_name") or "").strip()
    dealer = str(doc.get("dealer_name") or doc.get("dealer") or "").lower()
    origin = str(doc.get("data_origin") or "").strip().lower()
    if uno in EXCLUDED_UPLOAD_NOS:
        return f"excluded Production upload_no {uno}"
    if branch.lower() in EXCLUDED_BRANCHES:
        return f"excluded mirrored Production branch {branch}"
    if any(marker in dealer for marker in EXCLUDED_DEALER_MARKERS) and not uno.startswith(TS_PREFIX):
        return f"excluded Production dealer {dealer}"
    if origin and origin != TESTING_ORIGIN:
        return f"data_origin={origin!r} is not testing"
    if not uno.startswith(TS_PREFIX):
        return f"upload_no {uno!r} is not TS-"
    if origin != TESTING_ORIGIN:
        return "missing data_origin=testing"
    return None


def assert_no_production_rows(rows: Iterable[Mapping[str, Any]]) -> None:
    for row in rows:
        reason = production_leak_reason(row)
        if reason:
            raise RuntimeError(f"STOP: archive query included a Production/mirrored record: {reason}")
