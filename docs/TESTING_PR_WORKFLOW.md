# Testing PR workflow and isolated testing data

This guide is for NAVEEN. Production (`sleepingstock.in`, `api.sleepingstock.in`,
port 8000, DocumentDB `nmts`, S3 `dev/`) is never modified by these steps.

## What is already isolated

| | Production | Testing |
|---|---|---|
| Website | https://sleepingstock.in | https://testing.sleepingstock.in |
| API | https://api.sleepingstock.in | https://testing.sleepingstock.in/api |
| Backend port | 8000 | 8001 |
| Service | production uvicorn | `nmts-backend-testing` |
| Database | `nmts` | `nmts_testing` |
| S3 prefix | `dev/` | `testing/` |
| Checkout | `/home/ec2-user/sleeping-stock-web` | `/opt/nmts-testing` |
| JWT secret | production secret | separate testing secret |
| Email / WhatsApp | live recipients | test mode |
| Archive scheduler | production setting | disabled |

`APP_ENV=testing` is the primary environment control. Hostname checks are only
an extra UI safeguard. Production UI, roles, and permissions are unchanged.

## How Cursor creates or updates a PR

1. Cursor works on a branch named `cursor/<topic>-c95b`.
2. The branch is pushed to GitHub and a pull request is opened against `main`.
3. **Do not merge** that PR yet.
4. Fixes stay on the same branch / same PR. Redeploy testing after each push.

Product Hub single-Excel export is already on `main` via PR **#68**. Remaining
testing isolation, snapshot refresh, TS- identifiers, and this deploy workflow
live on the consolidation branch (not the old `cursor/testing-int-pr68-c95b`
integration branch). **Never merge** `cursor/testing-int-pr68-c95b` into
`main`; it was a temporary testing-only stack used to prove #68.

## How you select a PR in GitHub Actions

1. Open the repository on GitHub → **Actions**.
2. Choose workflow **Deploy PR to Testing**.
3. Click **Run workflow**.
4. Enter the PR number of the branch you want on testing.sleepingstock.in
   (not `main`, and never a merged production deploy).
5. Run it. The workflow does not start when a PR is opened or updated.

Required GitHub Actions secrets (never put these in the repo):

- `TESTING_SSH_HOST` — testing/EC2 host
- `TESTING_SSH_USER` — `ec2-user`
- `TESTING_SSH_KEY` — deploy-only SSH private key
- `TESTING_SSH_PORT` — optional, default 22
- `TESTING_SSH_KNOWN_HOSTS` — optional but recommended host key line

The workflow resolves the PR's exact head branch and full commit SHA, SSHes to
the host, and runs `deploy-pr.sh`. It never merges the PR and never restarts
port 8000.

Until those secrets exist, deploy on the host:

```bash
/usr/local/bin/deploy-pr.sh --pr <number>
# or, once scripts are installed from this branch:
/opt/nmts-testing/deploy/testing/deploy-pr.sh --pr <number> --sha <40-char-sha> --branch <name>
```

## How you deploy it to testing

The selected PR replaces whatever is currently on testing. Redeploying the same
PR deploys its latest commit.

Confirm after deploy:

```bash
curl -sS https://testing.sleepingstock.in/deployment.json
sudo systemctl is-active nmts-backend-testing
ss -lptn 'sport = :8000 or sport = :8001'
```

Port 8000's PID must be unchanged. Only `nmts-backend-testing` on 8001 restarts.

## How you confirm the banner

Open https://testing.sleepingstock.in/login.

The amber bar must show **TESTING ENVIRONMENT**, the PR number, git branch,
short SHA, and deploy time. Those values must match `deployment.json` and the
PR head commit. https://sleepingstock.in must not show the banner.

## How you refresh current testing snapshot data

Copies current Brand/Dealer/Branch/State/Group masters, current published
Product Hub rows, matching `batch_summaries`, and templates from `nmts` into
`nmts_testing`. It does **not** copy users, password hashes, sessions, tokens,
JWT secrets, counters, email/WhatsApp recipients, orders, requests, uploads,
or S3 `dev/` archives.

```bash
/opt/nmts-testing/deploy/testing/refresh-testing-snapshot.sh
# optional:
# --dry-run
# --rollback
# --reset-testing-created
```

Safe refresh method (DocumentDB compatible, no collection rename):

1. Write `snap_ingest_*` collections in `nmts_testing` only.
2. Validate counts, required fields, duplicate keys, Brand/Dealer/Branch
   mappings, and product totals.
3. Insert the new `snapshot_version` into live collections.
4. Flip `backend/testing_snapshot_active.json` only after validation.
5. On failure, delete only the new version and keep the previous snapshot.
6. Remove older snapshot versions after the new one is active.

Production is read through `ReadOnlyDatabase`. Prefer a dedicated production
read-only DocumentDB user and set `SNAPSHOT_SOURCE_DOCDB_SECRET_ID` in the
testing `.env`. The testing app never receives that credential.

### Snapshot business date / "today only"

Testing `_nmts_date_key()` returns the snapshot's source business date, not the
wall-clock date. Copied Product Hub rows stay visible without rewriting
production records. New testing uploads also use that frozen date so they
appear in the same Product Hub day.

## How you log in as Testing Master Admin

Use https://testing.sleepingstock.in/login with the existing Testing Master
Admin (`testing.master@sleepingstock.in`, role `master`). The password was
generated at seed time and is not stored in git. Production
`admin@sleepingstock.in` will not work on testing (different JWT secret and
database).

## How you test Product Hub filters and export

1. After a successful snapshot, Product Hub should show snapshot stock
   immediately.
2. Brand / Dealer / Branch filters, pagination, sorting, and summary totals
   must match the selected scope.
3. Testing Master Admin may use **All Data**, **Snapshot Reference Data**, or
   **Testing-Created Data**. Those filters are hidden in production.
4. Admin/User accounts keep existing Brand/Dealer/Branch restrictions.
5. **Export All** downloads one `.xlsx` with one worksheet, not a ZIP
   (PR #68, now on `main`). Pagination still loads one page from the backend.

## How you create testing users through User Hub

Create Admin/User accounts in testing User Hub as usual. They are stored only
in `nmts_testing`. New user IDs get a `TS-` prefix. Snapshot refresh does not
delete them.

## How you test uploads / orders / requests

Everything created on testing.sleepingstock.in writes to `nmts_testing` and
S3 `testing/` only. New business IDs are prefixed `TS-`:

| Generator | Production | Testing |
|---|---|---|
| Upload | `PU…` / `OU…` | `TS-PU…` / `TS-OU…` |
| Cancel | `CN…` | `TS-CN…` |
| Order | `OR…` | `TS-OR…` |
| Request | `RQ…` | `TS-RQ…` |
| User ID | `SS…` | `TS-SS…` |
| Query | `QRY…` | `TS-QRY…` |
| MOPS / AOPS / APS | `MOPS…` / `AOPS…` / `APS…` | `TS-MOPS…` / `TS-AOPS…` / `TS-APS…` |
| Mobile user | `MU…` | `TS-MU…` |
| Old report | `OR{yymmdd}…` | `TS-OR…` |

Snapshot reference rows keep their original numbers and are marked
`data_origin=snapshot`. Production counters are never read or incremented.
Testing-created rows are `data_origin=testing` and survive snapshot refresh.

Email, WhatsApp, and push stay in test mode (or test-recipient-only). The
archive scheduler stays disabled.

## How you redeploy an updated PR

Push the fix to the same PR branch, then re-run **Deploy PR to Testing** with
the same PR number (or run `deploy-pr.sh` again). Testing will move to the new
SHA. Banner values must change to the new commit.

## How you approve and merge only after testing passes

1. Test on https://testing.sleepingstock.in.
2. Approve the **application** or testing-infrastructure PR that is currently
   deployed.
3. Merge **that** PR into `main` yourself. Do not merge
   `cursor/testing-int-pr68-c95b`.
4. Do not use the website to merge or deploy arbitrary code. There is no such
   button.

## How production deployment occurs separately

Production deploy is a separate, explicit step after merge. This workflow
cannot deploy to port 8000, `/var/www/nmts-web`, or database `nmts`.

## How to roll back the testing deployment

Redeploy the previous known-good PR number. That replaces testing only.

Snapshot rollback (if the previous snapshot version is still present):

```bash
/opt/nmts-testing/deploy/testing/refresh-testing-snapshot.sh --rollback
```

## How to reset testing data without deleting Testing Master Admin

```bash
/opt/nmts-testing/deploy/testing/refresh-testing-snapshot.sh --reset-testing-created
```

This deletes testing-created products, uploads, orders, and requests. It keeps
all User Hub accounts, including Testing Master Admin. Then refresh the
snapshot if you need current production reference data again.

## S3 note and limitations

Testing never copies the production `dev/` archive and must not have delete or
write permission on `dev/`. Template metadata may be snapshotted; production
`dev/` files are not copied.

- Create a dedicated production DocumentDB **read-only** user and set
  `SNAPSHOT_SOURCE_DOCDB_SECRET_ID`. Until then the refresh script uses the
  application credential in a code-level read-only wrapper and never writes to
  `nmts`.
- The first snapshot into empty testing collections is insert-based and has
  been verified (71828 Product Hub rows). A later full re-refresh upserts by
  unique identity. If that job is interrupted, the previous snapshot stays
  active; do not kill production processes.
- GitHub Actions deploy needs `TESTING_SSH_*` secrets. Until those exist, run
  `/usr/local/bin/deploy-pr.sh --pr <n>` on the EC2 host.
- There is no website button that merges PRs or deploys arbitrary code.
