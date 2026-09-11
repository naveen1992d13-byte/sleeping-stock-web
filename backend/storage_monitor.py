"""Master-only Storage & Cost Monitor helpers (Mongo metrics + archive status)."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

import archive_manifest as am
import archive_runs as ar
import archive_verify as av
import history_archive as ha
import maintenance as maint
import storage_usage as su
from s3_storage import get_storage, product_mongo_hot_days

logger = logging.getLogger(__name__)
IST = ZoneInfo("Asia/Kolkata")
GIB = 1024 ** 3


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
        "capacity_bytes": None,
        "available_bytes": None,
        "capacity_status": "Unavailable",
        "capacity_reason": (
            "Atlas plan/capacity is not exposed via current dbStats APIs — "
            "Available/Balance cannot be computed reliably without Atlas Admin API credentials."
        ),
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
    # Manifest-recorded size (never labeled as Actual S3 Used Storage alone)
    manifests = await db.archive_manifests.find(
        {
            "status": {"$in": [am.STATUS_VERIFIED, am.STATUS_PRUNED]},
            "storage_backend": {"$in": ["s3", "REAL S3"]},
            "eligible_for_prune": {"$ne": False},
        },
        {"_id": 0, "file_size": 1, "status": 1, "module": 1, "storage_backend": 1},
    ).to_list(10000)
    # Also include PRUNED which may have eligible_for_prune False after prune
    pruned = await db.archive_manifests.find(
        {"status": am.STATUS_PRUNED, "storage_backend": {"$in": ["s3", "REAL S3"]}},
        {"_id": 0, "file_size": 1, "archive_id": 1},
    ).to_list(10000)
    seen = set()
    manifest_bytes = 0
    for m in list(manifests) + list(pruned):
        aid = m.get("archive_id") or id(m)
        if aid in seen:
            continue
        seen.add(aid)
        manifest_bytes += int(m.get("file_size") or 0)

    actual = storage.sum_prefix_bytes(storage.env + "/")
    return {
        "storage_backend": status.get("storage_backend"),
        "real_s3": status.get("real_s3"),
        "warning": status.get("warning"),
        "manifest_recorded_bytes": manifest_bytes,
        "manifest_recorded_gb": round(manifest_bytes / GIB, 6),
        "actual_s3_used_bytes": actual.get("actual_s3_bytes"),
        "actual_s3_used_gb": (
            round(actual["actual_s3_bytes"] / GIB, 6)
            if actual.get("actual_s3_bytes") is not None
            else None
        ),
        "actual_s3_object_count": actual.get("object_count"),
        "actual_s3_available": bool(actual.get("ok")),
        "actual_s3_reason": actual.get("reason"),
        # Backward-compatible field — prefer actual when available
        "s3_total_stored_bytes": actual.get("actual_s3_bytes")
        if actual.get("ok")
        else manifest_bytes,
        "s3_total_stored_gb": (
            round((actual.get("actual_s3_bytes") or 0) / GIB, 6)
            if actual.get("ok")
            else round(manifest_bytes / GIB, 6)
        ),
        "archive_object_count": len(seen) or len(manifests),
        "env": status.get("env"),
        "bucket_configured": status.get("bucket_configured"),
        "note": (
            "Actual S3 Used Storage from live bucket listing."
            if actual.get("ok")
            else "Actual S3 unavailable — showing Manifest Recorded Size separately. "
            "S3 has no fixed capacity/balance in this design."
        ),
        "balance": None,
        "balance_label": "N/A — S3 is elastic (no fixed capacity)",
    }


async def archive_health_summary(db) -> Dict[str, Any]:
    """Overall archive health from REAL head/size checks — not manifest claims alone."""
    rows = await db.archive_manifests.find({}, {"_id": 0}).sort("created_at", -1).to_list(2000)
    total = len(rows)
    verified = pending = failed = verification_failed = safe_to_delete = 0
    enriched = []
    for m in rows:
        live = av.head_s3_status(m)
        display = live.get("display_status") or av.classify_display_status(
            manifest_status=str(m.get("status") or ""),
            live=live,
        )
        if display in {av.DISPLAY_VERIFIED, av.DISPLAY_PRUNED}:
            verified += 1
        elif display == av.DISPLAY_NO_ELIGIBLE:
            # Genuine zero-eligible — not a transfer success and not a failure
            pass
        elif display in {av.DISPLAY_PENDING, av.DISPLAY_RUNNING}:
            pending += 1
        elif display == av.DISPLAY_VERIFICATION_FAILED:
            verification_failed += 1
        else:
            failed += 1
        if (
            display in {av.DISPLAY_VERIFIED, av.DISPLAY_PRUNED}
            and m.get("module") == ha.MODULE_PRODUCT_HISTORY
            and m.get("status") == am.STATUS_VERIFIED
            and m.get("eligible_for_prune")
        ):
            safe_to_delete += 1
        enriched.append(
            {
                "archive_id": m.get("archive_id"),
                "date": m.get("archive_date") or m.get("archive_month"),
                "module": m.get("module"),
                "dataset": m.get("module"),
                "records": m.get("record_count"),
                "archived_record_count": m.get("record_count"),
                "archive_size": m.get("file_size"),
                "status": m.get("status"),
                "display_status": display,
                "failure_reason": None if live.get("ok") else (live.get("reason") or m.get("error")),
                "s3_object_status": "EXISTS" if live.get("object_exists") else "MISSING",
                "s3_readable": live.get("object_readable"),
                "sha256_match": (
                    "MATCH"
                    if live.get("sha256_match")
                    else ("MISMATCH" if live.get("sha256_match") is False else "NOT RECHECKED")
                ),
                "started": m.get("created_at"),
                "transferred_at": m.get("verified_at") or m.get("created_at"),
                "verified": m.get("verified_at"),
                "pruned": m.get("pruned_at"),
                "dealer_count": m.get("dealer_count"),
                "branch_count": m.get("branch_count"),
                "brand_count": m.get("brand_count"),
                "error": m.get("error"),
                "eligible_for_prune": m.get("eligible_for_prune"),
                "storage_backend": m.get("storage_backend"),
                "storage_key": m.get("storage_key"),
                "retryable": display
                in {
                    av.DISPLAY_NOT_TRANSFERRED,
                    av.DISPLAY_VERIFICATION_FAILED,
                    av.DISPLAY_PENDING,
                    av.DISPLAY_RUNNING,
                }
                or m.get("status")
                in {am.STATUS_FAILED, am.STATUS_CREATING, am.STATUS_UPLOADED},
            }
        )
    pct = round((verified / total) * 100, 2) if total else 0.0
    return {
        "total_archive_datasets": total,
        "transferred_and_verified": verified,
        "pending": pending,
        "failed": failed,
        "verification_failed": verification_failed,
        "safe_to_delete": safe_to_delete,
        "overall_verified_percent": pct,
        "recent_jobs": enriched[:50],
        "failed_archive_count": failed + verification_failed,
        "last_successful_archive_date": next(
            (j["date"] for j in enriched if j["display_status"] in {av.DISPLAY_VERIFIED, av.DISPLAY_PRUNED}),
            None,
        ),
        "last_archive_status": (enriched[0]["display_status"] if enriched else None),
    }


async def archive_status_summary(db) -> Dict[str, Any]:
    return await archive_health_summary(db)


async def _dealer_product_shares(db) -> List[Dict[str, Any]]:
    """Dealer/branch counts without loading full product documents into the app."""
    pipeline = [
        {
            "$group": {
                "_id": {"$ifNull": ["$dealer_name", {"$ifNull": ["$dealer", "(unknown)"]}]},
                "product_rows": {"$sum": 1},
                "branches": {"$addToSet": {"$ifNull": ["$branch", "$branch_name"]}},
            }
        }
    ]
    try:
        raw = await db.products.aggregate(pipeline).to_list(10000)
        out = []
        for doc in raw:
            dname = str(doc.get("_id") or "(unknown)").strip() or "(unknown)"
            branches = sorted(
                str(b).strip() for b in (doc.get("branches") or []) if str(b or "").strip()
            )
            out.append(
                {
                    "dealer": dname,
                    "product_rows": int(doc.get("product_rows") or 0),
                    "branch_names": branches,
                }
            )
        return out
    except Exception:
        rows = await db.products.find(
            {},
            {"_id": 0, "dealer_name": 1, "dealer": 1, "branch": 1, "branch_name": 1},
        ).to_list(500000)
        dealers: Dict[str, Dict[str, Any]] = {}
        for r in rows:
            dname = str(r.get("dealer_name") or r.get("dealer") or "(unknown)").strip() or "(unknown)"
            slot = dealers.setdefault(dname, {"dealer": dname, "product_rows": 0, "branch_names": set()})
            slot["product_rows"] += 1
            br = str(r.get("branch") or r.get("branch_name") or "").strip()
            if br:
                slot["branch_names"].add(br)
        return [
            {
                "dealer": d["dealer"],
                "product_rows": d["product_rows"],
                "branch_names": sorted(d["branch_names"]),
            }
            for d in dealers.values()
        ]


async def dealer_storage_snapshot(db, *, mongo: Optional[Dict[str, Any]] = None, s3m: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Canonical dealer-wise Mongo + S3 usage so top cards and table never drift.

    Mongo per-dealer values are a *logical data-size allocation* from product
    row payload estimates (not physical dbStats disk bytes per dealer — Mongo
    does not expose per-dealer physical storage).
    S3 archive bytes are attributed from verified REAL-S3 manifests' scope_dealers
    (equal split when multiple dealers share one consolidated archive).
    """
    mongo = mongo if mongo is not None else await mongo_storage_metrics(db)
    s3m = s3m if s3m is not None else await s3_storage_metrics(db)

    shares = await _dealer_product_shares(db)
    dealers: Dict[str, Dict[str, Any]] = {}
    product_total = max(1, sum(int(s.get("product_rows") or 0) for s in shares) or 1)
    product_storage = int(mongo.get("product_size") or 0)
    mongo_used_total = int(mongo.get("storage_size") or mongo.get("data_size") or 0)

    for s in shares:
        dname = str(s.get("dealer") or "(unknown)").strip() or "(unknown)"
        dealers[dname] = {
            "dealer": dname,
            "branches": set(s.get("branch_names") or []),
            "product_rows": int(s.get("product_rows") or 0),
            "mongodb_used_bytes": 0,
            "s3_archive_used_bytes": 0,
            "archive_verified": 0,
            "archive_total": 0,
        }

    # Allocate product storage share + residual mongo proportionally by row count
    for slot in dealers.values():
        share = slot["product_rows"] / product_total
        slot["mongodb_used_bytes"] = int(round(product_storage * share)) if product_storage else 0

    # If product_size unknown, allocate full mongo storage_size by row share
    if product_storage <= 0 and mongo_used_total > 0 and dealers:
        for slot in dealers.values():
            share = slot["product_rows"] / product_total
            slot["mongodb_used_bytes"] = int(round(mongo_used_total * share))

    # Verified REAL-S3 archive bytes by dealer scope
    manifests = await db.archive_manifests.find(
        {
            "status": {"$in": [am.STATUS_VERIFIED, am.STATUS_PRUNED]},
            "storage_backend": {"$in": ["s3", "REAL S3"]},
        },
        {"_id": 0},
    ).to_list(10000)
    s3_attributed = 0
    for m in manifests:
        live = av.head_s3_status(m)
        ok = bool(live.get("ok"))
        size = int(m.get("file_size") or 0)
        names = [str(x).strip() for x in (m.get("scope_dealers") or []) if str(x).strip()]
        if not names:
            names = ["(unscoped)"]
        per = size / max(1, len(names))
        for dname in names:
            slot = dealers.setdefault(
                dname,
                {
                    "dealer": dname,
                    "branches": set(),
                    "product_rows": 0,
                    "mongodb_used_bytes": 0,
                    "s3_archive_used_bytes": 0,
                    "archive_verified": 0,
                    "archive_total": 0,
                },
            )
            slot["archive_total"] += 1
            if ok:
                slot["archive_verified"] += 1
                slot["s3_archive_used_bytes"] += int(round(per))
                s3_attributed += int(round(per))
            else:
                # Still count toward archive_total for % but not S3 used
                pass

    # Prefer actual S3 total for top card when available; dealer table uses attribution
    actual_s3 = s3m.get("actual_s3_used_bytes")
    top_s3 = actual_s3 if actual_s3 is not None else s3_attributed

    out_rows = []
    for dname, slot in dealers.items():
        branches = sorted(slot["branches"])
        verified_pct = (
            round((slot["archive_verified"] / slot["archive_total"]) * 100, 2)
            if slot["archive_total"]
            else 0.0
        )
        mongo_b = int(slot["mongodb_used_bytes"])
        s3_b = int(slot["s3_archive_used_bytes"])
        out_rows.append(
            {
                "dealer": dname,
                "branches": len(branches),
                "branch_names": branches[:50],
                "mongodb_used_bytes": mongo_b,
                "mongodb_used_gb": round(mongo_b / GIB, 6),
                "s3_archive_used_bytes": s3_b,
                "s3_archive_used_gb": round(s3_b / GIB, 6),
                "combined_used_bytes": mongo_b + s3_b,
                "combined_used_gb": round((mongo_b + s3_b) / GIB, 6),
                "archive_verified_percent": verified_pct,
                "archive_verified": slot["archive_verified"],
                "archive_total": slot["archive_total"],
                "product_rows": slot["product_rows"],
                "mongodb_allocation_note": (
                    "Logical data-size allocation from products row share "
                    "(not physical per-dealer dbStats bytes)"
                ),
            }
        )
    out_rows.sort(key=lambda r: r["combined_used_bytes"], reverse=True)

    mongo_dealer_sum = sum(r["mongodb_used_bytes"] for r in out_rows)
    s3_dealer_sum = sum(r["s3_archive_used_bytes"] for r in out_rows)
    # Canonical allocated Mongo total — identical source of truth as dealer column sum
    mongodb_allocated_usage_bytes = int(mongo_dealer_sum)

    return {
        "dealers": out_rows,
        "totals": {
            "mongodb_used_bytes": mongo_used_total,
            "mongodb_physical_storage_bytes": mongo_used_total,
            "mongodb_data_size": mongo.get("data_size"),
            "mongodb_index_size": mongo.get("index_size"),
            "mongodb_dealer_allocated_bytes": mongodb_allocated_usage_bytes,
            "mongodb_allocated_usage_bytes": mongodb_allocated_usage_bytes,
            "mongodb_capacity_bytes": None,
            "mongodb_available_bytes": None,
            "mongodb_capacity_status": "Unavailable",
            "mongodb_capacity_reason": mongo.get("capacity_reason"),
            "s3_actual_used_bytes": actual_s3,
            "s3_manifest_recorded_bytes": s3m.get("manifest_recorded_bytes"),
            "s3_dealer_attributed_bytes": s3_dealer_sum,
            "s3_verified_archive_usage_bytes": s3_dealer_sum,
            "s3_top_card_bytes": top_s3,
            "combined_top_bytes": int(mongo_used_total or 0)
            + int(top_s3 or 0),
            "reconciliation": {
                # Exact: Allocated Mongo Usage card == sum(dealer allocated Mongo bytes)
                "allocated_mongo_equals_dealer_sum": mongodb_allocated_usage_bytes
                == mongo_dealer_sum,
                "allocated_mongo_bytes": mongodb_allocated_usage_bytes,
                "dealer_allocated_sum_bytes": mongo_dealer_sum,
                "mongo_physical_equals_dealer_sum": False,
                "mongo_top_equals_dealer_sum": False,  # physical ≠ allocated by design
                "mongo_note": (
                    "Allocated Mongo Usage = sum of dealer logical products-share allocation "
                    "(exact byte match). Physical MongoDB Storage = dbStats storageSize — "
                    "a different metric; do not force them equal."
                ),
                "s3_dealer_sum_equals_attributed": s3_dealer_sum == s3_attributed,
                "s3_actual_equals_attributed": (
                    actual_s3 is not None and int(actual_s3) == int(s3_dealer_sum)
                ),
                "s3_note": (
                    "Actual S3 Used is live bucket listing. "
                    "Dealer-attributed Verified Archive Usage is manifest scope attribution. "
                    "They are related but not necessarily identical."
                ),
            },
        },
        "mongodb_allocation_note": (
            "Allocated Mongo Usage is the canonical logical data-size allocation "
            "(same bytes as the dealer table). Physical MongoDB Storage is separate dbStats."
        ),
    }


async def migration_space_report(db, mongo: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Report historical candidates BEFORE any prune."""
    storage = get_storage()
    mongo = mongo if mongo is not None else await mongo_storage_metrics(db)
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

    product_size = int(mongo.get("product_size") or 0)
    product_count = int(mongo.get("product_count") or 0) or 1
    today_count = int(mongo.get("today_product_count") or 0)
    hist_count = max(0, product_count - today_count)
    est_recoverable = int(product_size * (hist_count / product_count)) if product_size else None

    verified_dates = await db.archive_manifests.find(
        {
            "module": ha.MODULE_PRODUCT_HISTORY,
            "status": {"$in": [am.STATUS_VERIFIED, am.STATUS_PRUNED]},
            "storage_backend": {"$in": ["s3", "REAL S3"]},
        },
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
    token = av.begin_head_cache()
    try:
        mongo = await mongo_storage_metrics(db)
        s3m = await s3_storage_metrics(db)
        archives = await archive_health_summary(db)
        usage = await su.month_usage_totals(db, month)
        snapshot = await dealer_storage_snapshot(db, mongo=mongo, s3m=s3m)
        cost_ranking = await su.dealer_usage_ranking(db, month=month)
        try:
            import archive_cleanup as ac

            external = ac.external_console_links()
            cleanup_rows = await ac.list_cleanup_archives(db, years=3)
        except Exception:
            external = {"aws": {}, "mongodb": {}, "pattern": "identical_cards"}
            cleanup_rows = []
        migration = await migration_space_report(db, mongo=mongo)
    finally:
        av.end_head_cache(token)

    today_key, _ = _today_keys()

    refreshed = datetime.now(timezone.utc).isoformat()
    totals = snapshot.get("totals") or {}
    aws_card = {
        **(external.get("aws") or {}),
        "status": status.get("storage_backend"),
        "real_s3": status.get("real_s3"),
        "usage_bytes": s3m.get("actual_s3_used_bytes"),
        "usage_label": (
            "Actual S3 Used Storage"
            if s3m.get("actual_s3_available")
            else "Actual S3 Used Storage (Unavailable)"
        ),
        "manifest_recorded_bytes": s3m.get("manifest_recorded_bytes"),
        "estimated_cost": usage.get("estimated_total_cost"),
        "estimated_cost_label": "Estimated Cost (NMTS model, not AWS invoice)",
        "billing_available": False,
        "billing_message": (external.get("aws") or {}).get("billing_message") or "Billing data unavailable",
        "last_refreshed": refreshed,
        "balance": None,
        "balance_label": s3m.get("balance_label"),
    }
    mongo_card = {
        **(external.get("mongodb") or {}),
        "status": "CONNECTED" if mongo.get("data_size") is not None else "UNKNOWN",
        "usage_bytes": mongo.get("storage_size") or mongo.get("data_size"),
        "usage_label": "Physical MongoDB Storage (dbStats)",
        "allocated_usage_bytes": totals.get("mongodb_allocated_usage_bytes"),
        "allocated_usage_label": "Allocated Mongo Usage",
        "data_size": mongo.get("data_size"),
        "index_size": mongo.get("index_size"),
        "capacity": "Unavailable",
        "available": "Unavailable",
        "capacity_reason": mongo.get("capacity_reason"),
        "estimated_cost": None,
        "estimated_cost_label": "Billing data unavailable",
        "billing_available": False,
        "billing_message": (external.get("mongodb") or {}).get("billing_message") or "Billing data unavailable",
        "last_refreshed": refreshed,
    }

    return {
        "storage_backend": status.get("storage_backend"),
        "real_s3": status.get("real_s3"),
        "archive_prune_enabled": bool(status.get("archive_prune_enabled")),
        "warning": status.get("warning"),
        "cards": {
            "mongodb_allocated_usage": totals.get("mongodb_allocated_usage_bytes"),
            "mongodb_used_storage": mongo.get("storage_size") or mongo.get("data_size"),
            "mongodb_physical_storage": mongo.get("storage_size") or mongo.get("data_size"),
            "mongodb_data_size": mongo.get("data_size"),
            "mongodb_index_size": mongo.get("index_size"),
            "mongodb_capacity": "Unavailable",
            "mongodb_available": "Unavailable",
            "mongodb_capacity_reason": mongo.get("capacity_reason"),
            "mongodb_allocated_note": (
                "Allocated Mongo Usage matches dealer-table allocated bytes exactly. "
                "Physical MongoDB Storage is separate dbStats."
            ),
            "s3_actual_used": s3m.get("actual_s3_used_bytes"),
            "s3_dealer_attributed": totals.get("s3_dealer_attributed_bytes"),
            "s3_manifest_recorded": s3m.get("manifest_recorded_bytes"),
            "s3_total_stored": s3m.get("actual_s3_used_bytes")
            if s3m.get("actual_s3_available")
            else None,
            "s3_balance": None,
            "estimated_current_month_cost": usage.get("estimated_total_cost"),
            "cost_label": "Estimated Cost",
            "today_product_count": mongo.get("today_product_count"),
            "today_product_size_note": "Derived from today product row count; byte size uses collection share when available.",
            "historical_moved_to_s3_bytes": s3m.get("actual_s3_used_bytes")
            if s3m.get("actual_s3_available")
            else s3m.get("manifest_recorded_bytes"),
            "last_archive_status": archives.get("last_archive_status"),
            "last_successful_archive_date": archives.get("last_successful_archive_date"),
            "failed_archive_count": archives.get("failed_archive_count"),
        },
        "archive_health": {
            "total_archive_datasets": archives.get("total_archive_datasets"),
            "transferred_and_verified": archives.get("transferred_and_verified"),
            "pending": archives.get("pending"),
            "failed": archives.get("failed"),
            "verification_failed": archives.get("verification_failed"),
            "safe_to_delete": 0 if not status.get("archive_prune_enabled") else archives.get("safe_to_delete"),
            "cleanup_status": "DISABLED" if not status.get("archive_prune_enabled") else "ENABLED",
            "overall_verified_percent": archives.get("overall_verified_percent"),
        },
        "mongo": mongo,
        "s3": s3m,
        "usage_month": usage,
        "dealer_ranking": cost_ranking,
        "dealer_storage": snapshot.get("dealers") or [],
        "storage_totals": totals,
        "mongodb_allocation_note": snapshot.get("mongodb_allocation_note"),
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
            "daily_coordinated_batch": "23:00–04:00 IST maintenance window (same business day — uploads, product-history, orders, requests)",
            "daily_product_history": "23:00 IST start; frozen same-business-day archive_date (part of coordinated batch)",
            "monthly_orders_requests": "01:30 IST on the 1st (previous calendar month safety-net)",
            "unchanged": False,
        },
        "maintenance": maint.maintenance_status(),
        "tonight_archive": ar.tonight_card(
            await ar.latest_run(db),
            maintenance_active=maint.in_maintenance_window(),
        ),
        "cleanup_rows": cleanup_rows,
        "migration": migration,
        "filters_note": (
            "Storage & Data Cleanup always shows overall health. "
            "Global Brand/Dealer/Branch header filters do not apply on this page."
        ),
    }
