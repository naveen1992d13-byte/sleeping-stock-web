"""Event-driven archive handlers: upload / publish / order / request.

HTTP handlers enqueue jobs; this module performs S3 writes off the request path.
Gzip of large Product Hub batches happens in the worker, never inside publish-v2.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Dict, List, Optional

import archive_keys as ak
import archive_manifest as am
import archive_outbox as ao
import archive_verify as av
import history_archive as ha
from s3_storage import ImmutableObjectError, get_storage, sha256_bytes

logger = logging.getLogger(__name__)

LIFECYCLE_ACTIVE = am.LIFECYCLE_ACTIVE
LIFECYCLE_SUPERSEDED = am.LIFECYCLE_SUPERSEDED
LIFECYCLE_CANCELLED = am.LIFECYCLE_CANCELLED


def _text(value: Any) -> str:
    return str(value or "").strip()


def _scope(rows: List[Dict[str, Any]]) -> Dict[str, set]:
    brands, dealers, branches = set(), set(), set()
    for r in rows:
        b = r.get("brand_name") or r.get("brand") or r.get("requesting_brand") or r.get("supplying_brand")
        d = r.get("dealer_name") or r.get("dealer") or r.get("requesting_dealer") or r.get("supplying_dealer")
        br = r.get("branch") or r.get("branch_name") or r.get("requesting_branch") or r.get("supplying_branch")
        if b:
            brands.add(str(b))
        if d:
            dealers.add(str(d))
        if br:
            branches.add(str(br))
    return {"brands": brands, "dealers": dealers, "branches": branches}


async def _put_verified(
    db,
    *,
    module: str,
    archive_date: str,
    storage_key: str,
    data: bytes,
    source_collection: str,
    record_count: int,
    entity_id: str,
    lifecycle_status: str,
    extra_fields: Optional[Dict[str, Any]] = None,
    content_type: str = "application/gzip",
    require_jsonl_count: Optional[bool] = None,
) -> Dict[str, Any]:
    storage = get_storage()
    digest = sha256_bytes(data)
    existing = await am.find_any_entity(db, module, entity_id)
    if existing and existing.get("status") in {am.STATUS_VERIFIED, am.STATUS_PRUNED}:
        if existing.get("sha256") == digest and existing.get("storage_key") == storage_key:
            return {"status": "already_verified", "manifest": existing}
        if existing.get("status") in {am.STATUS_VERIFIED, am.STATUS_PRUNED} and existing.get("sha256") != digest:
            return {
                "status": "already_verified",
                "manifest": existing,
                "error": "existing VERIFIED/PRUNED object is immutable; refusing different bytes",
            }

    extra = {
        "entity_id": entity_id,
        "lifecycle_status": lifecycle_status,
        "original_storage_key": (existing or {}).get("original_storage_key") or storage_key,
    }
    if extra_fields:
        extra.update(extra_fields)

    allow_replace = bool(existing and existing.get("status") in {am.STATUS_FAILED, am.STATUS_CREATING, am.STATUS_UPLOADED})
    if "/cancelled/" in storage_key:
        allow_replace = False

    jsonl = require_jsonl_count if require_jsonl_count is not None else storage_key.endswith(".jsonl.gz")

    if not storage.is_s3():
        try:
            storage.upload_bytes(storage_key, data, content_type=content_type, allow_replace=allow_replace)
        except Exception:
            pass
        manifest = existing or am.base_manifest(
            module=module,
            archive_date=archive_date,
            storage_key=storage_key,
            source_collection=source_collection,
        )
        manifest.update(
            {
                "storage_key": storage_key,
                "record_count": record_count,
                "file_size": len(data),
                "sha256": digest,
                "source_collection": source_collection,
                **extra,
            }
        )
        manifest = await am.upsert_manifest(db, manifest)
        await am.mark_status(
            db,
            manifest["archive_id"],
            am.STATUS_FAILED,
            error="S3 credentials/config unavailable — local fallback is not REAL S3",
            eligible_for_prune=False,
            storage_backend=storage.mode,
        )
        raise RuntimeError("REAL S3 unavailable — archive not TRANSFERRED & VERIFIED")

    if storage.exists(storage_key):
        pre = av.physical_s3_verify(
            storage_key=storage_key,
            expected_sha256=digest,
            expected_size=len(data),
            expected_record_count=record_count if jsonl else None,
            require_jsonl_count=jsonl,
        )
        if pre.get("ok"):
            manifest = existing or am.base_manifest(
                module=module,
                archive_date=archive_date,
                storage_key=storage_key,
                source_collection=source_collection,
            )
            manifest.update(
                {
                    "storage_key": storage_key,
                    "record_count": record_count,
                    "file_size": len(data),
                    "sha256": digest,
                    "source_collection": source_collection,
                    **extra,
                }
            )
            manifest = await am.upsert_manifest(db, manifest)
            marked = await am.mark_status(
                db,
                manifest["archive_id"],
                am.STATUS_VERIFIED,
                eligible_for_prune=True,
                storage_backend="s3",
                error=None,
                reconciled=True,
            )
            return {"status": "already_archived", "manifest": marked}

    try:
        stored = storage.upload_bytes(
            storage_key, data, content_type=content_type, allow_replace=allow_replace
        )
    except ImmutableObjectError as exc:
        existing_head = storage.head(storage_key) or {}
        if existing:
            return {"status": "already_archived", "manifest": existing, "error": str(exc), "storage_key": storage_key}
        return {
            "status": "already_archived",
            "error": str(exc),
            "storage_key": storage_key,
            "existing_sha256": existing_head.get("sha256"),
        }

    provider = str(getattr(stored, "storage_provider", None) or "").lower()
    if provider == "local":
        raise RuntimeError("Upload resolved to local fallback, not REAL S3")

    live = av.physical_s3_verify(
        storage_key=storage_key,
        expected_sha256=digest,
        expected_size=len(data),
        expected_record_count=record_count if jsonl else None,
        require_jsonl_count=jsonl,
    )
    if not live.get("ok"):
        raise RuntimeError(live.get("reason") or "Archive integrity verification failed")

    scope = extra_fields or {}
    manifest = existing or am.base_manifest(
        module=module,
        archive_date=archive_date,
        storage_key=storage_key,
        source_collection=source_collection,
    )
    brands = set(scope.get("scope_brands") or [])
    dealers = set(scope.get("scope_dealers") or [])
    branches = set(scope.get("scope_branches") or [])
    manifest.update(
        {
            "storage_key": storage_key,
            "record_count": record_count,
            "file_size": len(data),
            "sha256": digest,
            "source_collection": source_collection,
            "min_date": archive_date,
            "max_date": archive_date,
            "brand_count": len(brands),
            "dealer_count": len(dealers),
            "branch_count": len(branches),
            **extra,
        }
    )
    manifest = await am.upsert_manifest(db, manifest)
    marked = await am.mark_status(
        db,
        manifest["archive_id"],
        am.STATUS_VERIFIED,
        eligible_for_prune=True,
        storage_backend="s3",
        error=None,
    )
    return {"status": "verified", "manifest": marked, "record_count": record_count}


async def _safe_move_to_cancelled(
    db,
    *,
    module: str,
    entity_id: str,
    src_key: str,
    lifecycle_status: str,
    extra_fields: Optional[Dict[str, Any]] = None,
    expected_sha256: str = "",
    expected_size: int = 0,
    expected_record_count: Optional[int] = None,
    require_jsonl_count: bool = False,
) -> Dict[str, Any]:
    """Copy current → cancelled, verify dest, delete source. Source untouched on failure."""
    dest_key = ak.cancelled_key_from_current(src_key)
    storage = get_storage()
    if not storage.is_s3():
        raise RuntimeError("REAL S3 unavailable — cancelled move not written")
    if not src_key:
        return {"status": "error", "error": "source storage_key required"}

    moved = storage.move_object(
        src_key,
        dest_key,
        expected_sha256=expected_sha256 or None,
        expected_size=expected_size or None,
    )
    live = av.physical_s3_verify(
        storage_key=dest_key,
        expected_sha256=moved.sha256 or expected_sha256,
        expected_size=moved.file_size or expected_size,
        expected_record_count=expected_record_count,
        require_jsonl_count=require_jsonl_count,
    )
    if not live.get("ok"):
        raise RuntimeError(live.get("reason") or "Cancelled dest failed verification after move")
    if storage.exists(src_key) and src_key != dest_key:
        raise RuntimeError("Source current object still present after move — retry")

    extra = {
        "entity_id": entity_id,
        "lifecycle_status": lifecycle_status,
        "original_storage_key": src_key,
        "cancelled_storage_key": dest_key,
        "storage_key": dest_key,
        "file_size": moved.file_size,
        "sha256": moved.sha256,
    }
    if extra_fields:
        extra.update(extra_fields)

    existing = await am.find_any_entity(db, module, entity_id)
    manifest = existing or am.base_manifest(
        module=module,
        archive_date=(extra_fields or {}).get("archive_date"),
        storage_key=dest_key,
        source_collection=(existing or {}).get("source_collection") or "",
    )
    manifest.update(extra)
    manifest = await am.upsert_manifest(db, manifest)
    marked = await am.mark_status(
        db,
        manifest["archive_id"],
        am.STATUS_VERIFIED,
        eligible_for_prune=True,
        storage_backend="s3",
        error=None,
        **{k: v for k, v in extra.items() if k != "storage_key"},
        storage_key=dest_key,
    )
    return {"status": "verified", "manifest": marked, "cancelled_storage_key": dest_key, "original_storage_key": src_key}


def _upload_archive_date(upload: Dict[str, Any]) -> str:
    dk = _text(upload.get("date_key"))
    if len(dk) == 8 and dk.isdigit():
        return ha.date_key_to_iso(dk)
    created = _text(upload.get("created_at"))
    if len(created) >= 10:
        return created[:10]
    return ha.date_key_to_iso(ha.iso_to_date_key(_text(upload.get("upload_date"))))


async def handle_upload_stored(db, payload: Dict[str, Any]) -> Dict[str, Any]:
    """Original Excel is already on S3. Verify + write an entity manifest; never re-upload."""
    upload_id = _text(payload.get("upload_id"))
    upload = await db.uploads.find_one({"id": upload_id}, {"_id": 0}) or {}
    key = _text(payload.get("storage_key") or upload.get("storage_key"))
    if not upload_id or not key:
        return {"status": "error", "error": "upload_id/storage_key required"}
    storage = get_storage()
    if not storage.is_s3():
        raise RuntimeError("REAL S3 unavailable — Excel archive not verified")
    digest = _text(payload.get("sha256") or upload.get("sha256"))
    size = int(payload.get("file_size") or upload.get("file_size") or 0)
    live = av.physical_s3_verify(
        storage_key=key,
        expected_sha256=digest,
        expected_size=size,
        require_jsonl_count=False,
    )
    if not live.get("ok"):
        # Size/sha may be unknown on legacy rows — require object exists + readable.
        head = storage.head(key)
        if not head or str(head.get("storage_provider") or "").lower() == "local":
            raise RuntimeError(live.get("reason") or "Excel S3 object missing")
        try:
            data, _ = storage.download_bytes(key)
        except Exception as exc:
            raise RuntimeError(f"Excel S3 unreadable: {exc}") from exc
        digest = digest or sha256_bytes(data)
        size = size or len(data)
        if digest and sha256_bytes(data) != digest:
            raise RuntimeError("Excel S3 checksum mismatch")
        live = {"ok": True, "real_s3": True}

    archive_date = _upload_archive_date(upload) or payload.get("archive_date")
    existing = await am.find_any_entity(db, ak.MODULE_UPLOADS, upload_id)
    manifest = existing or am.base_manifest(
        module=ak.MODULE_UPLOADS,
        archive_date=archive_date,
        storage_key=key,
        format="xlsx",
        source_collection="uploads",
    )
    manifest.update(
        {
            "storage_key": key,
            "original_storage_key": key,
            "record_count": 1,
            "file_size": size or int((storage.head(key) or {}).get("file_size") or 0),
            "sha256": digest,
            "entity_id": upload_id,
            "lifecycle_status": LIFECYCLE_ACTIVE,
            "source_collection": "uploads",
            "format": "xlsx",
        }
    )
    manifest = await am.upsert_manifest(db, manifest)
    marked = await am.mark_status(
        db,
        manifest["archive_id"],
        am.STATUS_VERIFIED,
        eligible_for_prune=True,
        storage_backend="s3",
        error=None,
    )
    return {"status": "verified", "manifest": marked}


async def handle_upload_cancelled(db, payload: Dict[str, Any]) -> Dict[str, Any]:
    """SAFE MOVE: copy Excel current → cancelled, verify dest, then delete source."""
    upload_id = _text(payload.get("upload_id"))
    upload = await db.uploads.find_one({"id": upload_id}, {"_id": 0}) or {}
    src_key = _text(payload.get("original_storage_key") or upload.get("storage_key"))
    if not upload_id or not src_key:
        return {"status": "error", "error": "upload_id/storage_key required"}
    archive_date = _upload_archive_date(upload) or payload.get("archive_date")
    extra = {
        "archive_date": archive_date,
        "cancelled_by": payload.get("cancelled_by") or upload.get("cancelled_by"),
        "cancelled_at": payload.get("cancelled_at") or upload.get("cancelled_at"),
        "cancel_reason": payload.get("reason") or upload.get("cancel_reason"),
        "record_count": 1,
        "format": "xlsx",
        "source_collection": "uploads",
    }
    result = await _safe_move_to_cancelled(
        db,
        module=ak.MODULE_UPLOADS,
        entity_id=upload_id,
        src_key=src_key,
        lifecycle_status=LIFECYCLE_CANCELLED,
        extra_fields=extra,
        expected_sha256=_text(upload.get("sha256")),
        expected_size=int(upload.get("file_size") or 0),
        require_jsonl_count=False,
    )
    dest_key = result.get("cancelled_storage_key")
    if dest_key:
        try:
            await db.uploads.update_one({"id": upload_id}, {"$set": {"storage_key": dest_key}})
        except Exception as exc:
            logger.warning("upload storage_key pointer update skipped: %s", type(exc).__name__)

    ph_existing = await am.find_any_entity(db, ak.MODULE_PRODUCT_HISTORY, upload_id)
    if not ph_existing:
        ph_existing = await am.find_any_entity(db, "product-hub", upload_id)
    storage = get_storage()
    ph_src = _text((ph_existing or {}).get("storage_key"))
    if ph_src and "/current/" in ph_src and storage.exists(ph_src):
        await _safe_move_to_cancelled(
            db,
            module=_text((ph_existing or {}).get("module")) or ak.MODULE_PRODUCT_HISTORY,
            entity_id=upload_id,
            src_key=ph_src,
            lifecycle_status=LIFECYCLE_CANCELLED,
            extra_fields={
                "archive_date": archive_date,
                "cancelled_by": extra["cancelled_by"],
                "cancelled_at": extra["cancelled_at"],
                "cancel_reason": extra["cancel_reason"],
                "record_count": int((ph_existing or {}).get("record_count") or 0),
            },
            expected_sha256=_text((ph_existing or {}).get("sha256")),
            expected_size=int((ph_existing or {}).get("file_size") or 0),
            expected_record_count=int((ph_existing or {}).get("record_count") or 0) or None,
            require_jsonl_count=True,
        )
    return result


async def handle_publish_completed(db, payload: Dict[str, Any]) -> Dict[str, Any]:
    """Archive one published upload batch to product-history/{date}/current/{upload_id}/."""
    upload_id = _text(payload.get("upload_id"))
    if not upload_id:
        return {"status": "error", "error": "upload_id required"}
    upload = await db.uploads.find_one({"id": upload_id}, {"_id": 0}) or {}
    archive_date = _upload_archive_date(upload) or payload.get("archive_date")
    date_key = ha.iso_to_date_key(archive_date)

    rows = await db.products.find({"upload_id": upload_id, "publish_status": "Published"}, {"_id": 0}).to_list(500000)
    if not rows:
        rows = await db.upload_items.find({"upload_id": upload_id}, {"_id": 0}).to_list(500000)
    rows = await ha._enrich_product_scope_rows(db, rows)

    summaries = await db.batch_summaries.find({"upload_id": upload_id}, {"_id": 0}).to_list(100)
    snap_docs = ha._build_analytics_snapshot_docs(archive_date, rows)

    key_kwargs = {
        "brand": upload.get("brand_name"),
        "dealer": upload.get("dealer_name"),
        "branch": upload.get("branch"),
        "upload_no": upload.get("upload_no"),
    }
    products_key = ak.product_history_products_key(archive_date, upload_id, cancelled=False, **key_kwargs)
    summary_key = ak.product_history_companion_key(
        archive_date, upload_id, "batch-summaries.json", cancelled=False, **key_kwargs
    )
    snap_key = ak.product_history_companion_key(
        archive_date, upload_id, "analytics-snapshots.json", cancelled=False, **key_kwargs
    )

    products_bytes = await asyncio.to_thread(ha._gzip_jsonl, rows)
    summary_bytes = json.dumps(summaries, ensure_ascii=False, default=str).encode("utf-8")
    snap_bytes = json.dumps(snap_docs, ensure_ascii=False, default=str).encode("utf-8")

    storage = get_storage()
    if storage.is_s3():
        try:
            storage.upload_bytes(summary_key, summary_bytes, content_type="application/json")
            storage.upload_bytes(snap_key, snap_bytes, content_type="application/json")
        except ImmutableObjectError:
            pass
        except Exception as exc:
            logger.warning("publish companion upload failed for %s: %s", upload_id, type(exc).__name__)

    # Derived analytics cache stays in Mongo (non-pruned) so Auto Perpetual keeps working.
    try:
        await ha.write_analytics_snapshots_for_date(db, archive_date, rows)
    except Exception as exc:
        logger.warning("analytics snapshot Mongo write skipped for %s: %s", upload_id, type(exc).__name__)

    scope = _scope(rows)
    extra = {
        "scope_brands": sorted(scope["brands"])[:200],
        "scope_dealers": sorted(scope["dealers"])[:200],
        "scope_branches": sorted(scope["branches"])[:200],
        "brand_name": upload.get("brand_name"),
        "dealer_name": upload.get("dealer_name"),
        "branch": upload.get("branch"),
        "upload_no": upload.get("upload_no"),
        "companion_keys": {"batch_summaries": summary_key, "analytics_snapshots": snap_key},
        "date_key": date_key,
        "reader_ready": True,
    }
    result = await _put_verified(
        db,
        module=ak.MODULE_PRODUCT_HISTORY,
        archive_date=archive_date,
        storage_key=products_key,
        data=products_bytes,
        source_collection="products",
        record_count=len(rows),
        entity_id=upload_id,
        lifecycle_status=LIFECYCLE_ACTIVE,
        extra_fields=extra,
    )
    return result


async def handle_publish_superseded(db, payload: Dict[str, Any]) -> Dict[str, Any]:
    """Same-day previous publish: SAFE MOVE current → cancelled, mark superseded."""
    upload_id = _text(payload.get("upload_id"))
    upload = await db.uploads.find_one({"id": upload_id}, {"_id": 0}) or {}
    archive_date = _upload_archive_date(upload) or payload.get("archive_date")
    existing = await am.find_any_entity(db, ak.MODULE_PRODUCT_HISTORY, upload_id)
    if not existing:
        existing = await am.find_any_entity(db, "product-hub", upload_id)
    src_key = _text((existing or {}).get("storage_key")) or ak.product_history_products_key(
        archive_date,
        upload_id,
        cancelled=False,
        brand=upload.get("brand_name"),
        dealer=upload.get("dealer_name"),
        branch=upload.get("branch"),
        upload_no=upload.get("upload_no"),
    )
    storage = get_storage()
    if not storage.is_s3():
        raise RuntimeError("REAL S3 unavailable — supersede move not written")

    if not storage.exists(src_key):
        if not existing or existing.get("status") != am.STATUS_VERIFIED:
            await ao.enqueue_safe(db, ao.EVENT_PUBLISH_COMPLETED, {"upload_id": upload_id})
            raise RuntimeError("supersede waiting for current publish archive")
        src_key = existing.get("storage_key") or src_key

    return await _safe_move_to_cancelled(
        db,
        module=_text((existing or {}).get("module")) or ak.MODULE_PRODUCT_HISTORY,
        entity_id=upload_id,
        src_key=src_key,
        lifecycle_status=LIFECYCLE_SUPERSEDED,
        extra_fields={
            "archive_date": archive_date,
            "cancelled_by": payload.get("superseded_by"),
            "cancelled_at": payload.get("superseded_at"),
            "cancel_reason": payload.get("reason") or "same-day republish",
            "record_count": int((existing or {}).get("record_count") or 0),
            "brand_name": upload.get("brand_name") or (existing or {}).get("brand_name"),
            "dealer_name": upload.get("dealer_name") or (existing or {}).get("dealer_name"),
            "branch": upload.get("branch") or (existing or {}).get("branch"),
            "upload_no": upload.get("upload_no") or (existing or {}).get("upload_no"),
            "reader_ready": True,
        },
        expected_sha256=_text((existing or {}).get("sha256")),
        expected_size=int((existing or {}).get("file_size") or 0),
        expected_record_count=int((existing or {}).get("record_count") or 0) or None,
        require_jsonl_count=True,
    )


def _package_rows(*groups: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for rows in groups:
        out.extend(rows or [])
    return out


async def _build_order_package(db, order_id: str) -> List[Dict[str, Any]]:
    header = await db.order_headers.find_one({"id": order_id}, {"_id": 0})
    if not header:
        return []
    items = await db.order_items.find({"order_id": order_id}, {"_id": 0}).to_list(10000)
    item_ids = [_text(it.get("id")) for it in items if _text(it.get("id"))]
    reqs = []
    if item_ids:
        reqs = await db.order_requests.find({"order_item_id": {"$in": item_ids}}, {"_id": 0}).to_list(50000)
    req_numbers = list({_text(r.get("request_number")) for r in reqs if _text(r.get("request_number"))})
    headers = []
    if req_numbers:
        headers = await db.request_headers.find({"request_number": {"$in": req_numbers}}, {"_id": 0}).to_list(5000)
    activity = await db.order_activity.find({"order_id": order_id}, {"_id": 0}).to_list(50000)
    rows = [{"record_type": "order_header", **header}]
    rows.extend({"record_type": "order_item", **it} for it in items)
    rows.extend({"record_type": "order_request", **r} for r in reqs)
    rows.extend({"record_type": "request_header", **h} for h in headers)
    rows.extend({"record_type": "order_activity", **a} for a in activity)
    return rows


async def handle_order_terminal(db, payload: Dict[str, Any]) -> Dict[str, Any]:
    order_id = _text(payload.get("order_id"))
    if not order_id:
        return {"status": "error", "error": "order_id required"}
    header = await db.order_headers.find_one({"id": order_id}, {"_id": 0}) or {}
    items = await db.order_items.find({"order_id": order_id}, {"_id": 0}).to_list(10000)
    if not ak.order_is_fully_terminal(items) and not ak.is_order_item_terminal(header.get("status") or header.get("overall_status")):
        # Header Completed (factory) with items Accepted is still operational; only archive true terminals.
        statuses = [_text(it.get("status")) for it in items]
        if not statuses or any(s not in ak.ORDER_ITEM_TERMINAL_STATUSES and s != "Completed" for s in statuses):
            if _text(header.get("status") or header.get("overall_status")) not in ak.ORDER_ITEM_TERMINAL_STATUSES | ak.TERMINAL_CURRENT_STATUSES:
                return {"status": "error", "error": "order is not fully terminal"}

    rows = await _build_order_package(db, order_id)
    if not rows:
        return {"status": "error", "error": "order package empty"}

    status = _text(payload.get("status") or header.get("overall_status") or header.get("status"))
    if not status:
        item_statuses = {_text(it.get("status")) for it in items}
        if item_statuses <= {"Cancelled", "Cancelled – No Response", "Cancelled - No Response"}:
            status = "Cancelled"
        elif "Rejected" in item_statuses or "No Further Stock" in item_statuses or "No Further Stock Available" in item_statuses:
            status = next(iter(item_statuses))
        else:
            status = "Completed"
    cancelled = ak.lifecycle_from_status(status) == ak.LIFECYCLE_CANCELLED
    archive_date = (header.get("updated_at") or header.get("completed_at") or header.get("created_at") or "")[:10]
    if len(archive_date) != 10:
        archive_date = ha.date_key_to_iso(_text(header.get("date_key"))) or archive_date
    existing = await am.find_any_entity(db, ak.MODULE_ORDERS, order_id)
    existing_key = _text((existing or {}).get("storage_key"))
    storage = get_storage()
    extra = {
        "scope_brands": sorted(_scope([header, *items])["brands"])[:200],
        "scope_dealers": sorted(_scope([header, *items])["dealers"])[:200],
        "scope_branches": sorted(_scope([header, *items])["branches"])[:200],
        "order_number": header.get("order_number"),
        "terminal_status": status,
        "brand_name": header.get("brand_name"),
        "dealer_name": header.get("dealer_name"),
        "branch": header.get("branch"),
        "reader_ready": True,
        "cancelled_by": payload.get("cancelled_by") or header.get("cancelled_by"),
        "cancelled_at": payload.get("cancelled_at") or header.get("cancelled_at"),
        "cancel_reason": payload.get("reason") or header.get("cancel_reason"),
        "archive_date": archive_date,
    }
    if cancelled and existing_key and "/current/" in existing_key and storage.exists(existing_key):
        return await _safe_move_to_cancelled(
            db,
            module=ak.MODULE_ORDERS,
            entity_id=order_id,
            src_key=existing_key,
            lifecycle_status=LIFECYCLE_CANCELLED,
            extra_fields=extra,
            expected_sha256=_text((existing or {}).get("sha256")),
            expected_size=int((existing or {}).get("file_size") or 0),
            expected_record_count=int((existing or {}).get("record_count") or 0) or None,
            require_jsonl_count=True,
        )
    key = ak.order_package_key(
        archive_date,
        order_id,
        cancelled=cancelled,
        brand=header.get("brand_name"),
        dealer=header.get("dealer_name"),
        branch=header.get("branch"),
        order_number=header.get("order_number"),
    )
    data = await asyncio.to_thread(ha._gzip_jsonl, rows)
    result = await _put_verified(
        db,
        module=ak.MODULE_ORDERS,
        archive_date=archive_date,
        storage_key=key,
        data=data,
        source_collection="order_headers+order_items+order_requests",
        record_count=len(rows),
        entity_id=order_id,
        lifecycle_status=LIFECYCLE_CANCELLED if cancelled else LIFECYCLE_ACTIVE,
        extra_fields=extra,
    )
    if cancelled and result.get("manifest"):
        await am.mark_status(
            db,
            result["manifest"]["archive_id"],
            result["manifest"].get("status") or am.STATUS_VERIFIED,
            cancelled_storage_key=key,
            original_storage_key=key,
        )
    return result


async def _build_request_package(db, request_id: str) -> List[Dict[str, Any]]:
    req = await db.order_requests.find_one({"id": request_id}, {"_id": 0})
    if not req:
        return []
    number = _text(req.get("request_number"))
    siblings = []
    headers = []
    if number:
        siblings = await db.order_requests.find({"request_number": number}, {"_id": 0}).to_list(10000)
        headers = await db.request_headers.find({"request_number": number}, {"_id": 0}).to_list(100)
    activity = await db.order_activity.find({"request_id": request_id}, {"_id": 0}).to_list(20000)
    if number:
        extra_act = await db.order_activity.find({"request_number": number}, {"_id": 0}).to_list(20000)
        seen = {_text(a.get("id")) for a in activity}
        for a in extra_act:
            if _text(a.get("id")) not in seen:
                activity.append(a)
    rows = [{"record_type": "order_request", **r} for r in (siblings or [req])]
    rows.extend({"record_type": "request_header", **h} for h in headers)
    rows.extend({"record_type": "order_activity", **a} for a in activity)
    return rows


async def handle_request_terminal(db, payload: Dict[str, Any]) -> Dict[str, Any]:
    request_id = _text(payload.get("request_id"))
    if not request_id:
        return {"status": "error", "error": "request_id required"}
    req = await db.order_requests.find_one({"id": request_id}, {"_id": 0}) or {}
    status = _text(payload.get("status") or req.get("status"))
    if not ak.is_request_terminal(status):
        return {"status": "error", "error": f"request status {status} is not terminal"}
    rows = await _build_request_package(db, request_id)
    if not rows:
        return {"status": "error", "error": "request package empty"}
    cancelled = ak.lifecycle_from_status(status) == ak.LIFECYCLE_CANCELLED
    archive_date = (req.get("updated_at") or req.get("completed_at") or req.get("decided_at") or req.get("requested_at") or "")[:10]
    existing = await am.find_any_entity(db, ak.MODULE_REQUESTS, request_id)
    existing_key = _text((existing or {}).get("storage_key"))
    storage = get_storage()
    extra = {
        "scope_brands": sorted(_scope([req])["brands"])[:200],
        "scope_dealers": sorted(_scope([req])["dealers"])[:200],
        "scope_branches": sorted(_scope([req])["branches"])[:200],
        "request_number": req.get("request_number"),
        "terminal_status": status,
        "reader_ready": True,
        "cancelled_by": payload.get("cancelled_by") or req.get("decided_by") or req.get("cancelled_by"),
        "cancelled_at": payload.get("cancelled_at") or req.get("decided_at") or req.get("cancelled_at"),
        "cancel_reason": payload.get("reason") or req.get("approval_remarks") or req.get("cancel_reason"),
        "archive_date": archive_date,
        "brand_name": req.get("requesting_brand") or req.get("brand_name"),
        "dealer_name": req.get("requesting_dealer") or req.get("dealer_name"),
        "branch": req.get("requesting_branch") or req.get("branch"),
    }
    if cancelled and existing_key and "/current/" in existing_key and storage.exists(existing_key):
        return await _safe_move_to_cancelled(
            db,
            module=ak.MODULE_REQUESTS,
            entity_id=request_id,
            src_key=existing_key,
            lifecycle_status=LIFECYCLE_CANCELLED,
            extra_fields=extra,
            expected_sha256=_text((existing or {}).get("sha256")),
            expected_size=int((existing or {}).get("file_size") or 0),
            expected_record_count=int((existing or {}).get("record_count") or 0) or None,
            require_jsonl_count=True,
        )
    key = ak.request_package_key(
        archive_date,
        request_id,
        cancelled=cancelled,
        brand=extra.get("brand_name"),
        dealer=extra.get("dealer_name"),
        branch=extra.get("branch"),
        request_number=req.get("request_number"),
    )
    data = await asyncio.to_thread(ha._gzip_jsonl, rows)
    return await _put_verified(
        db,
        module=ak.MODULE_REQUESTS,
        archive_date=archive_date,
        storage_key=key,
        data=data,
        source_collection="order_requests+request_headers+order_activity",
        record_count=len(rows),
        entity_id=request_id,
        lifecycle_status=LIFECYCLE_CANCELLED if cancelled else LIFECYCLE_ACTIVE,
        extra_fields=extra,
    )


HANDLER_MAP = {
    ao.EVENT_UPLOAD_STORED: handle_upload_stored,
    ao.EVENT_UPLOAD_CANCELLED: handle_upload_cancelled,
    ao.EVENT_PUBLISH_COMPLETED: handle_publish_completed,
    ao.EVENT_PUBLISH_SUPERSEDED: handle_publish_superseded,
    ao.EVENT_ORDER_TERMINAL: handle_order_terminal,
    ao.EVENT_REQUEST_TERMINAL: handle_request_terminal,
}


async def maybe_enqueue_publish(db, upload: Dict[str, Any], *, actor_id: str = "", now_iso: str = "") -> None:
    upload_id = _text(upload.get("id"))
    if not upload_id:
        return
    await ao.enqueue_safe(db, ao.EVENT_PUBLISH_COMPLETED, {"upload_id": upload_id})
    # Same-day prior publishes for this Brand/Dealer/Branch become superseded.
    date_key = _text(upload.get("date_key"))
    q = {
        "id": {"$ne": upload_id},
        "upload_type": "product",
        "publish_status": "Published",
        "brand_name": upload.get("brand_name"),
        "dealer_name": upload.get("dealer_name"),
        "branch": upload.get("branch"),
    }
    if date_key:
        q["date_key"] = date_key
    prior = await db.uploads.find(q, {"_id": 0, "id": 1}).to_list(50)
    for row in prior:
        pid = _text(row.get("id"))
        if pid:
            await ao.enqueue_safe(
                db,
                ao.EVENT_PUBLISH_SUPERSEDED,
                {
                    "upload_id": pid,
                    "superseded_by": upload_id,
                    "superseded_at": now_iso,
                    "reason": "same-day republish",
                },
            )


async def maybe_enqueue_upload_stored(db, upload: Dict[str, Any]) -> None:
    await ao.enqueue_safe(
        db,
        ao.EVENT_UPLOAD_STORED,
        {
            "upload_id": upload.get("id"),
            "storage_key": upload.get("storage_key"),
            "sha256": upload.get("sha256"),
            "file_size": upload.get("file_size"),
        },
    )


async def maybe_enqueue_upload_cancelled(db, upload: Dict[str, Any], *, actor_id: str = "", reason: str = "", now_iso: str = "") -> None:
    await ao.enqueue_safe(
        db,
        ao.EVENT_UPLOAD_CANCELLED,
        {
            "upload_id": upload.get("id"),
            "original_storage_key": upload.get("storage_key"),
            "cancelled_by": actor_id,
            "cancelled_at": now_iso,
            "reason": reason,
        },
    )


async def maybe_enqueue_order_terminal(db, order_id: str, *, status: str = "", actor_id: str = "", reason: str = "") -> None:
    order_id = _text(order_id)
    if not order_id:
        return
    try:
        items = await db.order_items.find({"order_id": order_id}, {"_id": 0, "status": 1}).to_list(10000)
        header = await db.order_headers.find_one({"id": order_id}, {"_id": 0, "status": 1, "overall_status": 1}) or {}
        header_status = _text(status or header.get("overall_status") or header.get("status"))
        if not ak.order_is_fully_terminal(items) and header_status not in ak.ORDER_ITEM_TERMINAL_STATUSES | ak.TERMINAL_CURRENT_STATUSES:
            return
        await ao.enqueue_safe(
            db,
            ao.EVENT_ORDER_TERMINAL,
            {"order_id": order_id, "status": header_status, "cancelled_by": actor_id, "reason": reason},
        )
    except Exception as exc:
        logger.warning("maybe_enqueue_order_terminal failed: %s", type(exc).__name__)


async def maybe_enqueue_request_terminal(db, request: Dict[str, Any], *, actor_id: str = "") -> None:
    try:
        status = _text(request.get("status"))
        request_id = _text(request.get("id"))
        if not request_id or not ak.is_request_terminal(status):
            return
        await ao.enqueue_safe(
            db,
            ao.EVENT_REQUEST_TERMINAL,
            {
                "request_id": request_id,
                "status": status,
                "cancelled_by": actor_id,
                "reason": request.get("approval_remarks") or request.get("cancel_reason"),
            },
        )
        order_id = _text(request.get("order_id"))
        if order_id:
            await maybe_enqueue_order_terminal(db, order_id, actor_id=actor_id)
    except Exception as exc:
        logger.warning("maybe_enqueue_request_terminal failed: %s", type(exc).__name__)


async def catch_up_missed_events(db, archive_date: str) -> Dict[str, Any]:
    """Night/safety-net: enqueue archives that were missed during the day. Does not delete Mongo."""
    date_iso = ha.date_key_to_iso(archive_date)
    date_key = ha.iso_to_date_key(date_iso)
    enqueued = {"upload_stored": 0, "publish": 0, "orders": 0, "requests": 0, "upload_cancelled": 0}

    uploads = await db.uploads.find(
        {"$or": [{"date_key": {"$in": [date_key, date_iso]}}, {"created_at": {"$regex": f"^{date_iso}"}}]},
        {"_id": 0},
    ).to_list(20000)
    for u in uploads:
        uid = _text(u.get("id"))
        if not uid:
            continue
        if u.get("storage_key") and not await am.find_verified_entity(db, ak.MODULE_UPLOADS, uid):
            await ao.enqueue_safe(db, ao.EVENT_UPLOAD_STORED, {"upload_id": uid, "storage_key": u.get("storage_key"), "sha256": u.get("sha256"), "file_size": u.get("file_size")})
            enqueued["upload_stored"] += 1
        if _text(u.get("publish_status")) == "Published" and not await am.find_verified_entity(db, ak.MODULE_PRODUCT_HISTORY, uid):
            await ao.enqueue_safe(db, ao.EVENT_PUBLISH_COMPLETED, {"upload_id": uid})
            enqueued["publish"] += 1
        if _text(u.get("status") or u.get("publish_status")) == "Cancelled":
            row = await am.find_any_entity(db, ak.MODULE_UPLOADS, uid)
            if not row or row.get("lifecycle_status") != LIFECYCLE_CANCELLED:
                await ao.enqueue_safe(db, ao.EVENT_UPLOAD_CANCELLED, {"upload_id": uid, "original_storage_key": u.get("storage_key")})
                enqueued["upload_cancelled"] += 1

    headers = await db.order_headers.find({}, {"_id": 0, "id": 1, "status": 1, "overall_status": 1, "updated_at": 1}).to_list(20000)
    for h in headers:
        oid = _text(h.get("id"))
        if not oid:
            continue
        items = await db.order_items.find({"order_id": oid}, {"_id": 0, "status": 1, "updated_at": 1, "completed_at": 1}).to_list(5000)
        if not ak.order_is_fully_terminal(items) and _text(h.get("status") or h.get("overall_status")) not in ak.ORDER_ITEM_TERMINAL_STATUSES | ak.TERMINAL_CURRENT_STATUSES:
            continue
        if await am.find_verified_entity(db, ak.MODULE_ORDERS, oid):
            continue
        await ao.enqueue_safe(db, ao.EVENT_ORDER_TERMINAL, {"order_id": oid, "status": h.get("overall_status") or h.get("status")})
        enqueued["orders"] += 1

    reqs = await db.order_requests.find({"status": {"$in": list(ak.REQUEST_TERMINAL_STATUSES)}}, {"_id": 0}).to_list(50000)
    for r in reqs:
        rid = _text(r.get("id"))
        if not rid or await am.find_verified_entity(db, ak.MODULE_REQUESTS, rid):
            continue
        await ao.enqueue_safe(db, ao.EVENT_REQUEST_TERMINAL, {"request_id": rid, "status": r.get("status")})
        enqueued["requests"] += 1

    return {"archive_date": date_iso, "enqueued": enqueued}
