"""Testing-only Product Hub History archive selection.

Never wraps Production overlay. Never archives mirrored Production rows.
Operator entry: scripts/testing_oct10_product_history_archive.py
"""
from __future__ import annotations

from typing import Any, Iterable, Mapping

TESTING_ORIGIN = "testing"
TS_PREFIX = "TS-"
OCT10_DATE_KEYS = frozenset({"20261010", "2026-10-10", "261010"})
OCT10_CREATED_PREFIXES = ("2026-10-10",)
EXCLUDED_UPLOAD_NOS = frozenset({"PUHY261010001"})
EXCLUDED_BRANCHES = frozenset({"chrompet", "chromepet"})
EXCLUDED_DEALER_MARKERS = ("kun auto",)


def is_testing_owned(doc: Mapping[str, Any]) -> bool:
    origin = str(doc.get("data_origin") or "").strip().lower()
    env = str(doc.get("environment") or "").strip().lower()
    uno = str(doc.get("upload_no") or "")
    return origin == TESTING_ORIGIN and uno.startswith(TS_PREFIX) and env in {"", TESTING_ORIGIN}


def is_oct10_testing_upload(doc: Mapping[str, Any]) -> bool:
    if not is_testing_owned(doc):
        return False
    if str(doc.get("publish_status") or "") == "Cancelled":
        return False
    uno = str(doc.get("upload_no") or "")
    date_key = str(doc.get("date_key") or "")
    created = str(doc.get("created_at") or "")
    if date_key in OCT10_DATE_KEYS:
        return True
    if "261010" in uno:
        return True
    return created.startswith(OCT10_CREATED_PREFIXES)


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
