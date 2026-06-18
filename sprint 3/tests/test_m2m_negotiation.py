from __future__ import annotations

import pytest

from conftest import (
    assert_contract_invalid,
    assert_contract_valid,
    assert_message_type,
    assert_uuid_v4,
    require_callable,
)


@pytest.mark.ct01
def test_ct01_help_request_accepted_redirects_idle_worker(runtime_api):
    run_flow = require_callable(runtime_api, "run_help_request_flow")

    result = run_flow(
        requester={"master_id": "A", "current_load": 150, "capacity": 100},
        neighbor={"master_id": "B", "current_load": 20, "capacity": 100},
        workers_needed=1,
        idle_workers=[{"worker_id": "B1", "address": "127.0.0.1:5002"}],
    )

    request = result["request_help"]
    accepted = result["response_accepted"]
    redirect = result["command_redirect"]

    assert_message_type(request, "request_help")
    assert request["payload"] == {
        "master_id": "A",
        "current_load": 150,
        "capacity": 100,
        "workers_needed": 1,
    }
    assert_message_type(accepted, "response_accepted")
    assert accepted["request_id"] == request["request_id"]
    assert accepted["payload"]["workers_offered"] == 1
    assert accepted["payload"]["worker_details"] == [
        {"worker_id": "B1", "address": "127.0.0.1:5002"}
    ]
    assert_message_type(redirect, "command_redirect")
    assert redirect["request_id"] == request["request_id"]
    assert redirect["payload"]["new_master_address"]
    assert redirect["payload"]["original_master_address"]

    assert_contract_valid(runtime_api, request | {"future_field": "ignored"})
    assert_contract_valid(runtime_api, accepted)
    assert_contract_valid(runtime_api, redirect)


@pytest.mark.ct02
def test_ct02_help_request_rejected_for_high_load(runtime_api):
    run_flow = require_callable(runtime_api, "run_help_request_flow")

    result = run_flow(
        requester={"master_id": "A", "current_load": 150, "capacity": 100},
        neighbor={"master_id": "B", "current_load": 130, "capacity": 100},
        workers_needed=2,
        idle_workers=[{"worker_id": "B1", "address": "127.0.0.1:5002"}],
    )

    request = result["request_help"]
    rejected = result["response_rejected"]

    assert_message_type(request, "request_help")
    assert_message_type(rejected, "response_rejected")
    assert rejected["request_id"] == request["request_id"]
    assert rejected["payload"] == {"reason": "high_load"}
    assert "command_redirect" not in result

    assert_contract_valid(runtime_api, rejected)


@pytest.mark.ct03
def test_ct03_concurrent_requests_keep_strict_request_id_correlation(runtime_api):
    run_concurrent = require_callable(runtime_api, "run_concurrent_help_requests")

    result = run_concurrent(request_count=12, workers_per_neighbor=1)
    exchanges = result["exchanges"]

    assert len(exchanges) == 12
    request_ids = [exchange["request"]["request_id"] for exchange in exchanges]
    assert len(set(request_ids)) == len(request_ids)

    for exchange in exchanges:
        request = exchange["request"]
        response = exchange["response"]
        assert_message_type(request, "request_help")
        assert response["type"] in {"response_accepted", "response_rejected"}
        assert response["request_id"] == request["request_id"]
        assert_uuid_v4(response["request_id"])


def test_protocol_rejects_missing_required_fields(runtime_api):
    missing_payload = {
        "type": "request_help",
        "request_id": "550e8400-e29b-41d4-a716-446655440000",
    }
    missing_workers_needed = {
        "type": "request_help",
        "request_id": "550e8400-e29b-41d4-a716-446655440000",
        "payload": {"master_id": "A", "current_load": 10, "capacity": 100},
    }

    assert_contract_invalid(runtime_api, missing_payload)
    assert_contract_invalid(runtime_api, missing_workers_needed)

