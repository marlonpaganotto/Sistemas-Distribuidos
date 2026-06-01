from __future__ import annotations

import pytest

from conftest import assert_contract_valid, assert_message_type, require_callable


@pytest.mark.ct04
def test_ct04_borrowed_worker_registers_and_runs_sprint2_task_cycle(runtime_api):
    run_cycle = require_callable(runtime_api, "run_borrowed_worker_task_cycle")

    result = run_cycle(
        worker_id="B1",
        request_id="550e8400-e29b-41d4-a716-446655440000",
        saturated_master_address="127.0.0.1:5000",
        original_master_address="127.0.0.1:5050",
        task={"task_id": "T-42", "payload": {"value": 21}},
        status="OK",
    )

    registration = result["register_temporary_worker"]
    assert_message_type(registration, "register_temporary_worker")
    assert registration["payload"] == {
        "worker_id": "B1",
        "original_master_address": "127.0.0.1:5050",
    }
    assert_contract_valid(runtime_api, registration)

    sprint2_cycle = result["sprint2_cycle"]
    assert sprint2_cycle["handshake"] == "WORKER=ALIVE"
    assert sprint2_cycle["query"] == "QUERY"
    assert sprint2_cycle["task"]["task_id"] == "T-42"
    assert sprint2_cycle["status"] == "OK"
    assert sprint2_cycle["ack"] == "ACK"


@pytest.mark.ct06
def test_ct06_release_flow_honors_hysteresis_and_notifies_return(runtime_api):
    run_release = require_callable(runtime_api, "run_release_flow")

    result = run_release(
        master_id="A",
        load_sequence=[101, 95, 70, 69],
        borrowed_worker={
            "worker_id": "B1",
            "original_master_address": "127.0.0.1:5050",
        },
    )

    assert result["requested_help_at_loads"] == [101]
    assert result["release_events_before_threshold"] == []

    release = result["command_release"]
    notify = result["notify_worker_returned"]
    assert_message_type(release, "command_release")
    assert release["payload"] == {"original_master_address": "127.0.0.1:5050"}
    assert_message_type(notify, "notify_worker_returned")
    assert notify["request_id"] == release["request_id"]
    assert notify["payload"] == {"worker_id": "B1"}

    assert_contract_valid(runtime_api, release)
    assert_contract_valid(runtime_api, notify)


@pytest.mark.ct08
def test_ct08_borrowed_worker_returns_to_original_master_after_failure(runtime_api):
    run_recovery = require_callable(runtime_api, "run_worker_recovery_flow")

    result = run_recovery(
        worker_id="B1",
        saturated_master_address="127.0.0.1:5000",
        original_master_address="127.0.0.1:5050",
        failure="saturated_master_down",
    )

    assert result["detected_failure"] is True
    assert result["stopped_retrying_saturated_master"] is True
    assert result["reconnected_address"] == "127.0.0.1:5050"
    assert result["resumed_sprint2_cycle"]["handshake"] == "WORKER=ALIVE"
    assert result["resumed_sprint2_cycle"]["query"] in {"QUERY", "NO_TASK"}

