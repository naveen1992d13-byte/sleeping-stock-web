"""GitHub merge eligibility tests. No live GitHub calls."""
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

import testing_github as tg


def test_confirm_phrase_exact():
    assert tg.confirm_phrase_ok("MERGE AND CLEAR TEST DATA") is True
    assert tg.confirm_phrase_ok("merge and clear test data") is False
    assert tg.confirm_phrase_ok("") is False


def test_check_rollup_passing_and_failing():
    ok = tg._check_rollup([
        {"name": "ci", "status": "completed", "conclusion": "success"},
        {"name": "skip", "status": "completed", "conclusion": "skipped"},
    ])
    assert ok["ok"] is True
    bad = tg._check_rollup([
        {"name": "ci", "status": "completed", "conclusion": "failure"},
    ])
    assert bad["ok"] is False
    pending = tg._check_rollup([
        {"name": "ci", "status": "in_progress", "conclusion": ""},
    ])
    assert pending["ok"] is False


def test_empty_checks_are_missing():
    empty = tg._check_rollup([], [])
    assert empty["ok"] is False
    assert empty["reason"] == "checks_missing"
    assert empty["missing"] is True


def test_cancelled_and_timed_out_checks_block():
    cancelled = tg._check_rollup([
        {"name": "ci", "status": "completed", "conclusion": "cancelled"},
    ])
    assert cancelled["ok"] is False
    timed = tg._check_rollup([
        {"name": "ci", "status": "completed", "conclusion": "timed_out"},
    ])
    assert timed["ok"] is False


def test_commit_status_pending_blocks():
    pending = tg._check_rollup([], [{"context": "ci", "state": "pending"}])
    assert pending["ok"] is False
    assert pending["reason"] == "checks_pending"


def test_live_merge_default_off(monkeypatch):
    monkeypatch.delenv("TESTING_LIVE_GITHUB_MERGE", raising=False)
    assert tg.live_merge_enabled() is False
    monkeypatch.setenv("TESTING_LIVE_GITHUB_MERGE", "true")
    assert tg.live_merge_enabled() is True


def test_token_not_leaked_in_status_shape():
    # fetch_pr_status without token still returns a serialisable payload, never a token key.
    import asyncio
    monkeypatch_env = {"NMTS_GITHUB_TOKEN": "secret-value", "GITHUB_TOKEN": "secret-value"}

    async def fake_get(path, timeout=20.0):
        return 404, {"message": "not found"}

    async def _go():
        original = tg._get_json
        tg._get_json = fake_get
        try:
            result = await tg.fetch_pr_status(1)
        finally:
            tg._get_json = original
        dumped = str(result)
        assert "secret-value" not in dumped
        assert "token" not in result
        return result

    result = asyncio.run(_go())
    assert result.get("ok") is False
    _ = monkeypatch_env


def test_merge_blocked_when_not_eligible(monkeypatch):
    async def fake_status():
        return {
            "ok": True,
            "eligible": False,
            "blockers": ["pr_is_draft", "sha_mismatch"],
            "pr_number": 12,
            "title": "draft",
            "head_sha": "abc",
            "deployed_sha": "def",
            "base_branch": "main",
            "ci": {"ok": False},
        }

    monkeypatch.setattr(tg, "fetch_pr_status", fake_status)
    result = __import__("asyncio").run(tg.merge_pull_request(
        confirm_text="MERGE AND CLEAR TEST DATA",
        operation_id="op-1",
        actor={"id": "u1", "role": "master"},
    ))
    assert result["ok"] is False
    assert result["status"] == "blocked"
    assert "pr_is_draft" in result["blockers"]


def test_merge_dry_run_when_live_disabled(monkeypatch):
    monkeypatch.delenv("TESTING_LIVE_GITHUB_MERGE", raising=False)

    async def fake_status():
        return {
            "ok": True,
            "eligible": True,
            "blockers": [],
            "pr_number": 12,
            "title": "ready",
            "head_sha": "abc1234deadbeef",
            "deployed_sha": "abc1234deadbeef",
            "base_branch": "main",
            "ci": {"ok": True},
        }

    async def fail_put(*args, **kwargs):
        raise AssertionError("live GitHub merge must not be called")

    monkeypatch.setattr(tg, "fetch_pr_status", fake_status)
    monkeypatch.setattr(tg, "_put_json", fail_put)
    result = __import__("asyncio").run(tg.merge_pull_request(
        confirm_text="MERGE AND CLEAR TEST DATA",
        operation_id="op-dry",
        actor={"id": "u1", "role": "master"},
    ))
    assert result["ok"] is True
    assert result["status"] == "dry_run"
    assert result["receipt"]["dry_run"] is True


def test_fetch_pr_status_required_blockers(monkeypatch):
    monkeypatch.setenv("APP_ENV", "testing")
    monkeypatch.setenv("DB_NAME", "nmts_testing")
    monkeypatch.setenv("NMTS_STORAGE_ENV", "testing")
    monkeypatch.setenv("MONGO_READONLY_URL", "mongodb://readonly-user@127.0.0.1/nmts")
    monkeypatch.setenv("NMTS_GITHUB_TOKEN", "token")
    pr = {
        "title": "x",
        "state": "open",
        "draft": True,
        "mergeable": False,
        "merged": False,
        "html_url": "",
        "head": {"sha": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"},
        "base": {"ref": "develop"},
    }

    async def fake_get(path, timeout=20.0):
        if "/pulls/" in path:
            return 200, pr
        if "/check-runs" in path:
            return 200, {"check_runs": []}
        if path.endswith("/status"):
            return 200, {"statuses": []}
        return 404, {}

    monkeypatch.setattr(tg, "_get_json", fake_get)
    monkeypatch.setattr(tg, "deployed_sha", lambda: "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb")
    result = __import__("asyncio").run(tg.fetch_pr_status(12))
    for expected in ("pr_is_draft", "merge_conflict", "sha_mismatch", "base_not_production", "checks_missing"):
        assert expected in result["blockers"], result["blockers"]
    assert result["eligible"] is False


def test_mergeable_unknown_blocks(monkeypatch):
    monkeypatch.setenv("APP_ENV", "testing")
    monkeypatch.setenv("DB_NAME", "nmts_testing")
    monkeypatch.setenv("NMTS_STORAGE_ENV", "testing")
    monkeypatch.setenv("MONGO_READONLY_URL", "mongodb://readonly-user@127.0.0.1/nmts")
    monkeypatch.setenv("NMTS_GITHUB_TOKEN", "token")
    pr = {
        "title": "x",
        "state": "open",
        "draft": False,
        "mergeable": None,
        "merged": False,
        "html_url": "",
        "head": {"sha": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"},
        "base": {"ref": "main"},
    }

    async def fake_get(path, timeout=20.0):
        if "/pulls/" in path:
            return 200, pr
        if "/check-runs" in path:
            return 200, {"check_runs": [{"name": "ci", "status": "completed", "conclusion": "success"}]}
        if path.endswith("/status"):
            return 200, {"statuses": []}
        return 404, {}

    monkeypatch.setattr(tg, "_get_json", fake_get)
    monkeypatch.setattr(tg, "deployed_sha", lambda: "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa")
    result = __import__("asyncio").run(tg.fetch_pr_status(12))
    assert "mergeable_unknown" in result["blockers"]


def test_wrong_confirm_phrase_rejected():
    result = __import__("asyncio").run(tg.merge_pull_request(
        confirm_text="please merge",
        operation_id="op-bad",
        actor={"id": "u1", "role": "master"},
    ))
    assert result["ok"] is False
    assert result["error"] == "confirm_phrase_mismatch"
