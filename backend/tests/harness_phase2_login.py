#!/usr/bin/env python3
"""Phase 2 Login harness — API cycles against Testing :8001 only."""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request

BASE = os.environ.get("NMTS_TESTING_API", "http://127.0.0.1:8001/api")
EMAIL = "testing.master@sleepingstock.in"
PASSWORD = (os.environ.get("NMTS_TESTING_MASTER_PASSWORD") or "").strip()


def _post(path, body):
    req = urllib.request.Request(
        f"{BASE}{path}",
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.status, json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode() or "{}")


def main() -> int:
    if not PASSWORD:
        print("FAIL: NMTS_TESTING_MASTER_PASSWORD unset")
        return 2

    # Wrong password
    st, body = _post("/auth/login", {"email": EMAIL, "password": "wrong-password"})
    print("wrong_password", st)
    if st != 401:
        print("FAIL expected 401")
        return 1

    # Production admin must not authenticate into Testing
    st, body = _post("/auth/login", {"email": "admin@sleepingstock.in", "password": "admin123"})
    print("prod_admin_login", st, body.get("detail"))
    if st == 200:
        print("FAIL admin@ must not login to Testing")
        return 1

    ok = 0
    latencies = []
    for i in range(1, 31):
        t0 = time.time()
        st, body = _post("/auth/login", {"email": EMAIL, "password": PASSWORD})
        ms = (time.time() - t0) * 1000
        latencies.append(ms)
        if st == 200 and body.get("access_token"):
            ok += 1
        else:
            print("FAIL cycle", i, st, body.get("detail"))
            return 1
    latencies.sort()
    print(
        f"login_cycles ok={ok}/30 p50={latencies[14]:.1f}ms p95={latencies[28]:.1f}ms "
        f"max={latencies[-1]:.1f}ms"
    )
    print("PASS phase2 login harness")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
