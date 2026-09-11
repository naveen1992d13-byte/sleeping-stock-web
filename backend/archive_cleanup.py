"""Additive safe Mongo cleanup helpers for VERIFIED product-history archives.

Does NOT delete S3 objects. Does NOT enable ARCHIVE_PRUNE_ENABLED.
Manual Master Admin flow only: View/Verify → Dry Run → typed DELETE confirm.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import logging
import os
import uuid
from datetime import datetime, timedelta, timezone
from io import BytesIO
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

import archive_manifest as am
import history_archive as ha
from s3_storage import get_storage, sha256_bytes

logger = logging.getLogger(__name__)
IST = ZoneInfo("Asia/Kolkata")
COLLECTION = "products"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _text(v: Any) -> str:
    return str(v or "").strip()


def external_console_links() -> Dict[str, Any]:
    """Safe external console URLs from env — never credentials."""
    bucket = (os.getenv("NMTS_S3_BUCKET") or os.getenv("AWS_S3_BUCKET") or "").strip()
    region = (os.getenv("AWS_REGION") or "").strip()
    aws_url = (os.getenv("NMTS_AWS_CONSOLE_URL") or "").strip()
    if not aws_url and bucket:
        aws_url = f"https://s3.console.aws.amazon.com/s3/buckets/{bucket}?tab=objects"
        if region:
            aws_url = f"https://s3.console.aws.amazon.com/s3/buckets/{bucket}?region={region}&tab=objects"
    region_out = region or "unknown"
    mongo_url = (os.getenv("NMTS_MONGODB_ATLAS_URL") or os.getenv("MONGODB_ATLAS_CONSOLE_URL") or "").strip()
    # Generic Atlas portal is safe (no secrets). Project-specific URL preferred via env.
    if not mongo_url:
        mongo_url = "https://cloud.mongodb.com/"
    return {
        "aws": {
            "label": "AWS / S3",
            "open_url": aws_url or None,
            "open_label": "Open AWS / S3",
            "status_note": "Uses browser AWS session — NMTS does not store AWS passwords.",
            "billing_available": False,
            "billing_message": "Billing data unavailable",
            "bucket": bucket or None,
            "region": region_out,
        },
        "mongodb": {
            "label": "MongoDB",
            "open_url": mongo_url or None,
            "open_label": "Open MongoDB",
            "status_note": "Uses browser Atlas session — NMTS does not store MongoDB passwords.",
            "billing_available": False,
            "billing_message": "Billing data unavailable",
        },
        "pattern": "identical_cards",
    }


def _clean_names(values: Any) -> List[str]:
    out = []
    for v in values or []:
        s = _text(v)
        if s:
            out.append(s)
    # Preserve order, unique
    seen = set()
    uniq = []
    for s in out:
        if s not in seen:
            seen.add(s)
            uniq.append(s)
    return uniq


async def _scope_from_mongo(db, date_iso: str) -> Dict[str, List[str]]:
    """Display-only scope for legacy manifests missing scope_* fields. Does not mutate manifests."""
    date_key = ha.iso_to_date_key(date_iso or "")
    match = {
        "publish_status": "Published",
        "active_date_key": {"$in": [date_key, date_iso]},
    }
    brands = _clean_names(await db.products.distinct("brand_name", match))
    if not brands:
        brands = _clean_names(await db.products.distinct("brand", match))
    dealers = _clean_names(await db.products.distinct("dealer_name", match))
    branches = _clean_names(await db.products.distinct("branch", match))
    brand_codes = _clean_names(await db.products.distinct("brand_code", match))
    dealer_codes = _clean_names(await db.products.distinct("dealer_code", match))
    return {
        "brands": brands,
        "dealers": dealers,
        "branches": branches,
        "brand_codes": brand_codes,
        "dealer_codes": dealer_codes,
    }


async def list_cleanup_archives(
    db,
    *,
    years: int = 3,
    brand: Optional[str] = None,
    dealer: Optional[str] = None,
    branch: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Archive transfer/cleanup rows (all datasets, up to N years).

    Brand/dealer/branch filters are ignored for Storage page global health —
    retained as no-ops so callers stay compatible. Always returns overall rows.
    """
    import archive_verify as av

    _ = (brand, dealer, branch)  # intentionally unused — Storage page is global
    cutoff = (datetime.now(IST).date() - timedelta(days=max(1, int(years)) * 366)).isoformat()
    q: Dict[str, Any] = {
        "$or": [
            {"archive_date": {"$gte": cutoff}},
            {"archive_month": {"$gte": cutoff[:7]}},
        ],
    }
    prune_on = bool(get_storage().status().get("archive_prune_enabled"))
    rows = await db.archive_manifests.find(q, {"_id": 0}).sort("created_at", -1).to_list(5000)
    out = []
    for m in rows:
        date_iso = m.get("archive_date") or m.get("archive_month")
        date_key = ha.iso_to_date_key(date_iso or "") if m.get("archive_date") else ""
        scope_brands = _clean_names(m.get("scope_brands") or [])
        scope_dealers = _clean_names(m.get("scope_dealers") or [])
        scope_branches = _clean_names(m.get("scope_branches") or [])
        brand_codes = _clean_names(m.get("scope_brand_codes") or [])
        dealer_codes = _clean_names(m.get("scope_dealer_codes") or [])
        if (
            m.get("module") == ha.MODULE_PRODUCT_HISTORY
            and not scope_brands
            and not scope_dealers
            and not scope_branches
        ):
            derived = await _scope_from_mongo(db, date_iso or "")
            scope_brands = derived["brands"] or scope_brands
            scope_dealers = derived["dealers"] or scope_dealers
            scope_branches = derived["branches"] or scope_branches
            brand_codes = brand_codes or derived["brand_codes"]
            dealer_codes = dealer_codes or derived["dealer_codes"]

        mongo_count = 0
        if m.get("module") == ha.MODULE_PRODUCT_HISTORY and date_key:
            mongo_count = await db.products.count_documents(
                {
                    "publish_status": "Published",
                    "active_date_key": {"$in": [date_key, date_iso]},
                }
            )
        s3_count = int(m.get("record_count") or 0)
        live = av.head_s3_status(m)
        display = live.get("display_status") or av.classify_display_status(
            manifest_status=str(m.get("status") or ""),
            live=live,
        )
        physically_ok = bool(live.get("ok") and live.get("real_s3"))
        counts_ok = mongo_count == s3_count if m.get("module") == ha.MODULE_PRODUCT_HISTORY else True
        delete_locked = (not prune_on) or not (
            physically_ok
            and m.get("module") == ha.MODULE_PRODUCT_HISTORY
            and m.get("status") == am.STATUS_VERIFIED
            and m.get("eligible_for_prune")
            and mongo_count > 0
            and counts_ok
        )
        if not prune_on:
            lock_reason = "Locked — Mongo cleanup disabled (ARCHIVE_PRUNE_ENABLED=false)"
        elif physically_ok and m.get("status") == am.STATUS_VERIFIED and m.get("eligible_for_prune"):
            lock_reason = None if m.get("module") == ha.MODULE_PRODUCT_HISTORY and mongo_count > 0 and counts_ok else (
                "Locked — only Product History archives support Mongo delete"
                if m.get("module") != ha.MODULE_PRODUCT_HISTORY
                else ("Locked — row count mismatch" if not counts_ok else "Locked — no Mongo rows to delete")
            )
        else:
            code = live.get("failure_code") or ""
            if code == "object_missing":
                lock_reason = "Locked — S3 object missing"
            elif code == "checksum_mismatch":
                lock_reason = "Locked — checksum mismatch"
            elif code == "count_mismatch":
                lock_reason = "Locked — row count mismatch"
            elif not live.get("real_s3"):
                lock_reason = "Locked — S3 credentials/config unavailable"
            else:
                lock_reason = f"Locked — {live.get('reason') or m.get('error') or 'not verified'}"

        has_fp = bool(m.get("source_fingerprint"))
        if m.get("status") == am.STATUS_PRUNED:
            data_changed = "ARCHIVED / PRUNED"
        elif m.get("module") != ha.MODULE_PRODUCT_HISTORY:
            data_changed = "N/A"
        elif mongo_count == 0:
            data_changed = "MONGO ABSENT"
        elif not has_fp and mongo_count != s3_count:
            data_changed = "SOURCE CHANGED AFTER ARCHIVE"
        elif not has_fp:
            data_changed = "LEGACY (verify required)"
        elif mongo_count != s3_count:
            data_changed = "SOURCE CHANGED AFTER ARCHIVE"
        else:
            data_changed = "MATCHES ARCHIVE (count)"

        brand_label = ", ".join(scope_brands[:5]) if scope_brands else (", ".join(brand_codes[:5]) if brand_codes else "")
        dealer_label = ", ".join(scope_dealers[:5]) if scope_dealers else (", ".join(dealer_codes[:5]) if dealer_codes else "")
        branch_label = ", ".join(scope_branches[:5]) if scope_branches else ""

        out.append(
            {
                "archive_id": m.get("archive_id"),
                "dataset": m.get("module"),
                "module": m.get("module"),
                "archive_date": date_iso,
                "brand": brand_label or None,
                "dealer": dealer_label or None,
                "branch": branch_label or None,
                "brands": scope_brands,
                "dealers": scope_dealers,
                "branches": scope_branches,
                "brand_codes": brand_codes,
                "dealer_codes": dealer_codes,
                "mongo_source_count": mongo_count,
                "source_record_count": mongo_count if m.get("module") == ha.MODULE_PRODUCT_HISTORY else s3_count,
                "s3_record_count": s3_count,
                "archived_record_count": s3_count,
                "file_size": m.get("file_size"),
                "archive_status": m.get("status"),
                "display_status": display,
                "failure_reason": None if physically_ok else (live.get("reason") or m.get("error")),
                "s3_object_status": "EXISTS" if live.get("object_exists") else "MISSING",
                "s3_readable": bool(live.get("object_readable")),
                "sha256_match": (
                    "MATCH"
                    if live.get("sha256_match") is True
                    else ("MISMATCH" if live.get("sha256_match") is False else "Not Checked")
                ),
                "data_changed_status": data_changed,
                "sha256_status": "PRESENT" if m.get("sha256") else "MISSING",
                "storage_backend": m.get("storage_backend"),
                "sha256": m.get("sha256"),
                "storage_key": m.get("storage_key"),
                "eligible_for_prune": m.get("eligible_for_prune"),
                "delete_locked": delete_locked,
                "lock_reason": lock_reason if delete_locked else None,
                "mongo_data_status": (
                    "PRUNED"
                    if m.get("status") == am.STATUS_PRUNED
                    else ("PRESENT" if mongo_count > 0 else "ABSENT")
                ),
                "cleanup_status": (
                    "PRUNED"
                    if m.get("status") == am.STATUS_PRUNED
                    else (
                        "DISABLED"
                        if not prune_on
                        else ("MONGO PRESENT" if mongo_count > 0 else "MONGO ABSENT")
                    )
                ),
                "archive_prune_enabled": prune_on,
                "transferred_at": m.get("verified_at") or m.get("created_at"),
                "verified_at": m.get("verified_at"),
                "created_at": m.get("created_at"),
                "source_fingerprint_present": has_fp,
                "retryable": display
                in {
                    av.DISPLAY_NOT_TRANSFERRED,
                    av.DISPLAY_VERIFICATION_FAILED,
                    av.DISPLAY_PENDING,
                    av.DISPLAY_RUNNING,
                }
                or (
                    m.get("status") in {am.STATUS_FAILED, am.STATUS_CREATING, am.STATUS_UPLOADED}
                    and display != av.DISPLAY_NO_ELIGIBLE
                ),
            }
        )
    return out


def _product_query(date_iso: str, brand=None, dealer=None, branch=None) -> Dict[str, Any]:
    """Exact Mongo scope for a historical archive date.

    Live/today protection is enforced separately by date checks in verify_archive.
    Historical rows may be mis-flagged is_active_today=True in source data; for
    non-today archive dates we therefore match by active_date_key only (plus
    optional brand/dealer/branch filters). Never uses {}.
    """
    date_key = ha.iso_to_date_key(date_iso)
    q: Dict[str, Any] = {
        "publish_status": "Published",
        "active_date_key": {"$in": [date_key, date_iso]},
    }
    if brand:
        q["brand_name"] = brand
    if dealer:
        q["dealer_name"] = dealer
    if branch:
        q["branch"] = branch
    return q


async def _load_archive_rows(storage_key: str) -> List[Dict[str, Any]]:
    storage = get_storage()
    data, _ = storage.download_bytes(storage_key)
    rows = []
    with gzip.GzipFile(fileobj=BytesIO(data), mode="rb") as gz:
        for line in gz:
            line = line.strip()
            if not line:
                continue
            rows.append(json.loads(line.decode("utf-8")))
    return rows


async def verify_archive(
    db,
    *,
    archive_id: str,
    brand: Optional[str] = None,
    dealer: Optional[str] = None,
    branch: Optional[str] = None,
) -> Dict[str, Any]:
    """Fresh verification of a product-history archive for safe-delete eligibility.

    Manifest VERIFIED alone is never sufficient — requires live physical S3 checks.
    """
    import archive_verify as av

    manifest = await db.archive_manifests.find_one({"archive_id": archive_id}, {"_id": 0})
    if not manifest:
        return {
            "ok": False,
            "safe_to_delete": False,
            "delete_locked": True,
            "lock_reason": "Locked — archive not found",
            "reason": "Archive not found",
            "archive_id": archive_id,
            "display_status": av.DISPLAY_NOT_TRANSFERRED,
        }

    storage = get_storage()
    status = storage.status()
    date_iso = manifest.get("archive_date")
    key = manifest.get("storage_key")
    live = await av.live_verify_manifest(manifest)

    checks: Dict[str, Any] = {
        "real_s3": bool(live.get("real_s3")),
        "archive_exists": True,
        "verified_manifest": manifest.get("status") in {am.STATUS_VERIFIED, am.STATUS_PRUNED},
        "object_exists": bool(live.get("object_exists")),
        "object_readable": bool(live.get("object_readable")),
        "sha256_match": bool(live.get("sha256_match")),
        "record_count_match": live.get("record_count_match") is not False,
        "date_scope_match": bool(date_iso),
        "source_unchanged": False,
        "archive_prune_enabled": bool(status.get("archive_prune_enabled")),
        "physical_ok": bool(live.get("ok")),
        "storage_backend_real": str(manifest.get("storage_backend") or "").lower() in {"s3", "real s3"},
        "counts_match": False,
    }
    reasons = []
    lock_reason = None

    if not checks["archive_prune_enabled"]:
        reasons.append("ARCHIVE_PRUNE_ENABLED=false")
        lock_reason = lock_reason or "Locked — Mongo cleanup disabled (ARCHIVE_PRUNE_ENABLED=false)"

    if not live.get("real_s3"):
        reasons.append("REAL S3 is not active")
        lock_reason = lock_reason or "Locked — S3 credentials/config unavailable"
    if manifest.get("module") != ha.MODULE_PRODUCT_HISTORY:
        reasons.append("Only product-history archives are supported for Mongo delete")
        checks["verified_manifest"] = False
        lock_reason = lock_reason or "Locked — only Product History archives support Mongo delete"
    if manifest.get("status") == am.STATUS_PRUNED:
        reasons.append("Archive already marked PRUNED")
        lock_reason = lock_reason or "Locked — already pruned"
    if not checks["verified_manifest"] or not live.get("ok"):
        if not live.get("ok"):
            reasons.append(str(live.get("reason") or "Physical S3 verification failed"))
            code = live.get("failure_code") or ""
            if code == "object_missing":
                lock_reason = lock_reason or "Locked — S3 object missing"
            elif code == "checksum_mismatch":
                lock_reason = lock_reason or "Locked — checksum mismatch"
            elif code == "count_mismatch":
                lock_reason = lock_reason or "Locked — row count mismatch"
            elif code == "unreadable":
                lock_reason = lock_reason or "Locked — file unreadable"
            elif code in {"s3_unavailable", "local_masquerade"}:
                lock_reason = lock_reason or "Locked — S3 credentials/config unavailable"
            else:
                lock_reason = lock_reason or f"Locked — {live.get('reason') or 'verification failed'}"
        else:
            reasons.append(f"Manifest status is {manifest.get('status')}, need VERIFIED")
            lock_reason = lock_reason or "Locked — archive not TRANSFERRED & VERIFIED"

    today = datetime.now(IST).date().isoformat()
    if date_iso == today:
        reasons.append("Cannot delete today's live Product dataset")
        checks["date_scope_match"] = False
        lock_reason = lock_reason or "Locked — cannot delete today's live Product data"

    expected_sha = manifest.get("sha256") or ""
    expected_count = int(manifest.get("record_count") or 0)
    archive_rows: List[Dict[str, Any]] = []
    try:
        if key and live.get("object_readable") and live.get("real_s3"):
            archive_rows = await _load_archive_rows(key)
    except Exception as exc:
        checks["object_readable"] = False
        reasons.append(f"S3 read failed: {type(exc).__name__}")
        lock_reason = lock_reason or "Locked — file unreadable"

    q = _product_query(date_iso, brand, dealer, branch)
    mongo_rows = await db.products.find(q, {"_id": 0}).to_list(500000)
    mongo_rows = await ha._enrich_product_scope_rows(db, mongo_rows)
    if archive_rows:
        archive_rows = await ha._enrich_product_scope_rows(db, archive_rows)
    mongo_count = len(mongo_rows)
    mongo_fp = ha._source_fingerprint(mongo_rows)
    archived_fp = manifest.get("source_fingerprint") or (
        ha._source_fingerprint(archive_rows) if archive_rows else ""
    )
    source_changed = bool(archived_fp and mongo_fp and archived_fp != mongo_fp)
    if not archived_fp and mongo_count != expected_count:
        source_changed = True
    checks["source_unchanged"] = (mongo_count == 0) or (not source_changed and mongo_count == expected_count)
    checks["counts_match"] = mongo_count == expected_count and checks["record_count_match"] is not False
    if mongo_count != expected_count:
        reasons.append(f"Mongo source count {mongo_count} != S3 archived count {expected_count}")
        lock_reason = lock_reason or "Locked — row count mismatch"

    source_status = (
        "NO_MONGO_ROWS"
        if mongo_count == 0
        else ("SOURCE CHANGED AFTER ARCHIVE" if source_changed else "MATCHES ARCHIVE")
    )
    if source_changed and mongo_count > 0:
        reasons.append("SOURCE CHANGED AFTER ARCHIVE")
        lock_reason = lock_reason or "Locked — source changed after archive"

    safe = (
        checks["real_s3"]
        and checks["physical_ok"]
        and checks["verified_manifest"]
        and checks["object_exists"]
        and checks["object_readable"]
        and checks["sha256_match"]
        and checks["record_count_match"]
        and checks["counts_match"]
        and checks["date_scope_match"]
        and checks["source_unchanged"]
        and checks["storage_backend_real"]
        and checks["archive_prune_enabled"]
        and mongo_count > 0
        and manifest.get("status") == am.STATUS_VERIFIED
        and bool(manifest.get("eligible_for_prune"))
    )
    if mongo_count <= 0:
        reasons.append("No matching Mongo source rows to delete")
        safe = False
        lock_reason = lock_reason or "Locked — no Mongo rows to delete"

    if not safe and not lock_reason:
        lock_reason = "Locked — archive not fully verified"

    scope_brands = _clean_names(manifest.get("scope_brands") or [])
    scope_dealers = _clean_names(manifest.get("scope_dealers") or [])
    scope_branches = _clean_names(manifest.get("scope_branches") or [])
    brand_codes = _clean_names(manifest.get("scope_brand_codes") or [])
    dealer_codes = _clean_names(manifest.get("scope_dealer_codes") or [])
    if not scope_brands and not scope_dealers and not scope_branches and date_iso:
        derived = await _scope_from_mongo(db, date_iso)
        scope_brands = derived["brands"]
        scope_dealers = derived["dealers"]
        scope_branches = derived["branches"]
        brand_codes = brand_codes or derived["brand_codes"]
        dealer_codes = dealer_codes or derived["dealer_codes"]

    return {
        "ok": True,
        "archive_id": archive_id,
        "archive_date": date_iso,
        "brand": brand or (", ".join(scope_brands[:5]) or ", ".join(brand_codes[:5]) or None),
        "dealer": dealer or (", ".join(scope_dealers[:5]) or ", ".join(dealer_codes[:5]) or None),
        "branch": branch or (", ".join(scope_branches[:5]) or None),
        "collection": COLLECTION,
        "storage_key": key,
        "manifest_status": manifest.get("status"),
        "display_status": live.get("display_status"),
        "storage_backend": manifest.get("storage_backend") or status.get("storage_backend"),
        "sha256": expected_sha,
        "sha256_status": (
            "MATCH"
            if checks.get("sha256_match") is True
            else ("MISMATCH" if checks.get("sha256_match") is False else "Not Checked")
        ),
        "s3_readable": checks["object_readable"],
        "s3_object_status": "EXISTS" if checks["object_exists"] else "MISSING",
        "archive_timestamp": manifest.get("verified_at") or manifest.get("created_at"),
        "verified_at": manifest.get("verified_at"),
        "mongo_count": mongo_count,
        "s3_count": expected_count,
        "source_record_count": mongo_count,
        "archived_record_count": expected_count,
        "file_size": manifest.get("file_size"),
        "source_change_status": source_status,
        "safe_to_delete": bool(safe),
        "delete_locked": not bool(safe),
        "lock_reason": None if safe else lock_reason,
        "reason": "SAFE TO DELETE" if safe else ("; ".join(reasons) or "NOT SAFE"),
        "checks": checks,
        "live_verification": live,
        "brands": scope_brands,
        "dealers": scope_dealers,
        "branches": scope_branches,
        "real_s3": status.get("real_s3"),
        "archive_prune_enabled": status.get("archive_prune_enabled"),
    }


async def reverify_archive(db, *, archive_id: str) -> Dict[str, Any]:
    """Read-only physical Re-Verify. Never deletes Mongo or S3 objects."""
    import archive_verify as av

    manifest = await db.archive_manifests.find_one({"archive_id": archive_id}, {"_id": 0})
    if not manifest:
        return {
            "ok": False,
            "action": "Re-Verify",
            "read_only": True,
            "archive_id": archive_id,
            "reason": "Archive not found",
            "display_status": av.DISPLAY_NOT_TRANSFERRED,
            "safe_to_delete": False,
            "delete_locked": True,
        }

    live = await av.live_verify_manifest(manifest)
    status = get_storage().status()
    prune_on = bool(status.get("archive_prune_enabled"))
    expected_count = int(manifest.get("record_count") or 0)
    observed = live.get("observed_record_count")
    mongo_count = None
    date_iso = manifest.get("archive_date")
    if manifest.get("module") == ha.MODULE_PRODUCT_HISTORY and date_iso:
        mongo_count = await db.products.count_documents(_product_query(date_iso))
    s3_count = int(observed) if observed is not None else expected_count
    counts_match = live.get("record_count_match") is not False
    if mongo_count is not None:
        counts_match = counts_match and int(mongo_count) == int(expected_count)
        if observed is not None:
            counts_match = counts_match and int(mongo_count) == int(observed)

    ok = bool(live.get("ok") and live.get("real_s3") and live.get("object_exists") and live.get("object_readable") and counts_match)
    return {
        "ok": ok,
        "action": "Re-Verify",
        "read_only": True,
        "archive_id": archive_id,
        "archive_date": date_iso or manifest.get("archive_month"),
        "dataset": manifest.get("module"),
        "collection": manifest.get("source_collection"),
        "storage_key": manifest.get("storage_key"),
        "manifest_status": manifest.get("status"),
        "display_status": live.get("display_status"),
        "s3_object_status": "EXISTS" if live.get("object_exists") else "MISSING",
        "s3_readable": bool(live.get("object_readable")),
        "sha256_status": (
            "MATCH"
            if live.get("sha256_match") is True
            else ("MISMATCH" if live.get("sha256_match") is False else "Not Checked")
        ),
        "mongo_count": mongo_count,
        "s3_count": s3_count,
        "source_record_count": mongo_count if mongo_count is not None else expected_count,
        "archived_record_count": expected_count,
        "record_count_match": counts_match,
        "size_match": live.get("size_match"),
        "file_size": manifest.get("file_size"),
        "observed_size": live.get("observed_size"),
        "verified_at": manifest.get("verified_at"),
        "archive_timestamp": manifest.get("verified_at") or manifest.get("created_at"),
        "reason": live.get("reason") if not ok else "TRANSFERRED & VERIFIED",
        "live_verification": live,
        "real_s3": status.get("real_s3"),
        "archive_prune_enabled": prune_on,
        "safe_to_delete": False,
        "delete_locked": True,
        "lock_reason": (
            None
            if ok
            else (live.get("reason") or "Re-Verify failed")
        ),
        "cleanup_status": "DISABLED" if not prune_on else ("MONGO PRESENT" if (mongo_count or 0) > 0 else "MONGO ABSENT"),
    }


async def dry_run_delete(
    db,
    *,
    archive_id: str,
    brand: Optional[str] = None,
    dealer: Optional[str] = None,
    branch: Optional[str] = None,
) -> Dict[str, Any]:
    report = await verify_archive(db, archive_id=archive_id, brand=brand, dealer=dealer, branch=branch)
    q = _product_query(report.get("archive_date") or "", brand, dealer, branch)
    return {
        **report,
        "dry_run": True,
        "deletion_scope_query": q,
        "mongo_matching_count": int(report.get("mongo_count") or 0),
        "s3_archived_count": int(report.get("s3_count") or 0),
        "checksum_status": report.get("sha256_status"),
        "real_s3_status": report.get("real_s3"),
        "would_delete_count": int(report.get("mongo_count") or 0) if report.get("safe_to_delete") else 0,
        "deleted": 0,
        "message": "Dry run only — zero records deleted",
    }


async def delete_mongo_for_archive(
    db,
    *,
    archive_id: str,
    current_user,
    confirm_text: str,
    brand: Optional[str] = None,
    dealer: Optional[str] = None,
    branch: Optional[str] = None,
    request_meta: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Delete ONLY Mongo products covered by a freshly verified SAFE archive."""
    if _text(confirm_text) != "DELETE":
        return {
            "status": "blocked",
            "deleted": 0,
            "reason": "Confirmation text must be exactly DELETE",
        }
    role = getattr(current_user, "role", None) or (current_user or {}).get("role")
    if role != "master":
        return {"status": "blocked", "deleted": 0, "reason": "Only Master Admin can delete Mongo archive source rows"}

    if not get_storage().status().get("archive_prune_enabled"):
        return {
            "status": "blocked",
            "deleted": 0,
            "reason": "Locked — Mongo cleanup disabled (ARCHIVE_PRUNE_ENABLED=false)",
        }

    report = await verify_archive(db, archive_id=archive_id, brand=brand, dealer=dealer, branch=branch)
    if not report.get("safe_to_delete"):
        await _write_audit(
            db,
            current_user=current_user,
            archive_id=archive_id,
            report=report,
            deleted=0,
            status="blocked",
            request_meta=request_meta,
        )
        return {
            "status": "blocked",
            "deleted": 0,
            "expected": report.get("s3_count"),
            "matched": report.get("mongo_count"),
            "remaining": report.get("mongo_count"),
            "reason": report.get("reason"),
            "verification": report,
        }

    date_iso = report["archive_date"]
    q = _product_query(date_iso, brand, dealer, branch)
    matched = await db.products.count_documents(q)
    expected = int(report.get("s3_count") or 0)
    if matched != expected:
        await _write_audit(
            db,
            current_user=current_user,
            archive_id=archive_id,
            report={**report, "reason": f"Pre-delete count drift matched={matched} expected={expected}"},
            deleted=0,
            status="blocked",
            request_meta=request_meta,
        )
        return {
            "status": "blocked",
            "deleted": 0,
            "expected": expected,
            "matched": matched,
            "remaining": matched,
            "reason": f"Pre-delete count drift matched={matched} expected={expected}",
        }

    # Exact delete of matched query only — never {}.
    result = await db.products.delete_many(q)
    deleted = int(result.deleted_count or 0)
    remaining = await db.products.count_documents(
        {
            "publish_status": "Published",
            "active_date_key": {"$in": [ha.iso_to_date_key(date_iso), date_iso]},
        }
    )

    # Best-effort: remove matching batch summaries for the same date/scope
    bq: Dict[str, Any] = {"active_date_key": {"$in": [ha.iso_to_date_key(date_iso), date_iso]}}
    if brand:
        bq["brand_name"] = brand
    if dealer:
        bq["dealer_name"] = dealer
    if branch:
        bq["branch"] = branch
    try:
        await db.batch_summaries.delete_many(bq)
    except Exception:
        pass

    if deleted == expected and remaining == 0:
        await am.mark_status(
            db,
            archive_id,
            am.STATUS_PRUNED,
            pruned_at=_utcnow().isoformat(),
            eligible_for_prune=False,
            mongo_deleted_count=deleted,
        )
    status = "deleted" if deleted == expected else "partial"
    await _write_audit(
        db,
        current_user=current_user,
        archive_id=archive_id,
        report=report,
        deleted=deleted,
        status=status,
        request_meta=request_meta,
    )
    return {
        "status": status,
        "expected": expected,
        "matched": matched,
        "deleted": deleted,
        "remaining": remaining,
        "collection": COLLECTION,
        "archive_date": date_iso,
        "storage_key": report.get("storage_key"),
        "verification": report,
    }


async def _write_audit(
    db,
    *,
    current_user,
    archive_id: str,
    report: Dict[str, Any],
    deleted: int,
    status: str,
    request_meta: Optional[Dict[str, Any]] = None,
) -> None:
    meta = request_meta or {}
    doc = {
        "id": str(uuid.uuid4()),
        "action": "mongo_archive_delete",
        "status": status,
        "timestamp": _utcnow().isoformat(),
        "admin_user_id": getattr(current_user, "id", None) or getattr(current_user, "user_id", None),
        "admin_name": getattr(current_user, "username", None) or getattr(current_user, "name", None),
        "admin_email": getattr(current_user, "email", None),
        "role": getattr(current_user, "role", None),
        "archive_id": archive_id,
        "archive_date": report.get("archive_date"),
        "brand": report.get("brand"),
        "dealer": report.get("dealer"),
        "branch": report.get("branch"),
        "brands": report.get("brands"),
        "dealers": report.get("dealers"),
        "branches": report.get("branches"),
        "collection": COLLECTION,
        "storage_key": report.get("storage_key"),
        "archive_sha256": report.get("sha256"),
        "expected_count": report.get("s3_count"),
        "deleted_count": deleted,
        "mongo_count_at_check": report.get("mongo_count"),
        "safe_to_delete": report.get("safe_to_delete"),
        "reason": report.get("reason"),
        "client_ip": meta.get("client_ip"),
        "user_agent": meta.get("user_agent"),
    }
    try:
        await db.storage_cleanup_audit.insert_one(doc)
    except Exception as exc:
        logger.warning("storage cleanup audit write failed: %s", exc)
