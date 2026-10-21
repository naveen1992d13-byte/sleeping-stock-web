"""Unit tests for Auto Perpetual IST-today session cards and re-audit filters."""
from datetime import datetime
from zoneinfo import ZoneInfo

from auto_perpetual import ist_date_key
from auto_perpetual_sessions import (
    TODAY_SESSION_NOT_CREATED,
    classify_auto_session_card,
    filter_session_tasks,
    session_task_flags,
    summarize_auto_sessions,
)


def test_ist_date_key_uses_asia_kolkata():
    ist = ZoneInfo("Asia/Kolkata")
    # Server "today" is the IST calendar date. Convert UTC instants to IST first.
    just_after_ist_midnight = datetime(2026, 10, 13, 18, 30, tzinfo=ZoneInfo("UTC")).astimezone(ist)
    assert just_after_ist_midnight.strftime("%Y-%m-%d") == "2026-10-14"
    assert ist_date_key(just_after_ist_midnight) == "2026-10-14"
    just_before_ist_midnight = datetime(2026, 10, 13, 18, 29, tzinfo=ZoneInfo("UTC")).astimezone(ist)
    assert ist_date_key(just_before_ist_midnight) == "2026-10-13"
    live = ist_date_key()
    assert live == datetime.now(ist).strftime("%Y-%m-%d")


def test_yesterday_pending_is_expired_not_today():
    today = "2026-10-14"
    card = classify_auto_session_card(
        allocation_date="2026-10-13", pending_count=3, today=today
    )
    assert card["is_today"] is False
    assert card["card_status"] == "EXPIRED"
    assert card["reaudit_available"] is True
    assert card["read_only"] is True
    assert card["editable"] is False


def test_previous_with_no_pending_is_completed():
    card = classify_auto_session_card(
        allocation_date="2026-10-12", pending_count=0, today="2026-10-14"
    )
    assert card["card_status"] == "COMPLETED"
    assert card["reaudit_available"] is False


def test_today_card_is_editable():
    card = classify_auto_session_card(
        allocation_date="2026-10-14", pending_count=2, today="2026-10-14"
    )
    assert card["is_today"] is True
    assert card["card_status"] == "TODAY"
    assert card["editable"] is True
    assert card["reaudit_available"] is False


def test_summarize_never_treats_previous_as_today():
    rows = [
        {"session_id": "APS-YDAY", "allocation_date": "2026-10-13", "status": "pending", "part_number": "A"},
        {"session_id": "APS-YDAY", "allocation_date": "2026-10-13", "status": "completed", "part_number": "B"},
        {"session_id": "APS-OLD", "allocation_date": "2026-10-11", "status": "completed", "part_number": "C"},
    ]
    summary = summarize_auto_sessions(rows, today="2026-10-14")
    assert summary["today"] == "2026-10-14"
    assert summary["today_created"] is False
    assert summary["message"] == TODAY_SESSION_NOT_CREATED
    assert [row["session_id"] for row in summary["sessions"]] == ["APS-YDAY", "APS-OLD"]
    assert summary["sessions"][0]["card_status"] == "EXPIRED"
    assert summary["sessions"][0]["pending_count"] == 1
    assert summary["sessions"][0]["verified_count"] == 1
    assert summary["sessions"][0]["assigned_count"] == 2
    assert summary["sessions"][1]["card_status"] == "COMPLETED"
    assert "TODAY" not in [row["card_status"] for row in summary["sessions"]]


def test_summarize_puts_today_first_when_created():
    rows = [
        {"session_id": "APS-YDAY", "allocation_date": "2026-10-13", "status": "pending", "part_number": "A"},
        {"session_id": "APS-TODAY", "allocation_date": "2026-10-14", "status": "pending", "part_number": "T1"},
        {"session_id": "APS-TODAY", "allocation_date": "2026-10-14", "status": "completed", "part_number": "T2"},
    ]
    summary = summarize_auto_sessions(rows, today="2026-10-14")
    assert summary["today_created"] is True
    assert summary["message"] is None
    assert summary["sessions"][0]["session_id"] == "APS-TODAY"
    assert summary["sessions"][0]["card_status"] == "TODAY"
    assert summary["sessions"][0]["progress_pct"] == 50
    assert summary["sessions"][1]["session_id"] == "APS-YDAY"


def test_reaudit_keeps_pending_including_live_qty_zero():
    rows = [
        {"part_number": "ZERO", "status": "pending"},
        {"part_number": "DONE", "status": "completed"},
        {"part_number": "LIVE", "status": "pending"},
    ]
    live = {"ZERO": 0, "DONE": 4, "LIVE": 9}
    view_rows = filter_session_tasks(rows, mode="view", live_qty_by_part=live)
    assert [row["part_number"] for row in view_rows] == ["ZERO", "DONE", "LIVE"]
    reaudit_rows = filter_session_tasks(rows, mode="reaudit", live_qty_by_part=live)
    assert [row["part_number"] for row in reaudit_rows] == ["ZERO", "LIVE"]
    assert any(row["part_number"] == "ZERO" for row in reaudit_rows)


def test_old_client_count_payload_omits_session_id():
    from mobile_api import AutoCountSaveRequest, AutoFinalizeRequest

    save = AutoCountSaveRequest(part_number="P1", physical_qty=4)
    assert save.session_id is None
    finalize = AutoFinalizeRequest(part_numbers=["P1"])
    assert finalize.session_id is None


def test_session_task_flags_reaudit_is_editable_without_becoming_today():
    flags = session_task_flags(allocation_date="2026-10-13", mode="reaudit", today="2026-10-14")
    assert flags["is_today"] is False
    assert flags["reaudit"] is True
    assert flags["editable"] is True
    assert flags["read_only"] is False
    view_flags = session_task_flags(allocation_date="2026-10-13", mode="view", today="2026-10-14")
    assert view_flags["editable"] is False
    assert view_flags["read_only"] is True
    assert view_flags["reaudit"] is False
