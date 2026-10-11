"""GitHub PR status and merge helpers for the testing banner.

Tokens stay server-side. Responses never include credentials. Live merge is
disabled unless TESTING_LIVE_GITHUB_MERGE=true (default false).
"""
from __future__ import annotations

import hashlib
import logging
import os
import subprocess
from datetime import datetime, timezone
from typing import Any, Dict, Optional
from urllib.parse import quote

import httpx

try:
    from . import testing_runtime
except ImportError:
    import testing_runtime

logger = logging.getLogger("nmts.testing_github")

DEFAULT_REPO = "naveen1992d13-byte/sleeping-stock-web"
DEFAULT_BASE = "main"
MERGE_PHRASE = "MERGE AND CLEAR TEST DATA"
LIVE_MERGE_ENV = "TESTING_LIVE_GITHUB_MERGE"
PRODUCTION_API_DEFAULT = "https://api.sleepingstock.in"
PRODUCTION_WEB_DEFAULT = "https://sleepingstock.in"
PRODUCTION_ROOT_DEFAULT = "/opt/nmts-production"


def _env(name: str, default: str = "") -> str:
    return str(os.getenv(name, default) or default).strip()


def github_token() -> str:
    return _env("NMTS_GITHUB_TOKEN") or _env("GITHUB_TOKEN")


def github_repo() -> str:
    return _env("NMTS_GITHUB_REPO", DEFAULT_REPO)


def production_base_branch() -> str:
    return _env("NMTS_PRODUCTION_BASE_BRANCH", DEFAULT_BASE)


def live_merge_enabled() -> bool:
    return _env(LIVE_MERGE_ENV).lower() in {"1", "true", "yes", "on"}


def _headers() -> Dict[str, str]:
    token = github_token()
    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "nmts-testing-banner",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


async def _get_json(path: str, timeout: float = 20.0) -> tuple[int, Any]:
    url = f"https://api.github.com{path}"
    async with httpx.AsyncClient(timeout=timeout) as client:
        response = await client.get(url, headers=_headers())
        try:
            payload = response.json()
        except Exception:
            payload = {"message": response.text[:300]}
        return response.status_code, payload


async def _put_json(path: str, body: dict, timeout: float = 30.0) -> tuple[int, Any]:
    url = f"https://api.github.com{path}"
    async with httpx.AsyncClient(timeout=timeout) as client:
        response = await client.put(url, headers=_headers(), json=body)
        try:
            payload = response.json()
        except Exception:
            payload = {"message": response.text[:300]}
        return response.status_code, payload


def production_api_url() -> str:
    return _env("NMTS_PRODUCTION_API_URL", PRODUCTION_API_DEFAULT).rstrip("/")


def production_web_url() -> str:
    return _env("NMTS_PRODUCTION_WEB_URL", PRODUCTION_WEB_DEFAULT).rstrip("/")


def production_root() -> str:
    return _env("NMTS_PRODUCTION_ROOT", PRODUCTION_ROOT_DEFAULT)


def deployed_sha() -> str:
    meta = testing_runtime.load_deployment_metadata()
    return str(meta.get("commit") or meta.get("commit_short") or "").strip()


def deployed_pr_number() -> Optional[int]:
    meta = testing_runtime.load_deployment_metadata()
    raw = meta.get("pr_number")
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def _check_rollup(check_runs: list, commit_statuses: Optional[list] = None) -> Dict[str, Any]:
    pending = 0
    failed = 0
    passed = 0
    names = []
    failed_conclusions = {"failure", "cancelled", "timed_out", "action_required", "startup_failure", "stale", "error"}
    for run in check_runs or []:
        name = str(run.get("name") or "")
        status = str(run.get("status") or "")
        conclusion = str(run.get("conclusion") or "")
        names.append({"name": name, "status": status, "conclusion": conclusion, "kind": "check_run"})
        if status != "completed":
            pending += 1
            continue
        if conclusion in {"success", "skipped", "neutral"}:
            passed += 1
        elif conclusion in failed_conclusions or conclusion:
            failed += 1
    for st in commit_statuses or []:
        name = str(st.get("context") or st.get("name") or "")
        state = str(st.get("state") or "")
        names.append({"name": name, "status": state, "conclusion": state, "kind": "status"})
        if state in {"pending", "expected"}:
            pending += 1
        elif state in {"success"}:
            passed += 1
        elif state:
            failed += 1
    missing = (not check_runs) and (not commit_statuses)
    ok = (not missing) and pending == 0 and failed == 0 and passed > 0
    if missing:
        label = "missing"
        reason = "checks_missing"
    elif pending:
        label = "pending"
        reason = "checks_pending"
    elif failed:
        label = "failing"
        reason = "checks_failed"
    elif passed == 0:
        label = "missing"
        reason = "checks_missing"
    else:
        label = "passing"
        reason = ""
    return {
        "ok": ok,
        "pending": pending,
        "failed": failed,
        "passed": passed,
        "missing": missing,
        "reason": reason,
        "checks": names[:40],
        "label": label,
    }


async def fetch_pr_status(pr_number: Optional[int] = None) -> Dict[str, Any]:
    number = pr_number or deployed_pr_number()
    repo = github_repo()
    if not number:
        return {
            "ok": False,
            "error": "no_connected_pr",
            "pr_number": None,
            "deployed_sha": deployed_sha(),
        }
    code, pr = await _get_json(f"/repos/{repo}/pulls/{number}")
    if code != 200 or not isinstance(pr, dict):
        return {
            "ok": False,
            "error": "pr_lookup_failed",
            "http_status": code,
            "pr_number": number,
            "detail": (pr or {}).get("message") if isinstance(pr, dict) else "lookup_failed",
            "deployed_sha": deployed_sha(),
        }
    head_sha = str(((pr.get("head") or {}).get("sha")) or "")
    base_ref = str(((pr.get("base") or {}).get("ref")) or "")
    checks_code, checks = await _get_json(
        f"/repos/{repo}/commits/{quote(head_sha)}/check-runs"
    )
    check_runs = (checks or {}).get("check_runs") if isinstance(checks, dict) else []
    _status_code, combined = await _get_json(
        f"/repos/{repo}/commits/{quote(head_sha)}/status"
    )
    commit_statuses = (combined or {}).get("statuses") if isinstance(combined, dict) else []
    rollup = _check_rollup(
        check_runs if isinstance(check_runs, list) else [],
        commit_statuses if isinstance(commit_statuses, list) else [],
    )
    tested_sha = deployed_sha()
    mergeable = pr.get("mergeable")
    draft = bool(pr.get("draft"))
    state = str(pr.get("state") or "")
    sha_match = bool(tested_sha) and head_sha.lower().startswith(tested_sha.lower()[:7]) and (
        tested_sha.lower() in head_sha.lower() or head_sha.lower() in tested_sha.lower()
    )
    if len(tested_sha) >= 40 and len(head_sha) >= 40:
        sha_match = tested_sha.lower() == head_sha.lower()
    blockers = []
    if state != "open":
        blockers.append("pr_not_open")
    if draft:
        blockers.append("pr_is_draft")
    if mergeable is False:
        blockers.append("merge_conflict")
    if mergeable is None:
        blockers.append("mergeable_unknown")
    if not rollup["ok"]:
        blockers.append(str(rollup.get("reason") or "checks_not_green"))
    if not sha_match:
        blockers.append("sha_mismatch")
    if base_ref != production_base_branch():
        blockers.append("base_not_production")
    if not github_token():
        blockers.append("github_token_missing")
    health = testing_environment_health()
    if not health.get("ok"):
        blockers.append("testing_health_failed")
    return {
        "ok": True,
        "pr_number": number,
        "title": pr.get("title") or "",
        "state": state,
        "draft": draft,
        "mergeable": mergeable,
        "merged": bool(pr.get("merged")),
        "html_url": pr.get("html_url") or "",
        "head_sha": head_sha,
        "head_sha_short": head_sha[:7],
        "base_branch": base_ref,
        "production_base_branch": production_base_branch(),
        "deployed_sha": tested_sha,
        "sha_match": sha_match,
        "ci": rollup,
        "testing_health": health,
        "blockers": blockers,
        "eligible": not blockers,
        "live_merge_enabled": live_merge_enabled(),
        "confirm_phrase": MERGE_PHRASE,
        "clear_phrase": "CLEAR TESTING DATA",
        "checks_http_status": checks_code,
    }


def testing_environment_health() -> Dict[str, Any]:
    """In-process testing isolation/health used as a merge eligibility gate."""
    checks = [
        {"name": "testing_env", "ok": testing_runtime.is_testing_env()},
        {"name": "testing_db", "ok": testing_runtime.db_name() == testing_runtime.TESTING_DB_NAME},
        {"name": "testing_storage", "ok": testing_runtime.storage_env_name() == testing_runtime.TESTING_STORAGE_ENV},
        {"name": "overlay_mode", "ok": testing_runtime.overlay_mode_enabled()},
        {"name": "deployed_sha", "ok": bool(deployed_sha())},
        {"name": "mongo_readonly", "ok": bool((os.getenv("MONGO_READONLY_URL") or os.getenv("DOCDB_READONLY_SECRET_ID") or "").strip())},
    ]
    failed = [c["name"] for c in checks if not c["ok"]]
    return {
        "ok": not failed,
        "failed": failed,
        "checks": checks,
        "label": "healthy" if not failed else "unhealthy",
    }


def _sha_matches(expected: str, actual: str) -> bool:
    left = str(expected or "").strip().lower()
    right = str(actual or "").strip().lower()
    if not left or not right:
        return False
    if left == right:
        return True
    return left.startswith(right) or right.startswith(left[:7])


def _production_git_sha() -> str:
    root = production_root()
    if not root or not os.path.isdir(root):
        return ""
    try:
        out = subprocess.check_output(
            ["git", "-C", root, "rev-parse", "HEAD"],
            text=True,
            timeout=5,
            stderr=subprocess.DEVNULL,
        )
        return str(out or "").strip()
    except Exception:
        return ""


async def verify_production_deployment(expected_sha: str) -> Dict[str, Any]:
    """Confirm the merged SHA is healthy in Production before any testing cleanup.

    Never logs in with real credentials. Login smoke uses an invalid password
    and expects HTTP 401 (API up) rather than 5xx.
    """
    api = production_api_url()
    web = production_web_url()
    expected = str(expected_sha or "").strip()
    checks: Dict[str, Any] = {}

    prod_sha = _production_git_sha()
    checks["production_sha"] = prod_sha
    checks["expected_sha"] = expected
    checks["sha_match"] = _sha_matches(expected, prod_sha)

    async with httpx.AsyncClient(timeout=12.0, follow_redirects=True) as client:
        try:
            backend = await client.get(f"{api}/api/maintenance/status")
            checks["backend_health"] = {
                "ok": backend.status_code == 200,
                "http_status": backend.status_code,
            }
        except Exception as exc:
            checks["backend_health"] = {"ok": False, "error": str(exc)[:200]}
        try:
            runtime = await client.get(f"{api}/api/testing/runtime")
            payload = runtime.json() if runtime.status_code == 200 else {}
            checks["runtime"] = {
                "ok": runtime.status_code == 200 and payload.get("is_testing") is False,
                "http_status": runtime.status_code,
                "is_testing": payload.get("is_testing") if isinstance(payload, dict) else None,
            }
        except Exception as exc:
            checks["runtime"] = {"ok": False, "error": str(exc)[:200]}
        try:
            frontend = await client.get(f"{web}/", headers={"Accept": "text/html"})
            checks["frontend_health"] = {
                "ok": frontend.status_code == 200,
                "http_status": frontend.status_code,
            }
        except Exception as exc:
            checks["frontend_health"] = {"ok": False, "error": str(exc)[:200]}
        try:
            login = await client.post(
                f"{api}/api/auth/login",
                json={"email": "nmts-merge-verify-invalid@example.invalid", "password": "not-a-real-password"},
            )
            checks["login_smoke"] = {
                "ok": login.status_code in {401, 422, 400},
                "http_status": login.status_code,
            }
        except Exception as exc:
            checks["login_smoke"] = {"ok": False, "error": str(exc)[:200]}
        try:
            deploy = await client.get(f"{web}/deployment.json")
            remote_sha = ""
            if deploy.status_code == 200:
                body = deploy.json() if deploy.content else {}
                if isinstance(body, dict):
                    remote_sha = str(body.get("commit") or body.get("commit_short") or "")
            checks["frontend_deployment"] = {
                "ok": True,
                "http_status": deploy.status_code,
                "sha": remote_sha,
                "present": deploy.status_code == 200,
            }
            if remote_sha:
                checks["sha_match"] = checks["sha_match"] or _sha_matches(expected, remote_sha)
        except Exception as exc:
            checks["frontend_deployment"] = {"ok": True, "present": False, "error": str(exc)[:200]}

    required = [
        checks.get("backend_health", {}).get("ok"),
        checks.get("runtime", {}).get("ok"),
        checks.get("frontend_health", {}).get("ok"),
        checks.get("login_smoke", {}).get("ok"),
        checks.get("sha_match"),
    ]
    ok = all(bool(x) for x in required)
    return {
        "ok": ok,
        "status": "verified" if ok else "failed",
        "checks": checks,
        "message": (
            "Production is serving the merged SHA and smoke checks passed."
            if ok
            else "Production verification failed. Testing data will be retained."
        ),
    }


def confirm_phrase_ok(text: str) -> bool:
    return str(text or "").strip() == MERGE_PHRASE


async def merge_pull_request(
    *,
    confirm_text: str,
    operation_id: str,
    actor: dict,
    dry_run: Optional[bool] = None,
) -> Dict[str, Any]:
    if not confirm_phrase_ok(confirm_text):
        return {"ok": False, "error": "confirm_phrase_mismatch", "status": "rejected"}
    status = await fetch_pr_status()
    if not status.get("ok"):
        return {"ok": False, "error": status.get("error") or "pr_unavailable", "status": "rejected", "pr": status}
    if not status.get("eligible"):
        return {
            "ok": False,
            "error": "not_eligible",
            "status": "blocked",
            "blockers": status.get("blockers"),
            "pr": {k: status.get(k) for k in (
                "pr_number", "title", "head_sha", "deployed_sha", "base_branch", "ci"
            )},
        }
    execute = live_merge_enabled() if dry_run is None else (not dry_run)
    receipt = {
        "operation_id": operation_id,
        "pr_number": status["pr_number"],
        "title": status.get("title"),
        "tested_sha": status.get("deployed_sha"),
        "head_sha": status.get("head_sha"),
        "target_branch": status.get("base_branch"),
        "actor_id": actor.get("id"),
        "actor_role": actor.get("role"),
        "dry_run": not execute,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    if not execute:
        receipt["status"] = "dry_run"
        receipt["message"] = (
            "Eligibility passed. Live GitHub merge is disabled until "
            f"{LIVE_MERGE_ENV}=true and operator approval."
        )
        return {"ok": True, "status": "dry_run", "receipt": receipt, "pr": status}
    sha = status["head_sha"]
    code, payload = await _put_json(
        f"/repos/{github_repo()}/pulls/{status['pr_number']}/merge",
        {
            "commit_title": f"Merge PR #{status['pr_number']} from testing (op {operation_id[:12]})",
            "sha": sha,
            "merge_method": "merge",
        },
    )
    receipt["github_http_status"] = code
    receipt["merged"] = bool(isinstance(payload, dict) and payload.get("merged"))
    receipt["merge_sha"] = (payload or {}).get("sha") if isinstance(payload, dict) else None
    receipt["status"] = "merged" if receipt["merged"] else "github_merge_failed"
    if not receipt["merged"]:
        receipt["detail"] = (payload or {}).get("message") if isinstance(payload, dict) else "merge_failed"
        return {"ok": False, "status": "github_merge_failed", "receipt": receipt, "pr": status}
    return {"ok": True, "status": "merged", "receipt": receipt, "pr": status}


def operation_id_for(seed: str) -> str:
    raw = f"{seed}:{datetime.now(timezone.utc).strftime('%Y%m%d%H%M')}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()
