"""Unit tests for Auto Perpetual blind count, edit history, and Audit View labels."""
from auto_perpetual_blind import (
    apply_quantity_edit,
    auto_count_phase,
    compact_edit_status,
    count_is_saved,
    format_qty,
    is_quantity_finalized,
    public_auto_task,
    strip_blind_fields,
)


def test_format_qty_strips_whole_floats():
    assert format_qty(8.0) == "8"
    assert format_qty(8.5) == "8.5"
    assert format_qty(None) == ""


def test_compact_edit_status_not_edited_for_legacy_rows():
    assert compact_edit_status(None) == "Not Edited"
    assert compact_edit_status([]) == "Not Edited"
    assert compact_edit_status("legacy") == "Not Edited"


def test_compact_edit_status_edited_values_only():
    history = [
        {"previous_quantity": 4, "new_quantity": 5},
        {"previous_quantity": 5, "new_quantity": 7.0},
        {"previous_quantity": 7, "new_quantity": 6},
    ]
    assert compact_edit_status(history) == "Edited [5] [7] [6]"


def test_old_client_phase_defaults_to_finalize():
    assert auto_count_phase(None) == "finalize"
    assert auto_count_phase("") == "finalize"
    assert auto_count_phase("SAVE") == "save"
    assert auto_count_phase("finalize") == "finalize"


def test_public_task_omits_restricted_fields_before_first_save():
    row = {
        "id": "a1",
        "part_number": "P1",
        "loc": "11A01",
        "system_qty": 40,
        "system_quantity": 40,
        "shortage_qty": 2,
        "status": "pending",
        "coverage_kind": "monthly",
    }
    task = public_auto_task(row, loc="11A01", session_id="S1", part_name="Bolt", system_qty=40)
    assert task["count_saved"] is False
    assert "system_qty" not in task
    assert "shortage_qty" not in task
    assert "difference" not in task
    assert "physical_quantity" not in task
    assert task["part_number"] == "P1"
    assert task["loc"] == "11A01"
    assert task["part_name"] == "Bolt"
    assert task["edit_status_label"] == "Not Edited"


def test_public_task_reveals_quantities_after_first_save():
    row = {"id": "a1", "part_number": "P1", "status": "pending", "loc": "11A01"}
    history = {
        "original_physical_quantity": 4,
        "physical_quantity": 5,
        "system_quantity": 6,
        "difference": -1,
        "shortage_qty": 1,
        "excess_qty": 0,
        "quantity_status": "shortage",
        "quantity_edit_history": [{"previous_quantity": 4, "new_quantity": 5}],
        "edit_count": 1,
    }
    task = public_auto_task(row, loc="11A01", session_id="S1", part_name="Bolt", system_qty=6, history=history)
    assert task["count_saved"] is True
    assert task["system_qty"] == 6
    assert task["physical_quantity"] == 5
    assert task["original_physical_quantity"] == 4
    assert task["edit_status_label"] == "Edited [5]"
    assert task["quantity_finalized"] is False


def test_strip_blind_fields_safety_net():
    stripped = strip_blind_fields({"part_number": "P1", "system_qty": 9, "loc": "A"})
    assert stripped == {"part_number": "P1", "loc": "A"}


def test_same_value_edit_is_ignored():
    existing = {
        "physical_quantity": 10,
        "original_physical_quantity": 8,
        "quantity_edit_history": [{"previous_quantity": 8, "new_quantity": 10}],
        "edit_count": 1,
        "quantity_edit_status": "Edited",
    }
    result = apply_quantity_edit(
        existing,
        new_qty=10,
        correction_reason="retry",
        user_name="A",
        mobile_user_id="MU1",
        device_id="D1",
        edited_at="now",
    )
    assert result["same_value"] is True
    assert result["changed"] is False
    assert result["edit_count"] == 1
    assert len(result["quantity_edit_history"]) == 1
    assert result["original_physical_quantity"] == 8


def test_real_edit_appends_and_preserves_first_count():
    existing = {
        "physical_quantity": 4,
        "original_physical_quantity": 4,
        "quantity_edit_history": [],
        "edit_count": 0,
    }
    first = apply_quantity_edit(
        existing,
        new_qty=6,
        correction_reason="miscount",
        user_name="A",
        mobile_user_id="MU1",
        device_id="D1",
        edited_at="t1",
    )
    assert first["changed"] is True
    assert first["original_physical_quantity"] == 4
    assert first["physical_quantity"] == 6
    assert first["edit_count"] == 1
    assert first["quantity_edit_history"][0]["new_quantity"] == 6

    second = apply_quantity_edit(
        {
            **existing,
            **{k: first[k] for k in ("physical_quantity", "original_physical_quantity", "quantity_edit_history", "edit_count", "quantity_edit_status")},
        },
        new_qty=5,
        correction_reason="recount",
        user_name="A",
        mobile_user_id="MU1",
        device_id="D1",
        edited_at="t2",
    )
    assert second["original_physical_quantity"] == 4
    assert second["physical_quantity"] == 5
    assert second["edit_count"] == 2
    assert [row["new_quantity"] for row in second["quantity_edit_history"]] == [6, 5]
    assert compact_edit_status(second["quantity_edit_history"]) == "Edited [6] [5]"
    # Historical values must never be summed into the latest quantity.
    assert second["physical_quantity"] != 4 + 6 + 5


def test_finalized_and_saved_flags():
    assert count_is_saved({"count_saved": True}) is True
    assert count_is_saved({}, {"physical_quantity": 3}) is True
    assert is_quantity_finalized({"status": "completed"}) is True
    assert is_quantity_finalized({}, {"quantity_finalized": True}) is True
    assert is_quantity_finalized({"status": "pending"}) is False
