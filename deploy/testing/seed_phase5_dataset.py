#!/usr/bin/env python3
"""Seed a small Testing dataset through the real Upload + Publish APIs.

Flow:
  1) Create 2 brands / dealers / branches via User Hub master APIs
  2) Upload + publish-v2 Excel (real /upload/v2 path) twice per day slot
  3) Retarget two of the three publish cohorts to prior IST dates in
     nmts_testing only (so Analytics has ≥3 distinct dates)
  4) Idempotent incremental rollup finalize for each date

Never writes to Production. Password from NMTS_TESTING_MASTER_PASSWORD (never logged).
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import urllib.error
import urllib.request

IST = ZoneInfo("Asia/Kolkata")
BASE = os.environ.get("NMTS_TESTING_API", "http://127.0.0.1:8001/api").rstrip("/")
EMAIL = "testing.master@sleepingstock.in"
PASSWORD = (os.environ.get("NMTS_TESTING_MASTER_PASSWORD") or "").strip()
BACKEND = Path(os.environ.get("NMTS_TESTING_BACKEND", "/opt/nmts-testing/backend"))


def _req(method: str, path: str, body=None, token=None, data=None, headers=None):
    hdrs = dict(headers or {})
    if token:
        hdrs["Authorization"] = f"Bearer {token}"
    payload = None
    if body is not None:
        hdrs.setdefault("Content-Type", "application/json")
        payload = json.dumps(body).encode()
    elif data is not None:
        payload = data
    req = urllib.request.Request(f"{BASE}{path}", data=payload, headers=hdrs, method=method)
    try:
        with urllib.request.urlopen(req, timeout=180) as resp:
            raw = resp.read().decode()
            return resp.status, json.loads(raw) if raw else {}
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode()
        try:
            detail = json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            detail = {"detail": raw[:500]}
        raise RuntimeError(f"{method} {path} -> {exc.code} {detail}") from exc


def _xlsx_bytes(rows: list[dict]) -> bytes:
    from openpyxl import Workbook

    wb = Workbook()
    ws = wb.active
    ws.title = "Inventory"
    # upload/v2 column order: A Part No, B Part Name, C Loc, D Qty, E Receipt, F Sales, G MAV, H Category
    ws.append(["Part No", "Part Name", "Loc", "Qty", "Last Receipt Date", "Last Sales Date", "MAV", "Part Category"])
    for r in rows:
        ws.append([
            r["part_number"], r["part_name"], r.get("loc", "A1"), r["qty"],
            r["receipt"], r["sales"], r["mav"], r["category"],
        ])
    path = Path(tempfile.mkstemp(suffix=".xlsx")[1])
    wb.save(path)
    data = path.read_bytes()
    path.unlink(missing_ok=True)
    return data


def _multipart(fields: dict, filename: str, file_bytes: bytes) -> tuple[bytes, str]:
    boundary = f"----nmts{uuid.uuid4().hex}"
    body = b""
    for k, v in fields.items():
        body += (
            f"--{boundary}\r\nContent-Disposition: form-data; name=\"{k}\"\r\n\r\n{v}\r\n"
        ).encode()
    body += (
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; "
        f"filename=\"{filename}\"\r\n"
        f"Content-Type: application/vnd.openxmlformats-officedocument.spreadsheetml.sheet\r\n\r\n"
    ).encode()
    body += file_bytes + f"\r\n--{boundary}--\r\n".encode()
    return body, boundary


async def _retarget_upload_date(upload_ids: list[str], date_key: str) -> None:
    sys.path.insert(0, str(BACKEND))
    from dotenv import load_dotenv
    from motor.motor_asyncio import AsyncIOMotorClient
    from mongo_connection import build_mongo_client_args, resolve_mongo_url

    load_dotenv(BACKEND / ".env", override=True)
    if os.environ.get("DB_NAME") != "nmts_testing":
        raise SystemExit("ABORT: retarget refuses non-testing DB")
    url, kw = build_mongo_client_args(resolve_mongo_url())
    client = AsyncIOMotorClient(url, **kw)
    db = client["nmts_testing"]
    for coll in ("uploads", "upload_items", "products", "batch_summaries"):
        await db[coll].update_many(
            {"upload_id": {"$in": upload_ids}} if coll != "uploads" else {"id": {"$in": upload_ids}},
            {"$set": {"active_date_key": date_key, "date_key": date_key}},
        )
    # products use upload_id field
    await db.products.update_many(
        {"upload_id": {"$in": upload_ids}},
        {"$set": {"active_date_key": date_key, "date_key": date_key}},
    )
    client.close()


def main() -> int:
    if not PASSWORD:
        print("FAIL: NMTS_TESTING_MASTER_PASSWORD unset")
        return 2
    st, login = _req("POST", "/auth/login", {"email": EMAIL, "password": PASSWORD})
    if st != 200:
        print("FAIL login", login)
        return 1
    token = login["access_token"]

    for b in (
        {"code": "TSB1", "name": "TS Brand Alpha"},
        {"code": "TSB2", "name": "TS Brand Beta"},
    ):
        try:
            _req("POST", "/masters/brands", b, token=token)
            print("brand_ok", b["name"])
        except RuntimeError as exc:
            print("brand_skip", b["name"], str(exc)[:120])
    for d in (
        {"name": "TS Dealer Alpha", "brand": "TS Brand Alpha"},
        {"name": "TS Dealer Beta", "brand": "TS Brand Beta"},
    ):
        try:
            _req("POST", "/masters/dealers", d, token=token)
            print("dealer_ok", d["name"])
        except RuntimeError as exc:
            print("dealer_skip", d["name"], str(exc)[:120])
    for br in (
        {"name": "TS Branch Alpha", "dealer": "TS Dealer Alpha", "brand": "TS Brand Alpha"},
        {"name": "TS Branch Beta", "dealer": "TS Dealer Beta", "brand": "TS Brand Beta"},
    ):
        try:
            _req("POST", "/masters/branches", br, token=token)
            print("branch_ok", br["name"])
        except RuntimeError as exc:
            print("branch_skip", br["name"], str(exc)[:120])

    today = datetime.now(IST).date()
    target_dates = [today - timedelta(days=i) for i in (3, 2, 1)]
    cohorts: list[dict] = []

    for day in target_dates:
        date_key = day.strftime("%Y%m%d")
        upload_ids = []
        for brand, dealer, branch in (
            ("TS Brand Alpha", "TS Dealer Alpha", "TS Branch Alpha"),
            ("TS Brand Beta", "TS Dealer Beta", "TS Branch Beta"),
        ):
            rows = [
                {
                    "part_number": f"TSP{date_key}{branch[-1]}{i:03d}",
                    "part_name": f"Test Part {i}",
                    "loc": f"B{i}",
                    "qty": 10 + i,
                    "mav": 100.0 + i,
                    "receipt": (day - timedelta(days=120)).strftime("%d-%m-%Y"),
                    "sales": (day - timedelta(days=30)).strftime("%d-%m-%Y"),
                    "category": "Spare Parts",
                }
                for i in range(1, 6)
            ]
            xlsx = _xlsx_bytes(rows)
            body, boundary = _multipart(
                {"brand": brand, "dealer": dealer, "branch": branch},
                f"seed-{date_key}-{branch[-1]}.xlsx",
                xlsx,
            )
            _st, up = _req(
                "POST",
                "/upload/v2",
                token=token,
                data=body,
                headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
            )
            upload_id = up.get("id") or up.get("upload_id") or (up.get("upload") or {}).get("id")
            if not upload_id:
                print("FAIL upload response", up)
                return 1
            _pst, pub = _req("PUT", f"/uploads/{upload_id}/publish-v2", {}, token=token)
            print(
                "published",
                brand,
                upload_id[:8],
                "rollup",
                ((pub or {}).get("analytics_rollup") or {}).get("status"),
            )
            upload_ids.append(upload_id)
        cohorts.append({"date_key": date_key, "upload_ids": upload_ids})

    # Retarget each cohort to its intended IST date (uploads always stamp "today").
    import asyncio

    for cohort in cohorts:
        asyncio.run(_retarget_upload_date(cohort["upload_ids"], cohort["date_key"]))
        print("retargeted", cohort["date_key"], len(cohort["upload_ids"]))
        # Re-run incremental finalize for the retargeted date
        _st, fin = _req(
            "POST",
            f"/analytics/rollup/finalize?date={cohort['date_key'][:4]}-{cohort['date_key'][4:6]}-{cohort['date_key'][6:8]}",
            {},
            token=token,
        )
        print("finalize", cohort["date_key"], fin.get("status"), "reconciled", fin.get("reconciled"))

    _, brands_l = _req("GET", "/masters/brands", token=token)
    _, dealers_l = _req("GET", "/masters/dealers", token=token)
    _, branches_l = _req("GET", "/masters/branches", token=token)
    users = _req("GET", "/users/list", token=token)[1]
    if any(str(u.get("email") or "").lower() == "admin@sleepingstock.in" for u in users):
        print("FAIL: admin@ reappeared in Testing Hub")
        return 1
    print(
        "hub_counts brands=%s dealers=%s branches=%s users=%s dates=%s"
        % (len(brands_l), len(dealers_l), len(branches_l), len(users), len(cohorts))
    )
    print("PASS seed_phase5_dataset")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
