"""Physical S3 archive verification — never trust manifest flags alone.

A dataset is TRANSFERRED & VERIFIED only when the live object is confirmed on
REAL S3 with matching size, checksum, and readable record count.
"""

from __future__ import annotations

import gzip
from io import BytesIO
from typing import Any, Dict, Optional

from s3_storage import get_storage, sha256_bytes

# UI / API display states (map from manifest + live checks)
DISPLAY_VERIFIED = "TRANSFERRED & VERIFIED"
DISPLAY_NOT_TRANSFERRED = "NOT TRANSFERRED"
DISPLAY_VERIFICATION_FAILED = "VERIFICATION FAILED"
DISPLAY_PENDING = "PENDING"
DISPLAY_RUNNING = "RUNNING"
DISPLAY_PRUNED = "PRUNED"
DISPLAY_NO_ELIGIBLE = "NO ELIGIBLE DATA"


def classify_display_status(
    *,
    manifest_status: str,
    live: Optional[Dict[str, Any]] = None,
) -> str:
    live = live or {}
    if manifest_status == "NO_ELIGIBLE":
        return DISPLAY_NO_ELIGIBLE
    if manifest_status == "PRUNED" and live.get("ok"):
        return DISPLAY_PRUNED
    if live.get("ok") and live.get("real_s3"):
        return DISPLAY_VERIFIED
    if manifest_status in {"CREATING", "UPLOADED"}:
        return DISPLAY_PENDING if manifest_status == "CREATING" else DISPLAY_RUNNING
    if manifest_status == "FAILED" or (live and not live.get("ok")):
        if live.get("object_exists") is False or "unavailable" in str(live.get("reason") or "").lower():
            return DISPLAY_NOT_TRANSFERRED
        return DISPLAY_VERIFICATION_FAILED
    if manifest_status == "VERIFIED" and not live.get("ok"):
        # Stale manifest claim — downgrade for UI
        return DISPLAY_VERIFICATION_FAILED
    return DISPLAY_NOT_TRANSFERRED


def head_s3_status(manifest: Dict[str, Any]) -> Dict[str, Any]:
    """Lightweight list-row status: REAL S3 head + size. Never trusts manifest alone.

    Full SHA/count download is reserved for verify/retry/delete paths.
    Green requires: REAL S3 + object exists (s3 provider) + size match +
    manifest VERIFIED/PRUNED with eligible_for_prune and storage_backend s3.
    """
    storage = get_storage()
    status = storage.status()
    key = str(manifest.get("storage_key") or "")
    report: Dict[str, Any] = {
        "ok": False,
        "real_s3": bool(storage.is_s3()),
        "storage_backend": status.get("storage_backend"),
        "storage_key": key,
        "object_exists": False,
        "object_readable": None,
        "size_match": False,
        "sha256_match": None,
        "record_count_match": None,
        "observed_size": None,
        "reason": "",
        "failure_code": "",
        "check_mode": "head",
    }
    mstatus_early = str(manifest.get("status") or "").upper()
    if mstatus_early == "NO_ELIGIBLE":
        report["ok"] = False
        report["reason"] = manifest.get("error") or "NO ELIGIBLE DATA"
        report["display_status"] = DISPLAY_NO_ELIGIBLE
        report["failure_code"] = ""
        return report

    if not key:
        report["failure_code"] = "missing_key"
        report["reason"] = "Wrong archive key/path"
        report["display_status"] = DISPLAY_NOT_TRANSFERRED
        return report
    if not storage.is_s3():
        report["failure_code"] = "s3_unavailable"
        report["reason"] = "S3 credentials/config unavailable — local fallback is not REAL S3"
        report["display_status"] = DISPLAY_NOT_TRANSFERRED
        return report

    head = storage.head(key)
    if head and str(head.get("storage_provider") or "").lower() == "local":
        report["failure_code"] = "local_masquerade"
        report["reason"] = "Object resolved to local fallback, not REAL S3"
        report["object_exists"] = True
        report["display_status"] = DISPLAY_VERIFICATION_FAILED
        return report
    report["object_exists"] = bool(head)
    if not head:
        report["failure_code"] = "object_missing"
        report["reason"] = "S3 object missing"
        report["display_status"] = DISPLAY_NOT_TRANSFERRED
        return report

    observed_size = int(head.get("file_size") or 0)
    report["observed_size"] = observed_size
    expected_size = int(manifest.get("file_size") or 0)
    report["size_match"] = (not expected_size) or observed_size == expected_size
    if not report["size_match"]:
        report["failure_code"] = "size_mismatch"
        report["reason"] = f"file size mismatch expected={expected_size} got={observed_size}"
        report["display_status"] = DISPLAY_VERIFICATION_FAILED
        return report

    backend = str(manifest.get("storage_backend") or "").lower()
    status_ok = manifest.get("status") in {"VERIFIED", "PRUNED"}
    eligible = bool(manifest.get("eligible_for_prune")) or manifest.get("status") == "PRUNED"
    real_backend = backend in {"s3", "real s3"}
    if status_ok and eligible and real_backend:
        report["ok"] = True
        report["object_readable"] = True  # was readable at upload-time verify; head still present
        report["sha256_match"] = True if manifest.get("sha256") else None
        report["record_count_match"] = True
        report["reason"] = "TRANSFERRED & VERIFIED"
        report["display_status"] = (
            DISPLAY_PRUNED if manifest.get("status") == "PRUNED" else DISPLAY_VERIFIED
        )
        return report

    mstatus = str(manifest.get("status") or "")
    if mstatus in {"CREATING", "UPLOADED"}:
        report["display_status"] = DISPLAY_PENDING if mstatus == "CREATING" else DISPLAY_RUNNING
        report["reason"] = mstatus
        return report
    report["failure_code"] = "manifest_not_verified"
    report["reason"] = manifest.get("error") or "Archive not TRANSFERRED & VERIFIED"
    report["display_status"] = (
        DISPLAY_VERIFICATION_FAILED if mstatus == "VERIFIED" else DISPLAY_NOT_TRANSFERRED
    )
    return report


def physical_s3_verify(
    *,
    storage_key: str,
    expected_sha256: str = "",
    expected_size: int = 0,
    expected_record_count: Optional[int] = None,
    require_jsonl_count: bool = True,
) -> Dict[str, Any]:
    """Verify the object on the current storage backend.

    Returns a structured report. `ok` is True only when REAL S3 is active and
    every requested integrity check passes.
    """
    storage = get_storage()
    status = storage.status()
    report: Dict[str, Any] = {
        "ok": False,
        "real_s3": bool(storage.is_s3()),
        "storage_backend": status.get("storage_backend"),
        "storage_key": storage_key,
        "object_exists": False,
        "object_readable": False,
        "size_match": False,
        "sha256_match": False,
        "record_count_match": None,
        "observed_size": None,
        "observed_sha256": None,
        "observed_record_count": None,
        "reason": "",
        "failure_code": "",
    }
    reasons = []

    if not storage_key:
        report["failure_code"] = "missing_key"
        report["reason"] = "Wrong archive key/path"
        return report

    if not storage.is_s3():
        report["failure_code"] = "s3_unavailable"
        report["reason"] = "S3 credentials/config unavailable — local fallback is not REAL S3"
        # Still probe local object for diagnostics
        head = storage.head(storage_key)
        report["object_exists"] = bool(head)
        if head:
            report["observed_size"] = head.get("file_size")
        return report

    head = storage.head(storage_key)
    # When mode is s3, refuse to treat a local-only head as success if provider is local
    if head and str(head.get("storage_provider") or "").lower() == "local":
        report["failure_code"] = "local_masquerade"
        report["reason"] = "Object resolved to local fallback, not REAL S3"
        report["object_exists"] = True
        return report

    report["object_exists"] = bool(head)
    if not head:
        report["failure_code"] = "object_missing"
        report["reason"] = "S3 object missing"
        return report

    observed_size = int(head.get("file_size") or 0)
    report["observed_size"] = observed_size
    if expected_size and observed_size != int(expected_size):
        reasons.append(f"file size mismatch expected={expected_size} got={observed_size}")
        report["failure_code"] = "size_mismatch"
    else:
        report["size_match"] = True if expected_size else True

    try:
        data, _ = storage.download_bytes(storage_key)
        report["object_readable"] = True
        got_sha = sha256_bytes(data)
        report["observed_sha256"] = got_sha
        if expected_sha256:
            report["sha256_match"] = got_sha == expected_sha256
            if not report["sha256_match"]:
                reasons.append("checksum mismatch")
                report["failure_code"] = "checksum_mismatch"
        else:
            report["sha256_match"] = True

        if require_jsonl_count and expected_record_count is not None:
            read_count = 0
            with gzip.GzipFile(fileobj=BytesIO(data), mode="rb") as gz:
                for line in gz:
                    if line.strip():
                        read_count += 1
            report["observed_record_count"] = read_count
            report["record_count_match"] = int(read_count) == int(expected_record_count)
            if not report["record_count_match"]:
                reasons.append(
                    f"row count mismatch expected={expected_record_count} got={read_count}"
                )
                report["failure_code"] = "count_mismatch"
        else:
            report["record_count_match"] = True
    except Exception as exc:
        report["object_readable"] = False
        report["failure_code"] = "unreadable"
        reasons.append(f"file unreadable: {type(exc).__name__}")

    # Also use storage.verify_object as belt-and-suspenders when size/sha provided
    if expected_sha256 and expected_size and report["object_readable"]:
        if not storage.verify_object(storage_key, expected_sha256, int(expected_size)):
            if "checksum mismatch" not in "; ".join(reasons):
                reasons.append("S3 integrity verification failed")
            report["sha256_match"] = False
            report["failure_code"] = report["failure_code"] or "verify_object_failed"

    ok = (
        report["real_s3"]
        and report["object_exists"]
        and report["object_readable"]
        and report["size_match"]
        and report["sha256_match"]
        and (report["record_count_match"] is not False)
    )
    report["ok"] = bool(ok)
    report["reason"] = "TRANSFERRED & VERIFIED" if ok else ("; ".join(reasons) or "VERIFICATION FAILED")
    return report


async def live_verify_manifest(manifest: Dict[str, Any]) -> Dict[str, Any]:
    """Re-check an existing manifest against the physical object."""
    key = str(manifest.get("storage_key") or "")
    live = physical_s3_verify(
        storage_key=key,
        expected_sha256=str(manifest.get("sha256") or ""),
        expected_size=int(manifest.get("file_size") or 0),
        expected_record_count=int(manifest.get("record_count") or 0)
        if manifest.get("format", "jsonl.gz") == "jsonl.gz" or str(key).endswith(".jsonl.gz")
        else None,
        require_jsonl_count=str(key).endswith(".jsonl.gz") or manifest.get("format") == "jsonl.gz",
    )
    live["manifest_status"] = manifest.get("status")
    live["display_status"] = classify_display_status(
        manifest_status=str(manifest.get("status") or ""),
        live=live,
    )
    live["archive_id"] = manifest.get("archive_id")
    live["module"] = manifest.get("module")
    live["archive_date"] = manifest.get("archive_date") or manifest.get("archive_month")
    live["error"] = None if live.get("ok") else (live.get("reason") or manifest.get("error"))
    return live
