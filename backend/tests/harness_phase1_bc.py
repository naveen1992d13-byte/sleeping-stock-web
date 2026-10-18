#!/usr/bin/env python3
"""Runtime harness for Phase 1 B (MA protection) and C (fresh-start survival).

Run against the live Testing API on :8001. Never targets Production :8000.
Does not print passwords.
"""
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request

BASE = os.environ.get("NMTS_TESTING_API", "http://127.0.0.1:8001/api")
EMAIL = "testing.master@sleepingstock.in"
PASSWORD = (os.environ.get("NMTS_TESTING_MASTER_PASSWORD") or "").strip()


def _req(method: str, path: str, body=None, token: str | None = None):
    data = None
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if body is not None:
        data = json.dumps(body).encode()
    req = urllib.request.Request(f"{BASE}{path}", data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read().decode()
            return resp.status, json.loads(raw) if raw else {}
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode()
        try:
            payload = json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            payload = {"detail": raw}
        return exc.code, payload


def main() -> int:
    if not PASSWORD:
        print("FAIL: NMTS_TESTING_MASTER_PASSWORD unset")
        return 2

    # C: login + me
    status, login = _req("POST", "/auth/login", {"email": EMAIL, "password": PASSWORD})
    print(f"login_status={status}")
    if status != 200:
        print("FAIL: login", login.get("detail"))
        return 1
    token = login["access_token"]
    me_status, me = _req("GET", "/auth/me", token=token)
    print(f"me_status={me_status} role={me.get('role')} email={me.get('email')} user_id={me.get('user_id')}")
    if me_status != 200 or me.get("email") != EMAIL:
        print("FAIL: /auth/me")
        return 1

    # Hub counts (testing-only after reset)
    st, users = _req("GET", "/users/list", token=token)
    st_b, brands = _req("GET", "/masters/brands", token=token)
    st_d, dealers = _req("GET", "/masters/dealers", token=token)
    st_br, branches = _req("GET", "/masters/branches", token=token)
    print(
        f"hub users={len(users) if isinstance(users, list) else '?'} "
        f"brands={len(brands) if isinstance(brands, list) else '?'} "
        f"dealers={len(dealers) if isinstance(dealers, list) else '?'} "
        f"branches={len(branches) if isinstance(branches, list) else '?'}"
    )
    if isinstance(users, list):
        emails = [u.get("email") for u in users]
        if "admin@sleepingstock.in" in emails:
            print("FAIL: Production admin@ still visible in Testing User Hub")
            return 1
        if EMAIL not in emails:
            print("FAIL: Testing Master missing from User Hub")
            return 1

    # B: reject self-delete / MA delete
    ma_id = me.get("id")
    del_status, del_body = _req("DELETE", f"/users/{ma_id}", token=token)
    print(f"self_delete_status={del_status} detail={del_body.get('detail')}")
    if del_status not in (403, 400):
        print("FAIL: Master self-delete was not rejected")
        return 1

    # B: reject demotion
    dem_status, dem_body = _req(
        "PUT",
        f"/users/hub/{ma_id}",
        {"role": "user", "status": "inactive"},
        token=token,
    )
    print(f"demote_status={dem_status} detail={dem_body.get('detail')}")
    if dem_status != 403:
        print("FAIL: Master demote/deactivate was not rejected")
        return 1

    # B: Production admin login blocked (if somehow password known — use wrong path)
    # We only assert mirrored account is absent from list; login block is unit-covered.

    # Survive: me still works
    me2_status, me2 = _req("GET", "/auth/me", token=token)
    print(f"post_guard_me={me2_status} email={me2.get('email')}")
    if me2_status != 200:
        return 1

    print("PASS phase1 B/C harness")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
