"""Master-only Storage & Cost Monitor helpers (Mongo metrics + archive status)."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

import archive_manifest as am
import history_archive as ha
import storage_usage as su
from s3_storage import get_storage, product_mongo_hot_days

logger = logging.getLogger(__name__)
IST = ZoneInfo("Asia/Kolkata")


def _today_keys():
    today = datetime.now(IST).date()
    return today.strftime("%Y%m%d"), today.isoformat()


async def mongo_storage_metrics(db) -> Dict[str, Any]:
    """Best-effort MongoDB size metrics via dbStats / collStats."""
    metrics: Dict[str, Any] = {
        "label": "Calculated from MongoDB stats (not Atlas billing)",
        "data_size": None,
        "storage_size": None,
        "index_size": None,
        "collections": [],
        "product_count": None,
        "product_size": None,
        "today_product_count": None,
        "error": None,
    }
    try:
        stats = await db.command("dbStats")
        metrics["data_size"] = int(stats.get("dataSize") or 0)
        metrics["storage_size"] = int(stats.get("storageSize") or 0)
        metrics["index_size"] = int(stats.get("indexSize") or 0)
    except Exception as exc:
        metrics["error"] = f"dbStats unavailable: {type(exc).__name__}"

    today_key, today_iso = _today_keys()
    try:
        metrics["product_count"] = await db.products.count_documents({})
        metrics["today_product_count"] = await db.products.count_documents(
            {
                "publish_status": "Published",
                "active_date_key": {"$in": [today_key, today_iso]},
            }
        )
    except Exception:
        pass

    # Top collections by storage when collStats works
    names = [
        "products",
        "upload_items",
        "uploads",
        "order_requests",
        "order_headers",
        "order_items",
        "stock_verification_history",
        "analytics_stock_daily_snapshots",
        "batch_summaries",
        "archive_manifests",
        "notice_board",
        "queries",
        "storage_usage_daily",
    ]
    coll_rows = []
    for name in names:
        try:
            st = await db.command("collStats", name)
            coll_rows.append(
                {
                    "collection": name,
                    "count": int(st.get("count") or 0),
                    "size": int(st.get("size") or 0),
                    "storage_size": int(st.get("storageSize") or 0),
                    "total_index_size": int(st.get("totalIndexSize") or 0),
                }
            )
            if name == "products":
                metrics["product_size"] = int(st.get("storageSize") or st.get("size") or 0)
        except Exception:
            try:
                cnt = await db[name].count_documents({})
                coll_rows.append(
                    {
                        "collection": name,
                        "count": int(cnt),
                        "size": None,
                        "storage_size": None,
                        "total_index_size": None,
                    }
                )
            except Exception:
                continue
    coll_rows.sort(key=lambda r: int(r.get("storage_size") or r.get("size") or 0), reverse=True)
    metrics["collections"] = coll_rows
    return metrics


async def s3_storage_metrics(db) -> Dict[str, Any]:
    storage = get_storage()
    status = storage.status()
    manifests = await db.archive_manifests.find(
        {"status": {"$in": [am.STATUS_VERIFIED, am.STATUS_PRUNED, am.STATUS_UPLOADED]}},
        {"_id": 0, "file_size": 1, "status": 1, "module": 1},
    ).to_list(10000)
    total_stored = sum(int(m.get("file_size") or 0) for m in manifests)
    return {
        "storage_backend": status.get("storage_backend"),
        "real_s3": status.get("real_s3"),
        "warning": status.get("warning"),
        "s3_total_stored_bytes": total_stored,
        "s3_total_stored_gb": round(total_stored / (1024 ** 3), 6),
        "archive_object_count": len(manifests),
        "env": status.get("env"),
        "bucket_configured": status.get("bucket_configured"),
        "note": "S3 is elastic — no fixed capacity/balance. Showing stored volume from manifests.",
    }


async def archive_status_summary(db) -> Dict[str, Any]:
    recent = await db.archive_manifests.find({}, {"_id": 0}).sort("created_at", -1).to_list(50)
    failed = await db.archive_manifests.count_documents({"status": am.STATUS_FAILED})
    verified = await db.archive_manifests.find(
        {"module": ha.MODULE_PRODUCT_HISTORY, "status": {"$in": [am.STATUS_VERIFIED, am.STATUS_PRUNED]}},
        {"_id": 0, "archive_date": 1, "status": 1, "verified_at": 1},
    ).sort("archive_date", -1).to_list(500)
    last_ok = verified[0] if verified else None
    return {
        "recent_jobs": [
            {
                "archive_id": m.get("archive_id"),
                "date": m.get("archive_date") or m.get("archive_month"),
                "module": m.get("module"),
                "records": m.get("record_count"),
                "archive_size": m.get("file_size"),
                "status": m.get("status"),
                "started": m.get("created_at"),
                "verified": m.get("verified_at"),
                "pruned": m.get("pruned_at"),
                "dealer_count": m.get("dealer_count"),
                "branch_count": m.get("branch_count"),
                "brand_count": m.get("brand_count"),
                "error": m.get("error"),
                "eligible_for_prune": m.get("eligible_for_prune"),
                "storage_backend": m.get("storage_backend"),
            }
            for m in recent
        ],
        "failed_archive_count": int(failed or 0),
        "last_successful_archive_date": (last_ok or {}).get("archive_date"),
        "last_archive_status": (recent[0].get("status") if recent else None),
    }


async def migration_space_report(db) -> Dict[str, Any]:
    """Report historical candidates BEFORE any prune."""
    storage = get_storage()
    mongo = await mongo_storage_metrics(db)
    today_key, today_iso = _today_keys()
    hist = await ha.list_historical_product_dates(db)
    candidate_records = sum(int(h.get("count") or 0) for h in hist)

    upload_items = 0
    uploads = 0
    try:
        upload_items = await db.upload_items.count_documents({})
        uploads = await db.uploads.count_documents({})
    except Exception:
        pass

    # Rough recoverable estimate from products collection share of historical rows
    product_size = int(mongo.get("product_size") or 0)
    product_count = int(mongo.get("product_count") or 0) or 1
    today_count = int(mongo.get("today_product_count") or 0)
    hist_count = max(0, product_count - today_count)
    est_recoverable = int(product_size * (hist_count / product_count)) if product_size else None

    verified_dates = await db.archive_manifests.find(
        {"module": ha.MODULE_PRODUCT_HISTORY, "status": {"$in": [am.STATUS_VERIFIED, am.STATUS_PRUNED]}},
        {"_id": 0, "archive_date": 1, "record_count": 1, "sha256": 1, "file_size": 1, "status": 1},
    ).to_list(5000)

    return {
        "storage_backend": storage.status().get("storage_backend"),
        "real_s3": storage.is_s3(),
        "archive_prune_enabled": storage.status().get("archive_prune_enabled"),
        "product_mongo_hot_days": product_mongo_hot_days(),
        "mongo": {
            "data_size": mongo.get("data_size"),
            "storage_size": mongo.get("storage_size"),
            "index_size": mongo.get("index_size"),
            "product_count": mongo.get("product_count"),
            "product_storage_size": mongo.get("product_size"),
            "today_product_count": today_count,
            "top_collections": mongo.get("collections", [])[:10],
        },
        "upload_items_count": upload_items,
        "uploads_count": uploads,
        "historical_dates": hist,
        "historical_date_count": len(hist),
        "archive_candidate_records": candidate_records,
        "estimated_recoverable_bytes": est_recoverable,
        "verified_archives": verified_dates,
        "verified_archive_count": len(verified_dates),
        "prune_blocked_reason": (
            None
            if (storage.is_s3() and storage.status().get("archive_prune_enabled"))
            else (
                "Cloud archive not active — MongoDB pruning disabled."
                if not storage.is_s3()
                else "ARCHIVE_PRUNE_ENABLED=false"
            )
        ),
        "warning": (
            "STOP BEFORE PRUNE: real S3 is not configured. Archives may use local fallback for tests only."
            if not storage.is_s3()
            else None
        ),
    }


async def monitor_dashboard(db, *, month: Optional[str] = None) -> Dict[str, Any]:
    storage = get_storage()
    status = storage.status()
    mongo = await mongo_storage_metrics(db)
    s3m = await s3_storage_metrics(db)
    archives = await archive_status_summary(db)
    usage = await su.month_usage_totals(db, month)
    dealers = await su.dealer_usage_ranking(db, month=month)
    today_key, _ = _today_keys()
    # Additive external console/billing cards (same pattern for AWS + MongoDB).
    try:
        import archive_cleanup as ac

        external = ac.external_console_links()
    except Exception:
        external = {"aws": {}, "mongodb": {}, "pattern": "identical_cards"}

    refreshed = datetime.now(timezone.utc).isoformat()
    aws_card = {
        **(external.get("aws") or {}),
        "status": status.get("storage_backend"),
        "real_s3": status.get("real_s3"),
        "usage_bytes": s3m.get("s3_total_stored_bytes"),
        "usage_label": "S3 archived bytes (from manifests)",
        "estimated_cost": usage.get("estimated_total_cost"),
        "estimated_cost_label": "Estimated Cost (NMTS model, not AWS invoice)",
        "billing_available": False,
        "billing_message": (external.get("aws") or {}).get("billing_message") or "Billing data unavailable",
        "last_refreshed": refreshed,
    }
    mongo_card = {
        **(external.get("mongodb") or {}),
        "status": "CONNECTED" if mongo.get("data_size") is not None else "UNKNOWN",
        "usage_bytes": mongo.get("storage_size") or mongo.get("data_size"),
        "usage_label": "MongoDB storage size (dbStats)",
        "data_size": mongo.get("data_size"),
        "index_size": mongo.get("index_size"),
        "estimated_cost": None,
        "estimated_cost_label": "Billing data unavailable",
        "billing_available": False,
        "billing_message": (external.get("mongodb") or {}).get("billing_message") or "Billing data unavailable",
        "last_refreshed": refreshed,
    }

    return {
        "storage_backend": status.get("storage_backend"),
        "real_s3": status.get("real_s3"),
        "warning": status.get("warning"),
        "cards": {
            "mongodb_used_storage": mongo.get("storage_size") or mongo.get("data_size"),
            "mongodb_data_size": mongo.get("data_size"),
            "mongodb_index_size": mongo.get("index_size"),
            "mongodb_allocated_note": "Atlas plan capacity is not exposed via current APIs — showing measured dbStats only.",
            "s3_total_stored": s3m.get("s3_total_stored_bytes"),
            "estimated_current_month_cost": usage.get("estimated_total_cost"),
            "cost_label": "Estimated Cost",
            "today_product_count": mongo.get("today_product_count"),
            "today_product_size_note": "Derived from today product row count; byte size uses collection share when available.",
            "historical_moved_to_s3_bytes": s3m.get("s3_total_stored_bytes"),
            "last_archive_status": archives.get("last_archive_status"),
            "last_successful_archive_date": archives.get("last_successful_archive_date"),
            "failed_archive_count": archives.get("failed_archive_count"),
        },
        "mongo": mongo,
        "s3": s3m,
        "usage_month": usage,
        "dealer_ranking": dealers,
        "archives": archives,
        "product_mongo_hot_days": product_mongo_hot_days(),
        "pricing": status.get("pricing"),
        "today_date_key": today_key,
        "external_services": {
            "pattern": "identical_cards",
            "aws": aws_card,
            "mongodb": mongo_card,
        },
        "archive_schedule": {
            "timezone": "Asia/Kolkata",
            "daily_product_history": "00:15 IST (previous calendar day)",
            "monthly_orders_requests": "01:30 IST on the 1st (previous calendar month)",
            "unchanged": True,
        },
    }
