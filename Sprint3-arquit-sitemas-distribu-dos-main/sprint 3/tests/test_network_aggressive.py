from __future__ import annotations

import socket
import sys
import time
from pathlib import Path

import pytest

from conftest import assert_contract_invalid, assert_message_type, require_callable


RUNTIME_ROOT = Path(__file__).resolve().parents[1] / "runtime"
if str(RUNTIME_ROOT) not in sys.path:
    sys.path.insert(0, str(RUNTIME_ROOT))

from master import MasterNode  # noqa: E402
from protocol import recv_json  # noqa: E402


@pytest.mark.ct07
def test_aggressive_tcp_malformed_json_is_logged_and_connection_closed():
    master = MasterNode("B", "127.0.0.1:5004")
    master.start()
    time.sleep(0.2)

    try:
        with socket.create_connection(("127.0.0.1", 5004), timeout=5) as sock:
            sock.settimeout(5)
            sock.sendall(b'{"type":"request_help","request_id":"not-a-uuid"\n')

            with pytest.raises((ConnectionError, TimeoutError, socket.timeout)):
                recv_json(sock)
    finally:
        master.stop()
        time.sleep(0.2)


def test_aggressive_request_id_with_special_characters_is_rejected(runtime_api):
    invalid_message = {
        "type": "request_help",
        "request_id": "550e8400-e29b-41d4-a716-446655440000\nINJECT",
        "payload": {
            "master_id": "A",
            "current_load": 150,
            "capacity": 100,
            "workers_needed": 1,
        },
    }

    assert_contract_invalid(runtime_api, invalid_message)


@pytest.mark.ct07
def test_aggressive_timeout_fallback_keeps_request_ids_isolated(runtime_api):
    run_timeout = require_callable(runtime_api, "run_timeout_fallback_flow")

    result = run_timeout(
        requester={"master_id": "A", "current_load": 150, "capacity": 100},
        neighbors=[
            {"master_id": "B", "behavior": "timeout"},
            {
                "master_id": "C",
                "behavior": "accept",
                "idle_workers": [
                    {"worker_id": "C1", "address": "127.0.0.1:5006"},
                ],
            },
        ],
        workers_needed=1,
        timeout_seconds=5,
    )

    assert result["configured_timeout_seconds"] == 5
    assert result["attempted_neighbors"] == ["B", "C"]
    assert result["timed_out_neighbors"] == ["B"]
    assert result["requests"][0]["request_id"] != result["requests"][1]["request_id"]

    accepted = result["response_accepted"]
    assert_message_type(accepted, "response_accepted")
    assert accepted["request_id"] == result["requests"][1]["request_id"]
