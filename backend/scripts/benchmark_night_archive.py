#!/usr/bin/env python3
"""Benchmark coordinated nightly archive stages (planning baseline).

Planning baseline:
  100 branches × 25,000 parts/day ≈ 2.5M product rows/day
Target: p95 full coordinated archive cycle < 3.5 hours (within 5h window).

This script measures stage timings on synthetic in-memory rows + optional REAL S3
when credentials are present. It does NOT claim production capacity without
measured results — print the numbers.
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import io
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def _ms(start: float) -> float:
    return (time.perf_counter() - start) * 1000.0


def gen_rows(n: int, branches: int = 100):
    for i in range(n):
        b = i % branches
        yield {
            "part_number": f"PN{i:07d}",
            "brand_name": "BenchmarkBrand",
            "dealer_name": f"Dealer{b // 10}",
            "branch": f"Branch{b:03d}",
            "available_qty_number": float(i % 50),
            "unit_value_number": 10.0,
            "total_value_number": float((i % 50) * 10),
            "part_category": "General",
            "purchase_aging_days": i % 400,
            "sales_aging_days": i % 200,
            "active_date_key": "20260813",
        }


def stage_transform_and_gzip(rows, label: str):
    t0 = time.perf_counter()
    buf = io.BytesIO()
    count = 0
    with gzip.GzipFile(fileobj=buf, mode="wb", compresslevel=6) as gz:
        for r in rows:
            gz.write((json.dumps(r, separators=(",", ":")) + "\n").encode("utf-8"))
            count += 1
    data = buf.getvalue()
    transform_ms = _ms(t0)
    t1 = time.perf_counter()
    sha = hashlib.sha256(data).hexdigest()
    sha_ms = _ms(t1)
    return {
        "label": label,
        "record_count": count,
        "bytes": len(data),
        "transform_gzip_ms": round(transform_ms, 2),
        "sha256_ms": round(sha_ms, 2),
        "sha256": sha,
        "data": data,
    }


def maybe_s3_roundtrip(payload: dict) -> dict:
    try:
        from s3_storage import get_storage

        storage = get_storage()
        if not storage.is_s3():
            return {"s3": "skipped_not_real_s3"}
        key = f"benchmark/night-archive/{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')}.jsonl.gz"
        t0 = time.perf_counter()
        storage.upload_bytes(key, payload["data"], content_type="application/gzip")
        upload_ms = _ms(t0)
        t1 = time.perf_counter()
        head = storage.head(key)
        head_ms = _ms(t1)
        t2 = time.perf_counter()
        data, _ = storage.download_bytes(key)
        get_ms = _ms(t2)
        t3 = time.perf_counter()
        sha = hashlib.sha256(data).hexdigest()
        verify_ms = _ms(t3)
        count_ok = sha == payload["sha256"] and len(data) == payload["bytes"]
        try:
            storage.delete_object(key)
        except Exception:
            pass
        return {
            "s3": "ok",
            "upload_ms": round(upload_ms, 2),
            "head_ms": round(head_ms, 2),
            "get_ms": round(get_ms, 2),
            "sha_verify_ms": round(verify_ms, 2),
            "count_sha_ok": count_ok,
            "head_size": (head or {}).get("content_length"),
        }
    except Exception as exc:
        return {"s3": "error", "error": str(exc)[:300]}


def extrapolate(sample_n: int, sample_total_ms: float, target_n: int = 2_500_000) -> dict:
    if sample_n <= 0:
        return {}
    factor = target_n / sample_n
    est_ms = sample_total_ms * factor
    return {
        "target_rows": target_n,
        "scale_factor": round(factor, 2),
        "estimated_ms": round(est_ms, 2),
        "estimated_hours": round(est_ms / 3_600_000.0, 3),
        "target_hours": 3.5,
        "within_target_estimate": (est_ms / 3_600_000.0) < 3.5,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--rows", type=int, default=25000, help="Synthetic rows for sample (default 25k = 1 branch day)")
    parser.add_argument("--branches", type=int, default=100)
    parser.add_argument("--concurrency-note", action="store_true")
    args = parser.parse_args()

    print("=== Night Archive Benchmark ===")
    print(f"sample_rows={args.rows} branches={args.branches}")
    print("baseline_target=100 branches x 25k parts ≈ 2.5M rows/day; p95 < 3.5h")

    t_mongo = time.perf_counter()
    rows = list(gen_rows(args.rows, branches=args.branches))
    mongo_read_ms = _ms(t_mongo)  # synthetic stand-in for Mongo cursor materialization

    payload = stage_transform_and_gzip(rows, "product-history")
    s3 = maybe_s3_roundtrip(payload)

    # Companion stages (orders/requests/uploads) — smaller relative size; measure lightweight transforms
    orders = stage_transform_and_gzip(
        [{"order_number": f"O{i}", "status": "Completed"} for i in range(max(100, args.rows // 250))],
        "orders",
    )
    requests = stage_transform_and_gzip(
        [{"request_number": f"R{i}", "status": "Approved"} for i in range(max(100, args.rows // 200))],
        "requests",
    )
    uploads = stage_transform_and_gzip(
        [{"upload_id": f"U{i}", "filename": "stock.xlsx"} for i in range(max(10, args.branches))],
        "uploads",
    )

    product_stage_ms = (
        mongo_read_ms
        + payload["transform_gzip_ms"]
        + payload["sha256_ms"]
        + float(s3.get("upload_ms") or 0)
        + float(s3.get("head_ms") or 0)
        + float(s3.get("get_ms") or 0)
        + float(s3.get("sha_verify_ms") or 0)
    )
    report = {
        "measured_at": datetime.now(timezone.utc).isoformat(),
        "job_lock_ttl_seconds": 5 * 60 * 60,
        "stages": {
            "mongodb_read_materialize_ms": round(mongo_read_ms, 2),
            "product_transform_gzip_ms": payload["transform_gzip_ms"],
            "product_sha256_ms": payload["sha256_ms"],
            "product_bytes": payload["bytes"],
            "s3": s3,
            "orders_transform_gzip_ms": orders["transform_gzip_ms"],
            "requests_transform_gzip_ms": requests["transform_gzip_ms"],
            "uploads_transform_gzip_ms": uploads["transform_gzip_ms"],
            "analytics_snapshot_note": "Companion snapshot generation scales with unique brand/dealer/branch/part keys; not fully simulated here.",
        },
        "sample_product_cycle_ms": round(product_stage_ms, 2),
        "extrapolation_2_5m_rows": extrapolate(args.rows, product_stage_ms, 2_500_000),
        "concurrency": {
            "mode": "sequential (default)",
            "safe_module_level": [1, 2, 4],
            "unbounded_per_branch": False,
            "note": "Do not introduce unbounded per-branch concurrency.",
        },
        "disclaimer": (
            "Extrapolation assumes linear scaling of transform/upload/verify. "
            "Real Mongo query shape, S3 throughput, and network variance will differ. "
            "This is a measured planning input, not a capacity guarantee."
        ),
    }
    out = Path("/tmp/cursor/artifacts") if Path("/tmp/cursor").exists() else Path("/tmp")
    out.mkdir(parents=True, exist_ok=True)
    path = out / "night_archive_benchmark.json"
    path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))
    print(f"wrote {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
