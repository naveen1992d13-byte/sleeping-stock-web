# Analytics rollup rollback (Testing only)

Collections involved (Testing local-only via overlay — never Production):

- `analytics_stock_daily_snapshots` — part-level finalized rows for one IST date
- `analytics_rollup_status` — per-date finalize status / provenance / reconcile totals
- `analytics_daily_aggs` — pre-aggregated brand/dealer/branch/category daily metrics (screen path)

Backups for this gate live under `~/testing-backups/YYYY-MM-DD-analytics-rollup/`.

## Exact rollback — `analytics_stock_daily_snapshots`

Restore one date (or all dates) from the BSON/JSON backup taken before a force finalize:

```bash
# Example: restore snapshots for 2026-09-19 from backup dump
BACKUP=~/testing-backups/2026-10-17-analytics-rollup
# Prefer mongorestore when a dump/ exists:
mongorestore --uri="$TESTING_MONGO_URI" \
  --db nmts_testing \
  --collection analytics_stock_daily_snapshots \
  --drop \
  "$BACKUP/dump/nmts_testing/analytics_stock_daily_snapshots.bson"

# Or delete a bad date then re-insert from JSON export:
# mongo nmts_testing --eval 'db.analytics_stock_daily_snapshots.deleteMany({snapshot_date_ist:"2026-09-19"})'
# then insert documents from $BACKUP/analytics_stock_daily_snapshots-2026-09-19.json
```

Delete only (no restore) for a single bad date:

```javascript
db.analytics_stock_daily_snapshots.deleteMany({ snapshot_date_ist: "YYYY-MM-DD" })
```

## Exact rollback — `analytics_rollup_status`

```bash
mongorestore --uri="$TESTING_MONGO_URI" \
  --db nmts_testing \
  --collection analytics_rollup_status \
  --drop \
  "$BACKUP/dump/nmts_testing/analytics_rollup_status.bson"
```

Or per-date:

```javascript
// Remove status so the night job / Master finalize will rebuild
db.analytics_rollup_status.deleteOne({ rollup_date_ist: "YYYY-MM-DD" })

// Or restore a previous status document from backup JSON
db.analytics_rollup_status.replaceOne(
  { rollup_date_ist: "YYYY-MM-DD" },
  /* <document from backup> */,
  { upsert: true }
)
```

## Companion collection — `analytics_daily_aggs`

Screen reads use this collection. Always roll it back together with snapshots/status for the same dates:

```bash
mongorestore --uri="$TESTING_MONGO_URI" \
  --db nmts_testing \
  --collection analytics_daily_aggs \
  --drop \
  "$BACKUP/dump/nmts_testing/analytics_daily_aggs.bson"
```

Per-date delete (forces empty screen / No Data until re-finalize):

```javascript
db.analytics_daily_aggs.deleteMany({ rollup_date_ist: "YYYY-MM-DD" })
db.analytics_stock_daily_snapshots.deleteMany({ snapshot_date_ist: "YYYY-MM-DD" })
db.analytics_rollup_status.deleteOne({ rollup_date_ist: "YYYY-MM-DD" })
```

Then rebuild from that date’s archive (preferred) via Master:

`POST /api/analytics/rollup/finalize?date=YYYY-MM-DD&force=true`

## Safety

- These three collections are `LOCAL_ONLY` in `testing_overlay.py` — Production Mongo is never written.
- Do not run rollback against Production or either `main` branch deploy.
- After restore, hit stock-trend / aging-trend once and confirm `data_updated_up_to` and reconcile totals.
