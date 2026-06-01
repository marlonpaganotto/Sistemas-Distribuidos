from __future__ import annotations

import pytest

from conftest import assert_message_type, require_callable


@pytest.mark.ct07
def test_ct07_m2m_timeout_uses_five_seconds_then_next_neighbor(runtime_api):
    run_timeout = require_callable(runtime_api, "run_timeout_fallback_flow")

    result = run_timeout(
        requester={"master_id": "A", "current_load": 150, "capacity": 100},
        neighbors=[
            {"master_id": "B", "behavior": "timeout"},
            {
                "master_id": "C",
                "behavior": "accept",
                "idle_workers": [
                    {"worker_id": "C1", "address": "127.0.0.1:5012"}
                ],
            },
        ],
        workers_needed=1,
        timeout_seconds=5,
    )

    assert result["configured_timeout_seconds"] == 5
    assert result["attempted_neighbors"] == ["B", "C"]
    assert result["timed_out_neighbors"] == ["B"]

    first_request, second_request = result["requests"]
    assert_message_type(first_request, "request_help")
    assert_message_type(second_request, "request_help")
    assert first_request["request_id"] != second_request["request_id"]

    accepted = result["response_accepted"]
    assert_message_type(accepted, "response_accepted")
    assert accepted["request_id"] == second_request["request_id"]
    assert accepted["payload"]["worker_details"] == [
        {"worker_id": "C1", "address": "127.0.0.1:5012"}
    ]

