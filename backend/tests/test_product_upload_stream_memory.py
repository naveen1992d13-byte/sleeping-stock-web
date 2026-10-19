"""Memory harness for bounded-memory Product Hub Excel streaming."""
from __future__ import annotations

import gc
import os
import resource
import sys
from pathlib import Path

import pytest

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from product_upload_stream import (  # noqa: E402
    UPLOAD_V2_BATCH_SIZE,
    build_synthetic_inventory_xlsx,
    open_inventory_worksheet,
    stream_product_items,
)


def _rss_bytes() -> int:
    # ru_maxrss is kilobytes on Linux.
    return int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss) * 1024


def test_open_inventory_uses_read_only():
    raw, rows = build_synthetic_inventory_xlsx(min_bytes=64 * 1024, start_rows=200)
    wb, ws = open_inventory_worksheet(raw)
    assert ws.title.lower() == "inventory"
    assert getattr(wb, "read_only", False) is True
    # Smoke: stream a few rows
    n = 0
    for row in ws.iter_rows(min_row=2, values_only=True):
        if row and any(row):
            n += 1
        if n >= 5:
            break
    wb.close()
    assert rows >= 200
    assert n == 5


def test_stream_batches_and_20mb_peak_rss_under_250mb():
    """Synthetic file >= 20 MB (≈2× Vanagaram); peak RSS of stream path < 250 MB."""
    target = 20 * 1024 * 1024
    raw, nrows = build_synthetic_inventory_xlsx(min_bytes=target, start_rows=45000)
    assert len(raw) >= target
    assert nrows >= 1000

    gc.collect()
    baseline = _rss_bytes()

    imported = 0
    batches = 0
    max_batch = 0
    for batch, stats in stream_product_items(
        raw,
        upload_id="mem-harness",
        upload_no="TS-MEM",
        now_iso="2026-10-19T00:00:00+05:30",
        active_date_key="2026-10-19",
        context={"brand": "Hyundai", "dealer_name": "FPL", "branch": "Vanagaram"},
        format_excel_date=lambda v: str(v or ""),
        normalize_part_category=lambda v: v or "",
        stamp=lambda d: d,
        batch_size=UPLOAD_V2_BATCH_SIZE,
    ):
        batches += 1
        max_batch = max(max_batch, len(batch))
        imported += int(stats.get("rows_imported") or 0)
        del batch

    gc.collect()
    peak = _rss_bytes()
    # Report for operators (pytest -s shows this).
    print(
        f"MEM_HARNESS file_bytes={len(raw)} rows={nrows} imported={imported} "
        f"batches={batches} max_batch={max_batch} baseline_rss={baseline} peak_rss={peak} "
        f"peak_mb={peak / (1024 * 1024):.1f}"
    )
    assert imported == nrows
    assert max_batch <= UPLOAD_V2_BATCH_SIZE
    assert batches >= max(1, nrows // UPLOAD_V2_BATCH_SIZE)
    assert peak < 250 * 1024 * 1024, f"peak RSS {peak} exceeded 250 MB budget"


def test_idempotent_part_keys_stable_across_stream():
    raw, _ = build_synthetic_inventory_xlsx(min_bytes=8 * 1024, start_rows=50)
    seen = set()
    for batch, _stats in stream_product_items(
        raw,
        upload_id="idemp",
        upload_no="TS-ID",
        now_iso="2026-10-19T00:00:00+05:30",
        active_date_key="2026-10-19",
        context={"branch": "Vanagaram"},
        format_excel_date=lambda v: str(v or ""),
        normalize_part_category=lambda v: v or "",
        stamp=lambda d: d,
        batch_size=10,
    ):
        for doc in batch:
            key = (doc["part_number"], doc["branch"], doc["active_date_key"])
            assert key not in seen
            seen.add(key)
    assert len(seen) >= 50
