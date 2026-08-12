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
    """Archive/history rows for cleanup UI (up to N years)."""
    cutoff = (datetime.now(IST).date() - timedelta(days=max(1, int(years)) * 366)).isoformat()
    q: Dict[str, Any] = {
        "module": ha.MODULE_PRODUCT_HISTORY,
        "archive_date": {"$gte": cutoff},
    }
    rows = await db.archive_manifests.find(q, {"_id": 0}).sort("archive_date", -1).to_list(5000)
    out = []
    for m in rows:
        date_iso = m.get("archive_date")
        date_key = ha.iso_to_date_key(date_iso or "")
        scope_brands = _clean_names(m.get("scope_brands") or [])
        scope_dealers = _clean_names(m.get("scope_dealers") or [])
        scope_branches = _clean_names(m.get("scope_branches") or [])
        brand_codes = _clean_names(m.get("scope_brand_codes") or [])
        dealer_codes = _clean_names(m.get("scope_dealer_codes") or [])
        # Legacy manifests (archived before scope_* fields): derive display scope from Mongo only.
        if not scope_brands and not scope_dealers and not scope_branches:
            derived = await _scope_from_mongo(db, date_iso or "")
            scope_brands = derived["brands"] or scope_brands
            scope_dealers = derived["dealers"] or scope_dealers
            scope_branches = derived["branches"] or scope_branches
            brand_codes = brand_codes or derived["brand_codes"]
            dealer_codes = dealer_codes or derived["dealer_codes"]

        if brand and scope_brands and brand not in scope_brands:
            continue
        if dealer and scope_dealers and dealer not in scope_dealers:
            continue
        if branch and scope_branches and branch not in scope_branches:
            continue

        mongo_count = await db.products.count_documents(
            {
                "publish_status": "Published",
                "active_date_key": {"$in": [date_key, date_iso]},
            }
        )
        s3_count = int(m.get("record_count") or 0)
        has_fp = bool(m.get("source_fingerprint"))
        if m.get("status") == am.STATUS_PRUNED:
            data_changed = "ARCHIVED / PRUNED"
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
                "s3_record_count": s3_count,
                "file_size": m.get("file_size"),
                "archive_status": m.get("status"),
                "data_changed_status": data_changed,
                "sha256_status": "PRESENT" if m.get("sha256") else "MISSING",
                "storage_backend": m.get("storage_backend"),
                "sha256": m.get("sha256"),
                "storage_key": m.get("storage_key"),
                "eligible_for_prune": m.get("eligible_for_prune"),
                "mongo_data_status": (
                    "PRUNED"
                    if m.get("status") == am.STATUS_PRUNED
                    else ("PRESENT" if mongo_count > 0 else "ABSENT")
                ),
                "verified_at": m.get("verified_at"),
                "created_at": m.get("created_at"),
                "source_fingerprint_present": has_fp,
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
    """Fresh verification of a product-history archive for safe-delete eligibility."""
    manifest = await db.archive_manifests.find_one({"archive_id": archive_id}, {"_id": 0})
    if not manifest:
        return {"ok": False, "safe_to_delete": False, "reason": "Archive not found", "archive_id": archive_id}

    storage = get_storage()
    status = storage.status()
    date_iso = manifest.get("archive_date")
    key = manifest.get("storage_key")
    checks: Dict[str, Any] = {
        "real_s3": bool(storage.is_s3()),
        "archive_exists": True,
        "verified_manifest": manifest.get("status") in {am.STATUS_VERIFIED, am.STATUS_PRUNED},
        "object_exists": False,
        "object_readable": False,
        "sha256_match": False,
        "record_count_match": False,
        "date_scope_match": bool(date_iso),
        "source_unchanged": False,
        "archive_prune_enabled": bool(status.get("archive_prune_enabled")),
    }
    reasons = []

    if not storage.is_s3():
        reasons.append("REAL S3 is not active")
    if manifest.get("module") != ha.MODULE_PRODUCT_HISTORY:
        reasons.append("Only product-history archives are supported for Mongo delete")
        checks["verified_manifest"] = False
    if manifest.get("status") == am.STATUS_PRUNED:
        reasons.append("Archive already marked PRUNED")
    if not checks["verified_manifest"]:
        reasons.append(f"Manifest status is {manifest.get('status')}, need VERIFIED")

    today = datetime.now(IST).date().isoformat()
    if date_iso == today:
        reasons.append("Cannot delete today's live Product dataset")
        checks["date_scope_match"] = False

    head = storage.head(key) if key else None
    checks["object_exists"] = bool(head)
    if not head:
        reasons.append("S3 object missing")

    expected_sha = manifest.get("sha256") or ""
    expected_size = int(manifest.get("file_size") or 0)
    expected_count = int(manifest.get("record_count") or 0)
    archive_rows: List[Dict[str, Any]] = []
    try:
        if key:
            ok = storage.verify_object(key, expected_sha, expected_size)
            checks["sha256_match"] = bool(ok)
            data, _ = storage.download_bytes(key)
            got_sha = sha256_bytes(data)
            checks["object_readable"] = True
            checks["sha256_match"] = checks["sha256_match"] and got_sha == expected_sha
            with gzip.GzipFile(fileobj=BytesIO(data), mode="rb") as gz:
                for line in gz:
                    if line.strip():
                        archive_rows.append(json.loads(line.decode("utf-8")))
            checks["record_count_match"] = len(archive_rows) == expected_count
            if not checks["sha256_match"]:
                reasons.append("SHA256 mismatch")
            if not checks["record_count_match"]:
                reasons.append(
                    f"Record count mismatch archive_lines={len(archive_rows)} manifest={expected_count}"
                )
    except Exception as exc:
        checks["object_readable"] = False
        reasons.append(f"S3 read failed: {type(exc).__name__}")

    q = _product_query(date_iso, brand, dealer, branch)
    mongo_rows = await db.products.find(q, {"_id": 0}).to_list(500000)
    # Enrich both sides the same way archive does, for fair fingerprint compare
    mongo_rows = await ha._enrich_product_scope_rows(db, mongo_rows)
    if archive_rows:
        archive_rows = await ha._enrich_product_scope_rows(db, archive_rows)
    mongo_count = len(mongo_rows)
    mongo_fp = ha._source_fingerprint(mongo_rows)
    archived_fp = manifest.get("source_fingerprint") or (
        ha._source_fingerprint(archive_rows) if archive_rows else ""
    )
    source_changed = bool(archived_fp and mongo_fp and archived_fp != mongo_fp)
    # Also treat count drift as change when fingerprint missing on legacy manifests
    if not archived_fp and mongo_count != expected_count:
        source_changed = True
    checks["source_unchanged"] = (mongo_count == 0) or (not source_changed and mongo_count == expected_count)

    source_status = "NO_MONGO_ROWS" if mongo_count == 0 else ("SOURCE CHANGED AFTER ARCHIVE" if source_changed else "MATCHES ARCHIVE")
    if source_changed and mongo_count > 0:
        reasons.append("SOURCE CHANGED AFTER ARCHIVE")

    # Manual delete is allowed even when ARCHIVE_PRUNE_ENABLED=false — that flag only blocks auto prune.
    safe = (
        checks["real_s3"]
        and checks["verified_manifest"]
        and checks["object_exists"]
        and checks["object_readable"]
        and checks["sha256_match"]
        and checks["record_count_match"]
        and checks["date_scope_match"]
        and checks["source_unchanged"]
        and mongo_count > 0
        and manifest.get("status") == am.STATUS_VERIFIED
    )
    if mongo_count <= 0:
        reasons.append("No matching Mongo source rows to delete")
        safe = False

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
        "storage_backend": manifest.get("storage_backend") or status.get("storage_backend"),
        "sha256": expected_sha,
        "sha256_status": "MATCH" if checks["sha256_match"] else "MISMATCH",
        "s3_readable": checks["object_readable"],
        "archive_timestamp": manifest.get("verified_at") or manifest.get("created_at"),
        "mongo_count": mongo_count,
        "s3_count": expected_count,
        "source_change_status": source_status,
        "safe_to_delete": bool(safe),
        "reason": "SAFE TO DELETE" if safe else ("; ".join(reasons) or "NOT SAFE"),
        "checks": checks,
        "brands": scope_brands,
        "dealers": scope_dealers,
        "branches": scope_branches,
        "real_s3": status.get("real_s3"),
        "archive_prune_enabled": status.get("archive_prune_enabled"),
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
