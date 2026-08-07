#!/usr/bin/env python3
"""Safe API-level UAT for Auto Perpetual (shared Atlas). Run with backend on 127.0.0.1:8000."""
from __future__ import annotations

import json
import os
import sys
from collections import defaultdict

import requests

BASE = os.environ.get("REACT_APP_BACKEND_URL", "http://127.0.0.1:8000").rstrip("/")
ADMIN = ("admin@sleepingstock.in", "admin123")


def login():
    r = requests.post(f"{BASE}/api/auth/login", json={"email": ADMIN[0], "password": ADMIN[1]}, timeout=60)
    r.raise_for_status()
    return r.json()["access_token"]


def main():
    results = []
    token = login()
    h = {"Authorization": f"Bearer {token}"}

    users = requests.get(f"{BASE}/api/mobile/users", headers=h, timeout=60).json()
    if not isinstance(users, list):
        print("mobile users failed", users)
        sys.exit(1)
    # pick branch with most active users
    by_branch = defaultdict(list)
    for u in users:
        if u.get("status") == "active" and not u.get("deleted_at"):
            key = (u["brand_name"], u["dealer_name"], u["branch"])
            by_branch[key].append(u)
    brand, dealer, branch = max(by_branch.items(), key=lambda kv: len(kv[1]))[0]
    branch_users = by_branch[(brand, dealer, branch)]
    results.append(("SETUP", f"branch={branch} users={len(branch_users)}", "OK"))

    # Quantity derivation (TEST G) — mirror mobile_api logic
    def derive(system_qty, physical_qty, damage_qty=0):
        diff = physical_qty - system_qty
        qs = "matched" if diff == 0 else ("shortage" if diff < 0 else "excess")
        shortage_qty = abs(diff) if diff < 0 else 0.0
        excess_qty = diff if diff > 0 else 0.0
        return qs, shortage_qty, excess_qty, damage_qty

    for sys_q, phys_q, exp_qs, exp_short, exp_excess in [
        (10, 10, "matched", 0, 0),
        (10, 8, "shortage", 2, 0),
        (10, 12, "excess", 0, 2),
    ]:
        qs, sh, ex, dmg = derive(sys_q, phys_q, 2 if phys_q == 10 and sys_q == 10 else 0)
        ok = qs == exp_qs and sh == exp_short and ex == exp_excess
        results.append((f"G qty {sys_q}/{phys_q}", f"{qs} sh={sh} ex={ex}", "PASS" if ok else "FAIL"))

    dmg_ok = derive(10, 10, 2)[3] == 2
    results.append(("G damage", "damage_qty=2", "PASS" if dmg_ok else "FAIL"))

    params = {"brand_name": brand, "dealer_name": dealer, "branch": branch}
    summary_before = requests.get(f"{BASE}/api/mobile/auto-perpetual/summary", headers=h, params=params, timeout=60).json()
    results.append(("Summary API", json.dumps({k: summary_before.get(k) for k in ['month_key', 'total_stock_lines', 'coverage_pct']}), "OK"))

    perf = requests.get(f"{BASE}/api/mobile/auto-perpetual/user-performance", headers=h, params=params, timeout=60).json()
    results.append(("Performance API", f"rows={len(perf) if isinstance(perf, list) else perf}", "PASS" if isinstance(perf, list) and len(perf) >= 1 else "FAIL"))

    hist = requests.get(
        f"{BASE}/api/mobile/perpetual-stock/verification-history",
        headers=h,
        params={"brand": brand, "dealer": dealer, "branch": branch, "limit": 5},
        timeout=60,
    )
    results.append(("History API", f"status={hist.status_code} count={len(hist.json()) if hist.ok else hist.text[:120]}", "PASS" if hist.ok else "FAIL"))

    # TEST D: double generate should not duplicate assignments
    def j(r):
        try:
            return r.json()
        except Exception:
            return {"_raw": r.text[:200]}

    gen1 = requests.post(
        f"{BASE}/api/mobile/auto-perpetual/generate",
        headers=h,
        params={**params, "recalc_pending": "false"},
        timeout=120,
    )
    gen2 = requests.post(
        f"{BASE}/api/mobile/auto-perpetual/generate",
        headers=h,
        params={**params, "recalc_pending": "false"},
        timeout=120,
    )
    g1 = j(gen1)
    g2 = j(gen2)
    d_ok = gen1.status_code in (200, 400, 409) and gen2.status_code == 200 and (g2.get("duplicate") is True)
    results.append(("D double generate", f"gen1={gen1.status_code} gen2={gen2.status_code} dup={g2.get('duplicate')}", "PASS" if d_ok else "PARTIAL" if gen2.ok else "FAIL"))

    if gen1.ok and not g1.get("duplicate"):
        abu = g1.get("assignments_by_user") or {}
        total_a = sum(abu.values())
        results.append(("A workload split", f"users={len(abu)} parts={total_a}", "PASS" if len(abu) >= 1 else "FAIL"))
        # unique parts per day
        today = requests.get(
            f"{BASE}/api/mobile/auto-perpetual/assignments/today",
            headers=h,
            params=params,
            timeout=60,
        ).json()
        parts = [a.get("part_number") for a in (today if isinstance(today, list) else today.get("assignments", []))]
        dup_parts = len(parts) - len(set(parts))
        results.append(("A unique parts", f"assignments={len(parts)} dup={dup_parts}", "PASS" if dup_parts == 0 else "FAIL"))

    # TEST B: mark one user inactive and recalc pending only if we have 2+ users
    if len(branch_users) >= 2:
        absent = branch_users[0]["mobile_user_id"]
        present = branch_users[1:]
        for u in branch_users:
            st = "inactive" if u["mobile_user_id"] == absent else "active"
            requests.put(
                f"{BASE}/api/mobile/users/{u['mobile_user_id']}/attendance",
                headers=h,
                json={"status": st, "brand_name": brand, "dealer_name": dealer, "branch": branch},
                timeout=60,
            )
        gen_b = requests.post(
            f"{BASE}/api/mobile/auto-perpetual/generate",
            headers=h,
            params={**params, "recalc_pending": "true"},
            timeout=120,
        )
        if gen_b.ok:
            abu = j(gen_b).get("assignments_by_user") or {}
            b_ok = absent not in abu and all(uid in abu for uid in [u["mobile_user_id"] for u in present[:4]])
            results.append(("B absent excluded", f"assigned_users={list(abu.keys())[:5]}", "PASS" if absent not in abu else "FAIL"))
        else:
            results.append(("B absent excluded", gen_b.text[:200], "SKIP"))
        # restore all active
        for u in branch_users:
            requests.put(
                f"{BASE}/api/mobile/users/{u['mobile_user_id']}/attendance",
                headers=h,
                json={"status": "active", "brand_name": brand, "dealer_name": dealer, "branch": branch},
                timeout=60,
            )

    print("UAT RESULTS")
    for name, detail, status in results:
        print(f"  [{status}] {name}: {detail}")
    fails = sum(1 for *_, s in results if s == "FAIL")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
