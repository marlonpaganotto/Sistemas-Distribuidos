"""Lightweight verification for Sprint 3 runtime modules."""

from __future__ import annotations

import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from master import DEFAULT_RELEASE_THRESHOLD, DEFAULT_SATURATION_THRESHOLD, MasterNode
from protocol import ProtocolError, build_message, parse_sprint3_message


def verify_protocol() -> None:
    request = build_message(
        "request_help",
        {"master_id": "A", "current_load": 150, "capacity": 100, "workers_needed": 2},
    )
    parsed = parse_sprint3_message({**request, "future_field": True})
    assert parsed.type == "request_help"

    try:
        parse_sprint3_message({"type": "REQUEST_HELP", "request_id": request["request_id"], "payload": {}})
    except ProtocolError:
        pass
    else:
        raise AssertionError("uppercase Sprint 3 type should fail")

    try:
        parse_sprint3_message({"type": "request_help", "request_id": request["request_id"], "payload": {}})
    except ProtocolError:
        pass
    else:
        raise AssertionError("missing payload fields should fail")


def verify_thresholds() -> None:
    master = MasterNode("A", "127.0.0.1:5000")
    assert master.saturation_threshold == DEFAULT_SATURATION_THRESHOLD
    assert master.release_threshold == DEFAULT_RELEASE_THRESHOLD
    assert master._workers_needed(101) == 1
    assert master._workers_needed(250) == 2


if __name__ == "__main__":
    verify_protocol()
    verify_thresholds()
    print("Sprint 3 runtime verification passed")
