"""Phase 4: incremental rollup only — no historical multi-date backfill."""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import analytics_rollup as ar


def test_backfill_refuses_historical_multi_date():
    result = asyncio.run(ar.backfill_analytics_rollups(None, ["2026-10-01", "2026-10-02", "2026-10-03"]))
    assert result["error"] == "historical_backfill_disabled"
    assert result["success"] == 0
    assert result["requested"] == 3


def test_status_unavailable_constant():
    assert ar.STATUS_UNAVAILABLE == "unavailable"
    assert ar.MAX_INCREMENTAL_DATES == 1
    assert ar.MAX_SOURCE_ROWS_PER_DATE <= 25000
