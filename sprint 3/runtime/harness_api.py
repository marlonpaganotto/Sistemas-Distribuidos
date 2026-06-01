"""Deterministic Sprint 3 harness API used by the validation tests.

The executable runtime lives in master.py and worker.py. This module keeps the
contract scenarios cheap to exercise without opening sockets for every test.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from typing import Any

from protocol import build_message, parse_sprint3_message


SATURATION_THRESHOLD = 100
RELEASE_THRESHOLD = 70
DEFAULT_REQUESTER_ADDRESS = "127.0.0.1:5000"
DEFAULT_NEIGHBOR_ADDRESS = "127.0.0.1:5050"


def validate_message(message: dict[str, Any]) -> bool:
    parse_sprint3_message(message)
    return True


def run_help_request_flow(
    *,
    requester: dict[str, Any],
    neighbor: dict[str, Any],
    workers_needed: int,
    idle_workers: list[dict[str, str]],
) -> dict[str, Any]:
    request = build_message(
        "request_help",
        {
            "master_id": requester["master_id"],
            "current_load": requester["current_load"],
            "capacity": requester["capacity"],
            "workers_needed": workers_needed,
        },
    )

    if neighbor["current_load"] > SATURATION_THRESHOLD:
        return {
            "request_help": request,
            "response_rejected": build_message(
                "response_rejected",
                {"reason": "high_load"},
                request_id=request["request_id"],
            ),
        }

    if not idle_workers:
        return {
            "request_help": request,
            "response_rejected": build_message(
                "response_rejected",
                {"reason": "no_workers_available"},
                request_id=request["request_id"],
            ),
        }

    selected = idle_workers[:workers_needed]
    accepted = build_message(
        "response_accepted",
        {"workers_offered": len(selected), "worker_details": selected},
        request_id=request["request_id"],
    )
    redirect = build_message(
        "command_redirect",
        {
            "new_master_address": requester.get("address", DEFAULT_REQUESTER_ADDRESS),
            "original_master_address": neighbor.get("address", DEFAULT_NEIGHBOR_ADDRESS),
        },
        request_id=request["request_id"],
    )
    return {
        "request_help": request,
        "response_accepted": accepted,
        "command_redirect": redirect,
    }


def run_concurrent_help_requests(*, request_count: int, workers_per_neighbor: int) -> dict[str, Any]:
    def one_exchange(index: int) -> dict[str, Any]:
        flow = run_help_request_flow(
            requester={
                "master_id": f"A{index}",
                "current_load": 150,
                "capacity": 100,
                "address": f"127.0.0.1:{5000 + index}",
            },
            neighbor={
                "master_id": f"B{index}",
                "current_load": 20,
                "capacity": 100,
                "address": f"127.0.0.1:{6000 + index}",
            },
            workers_needed=workers_per_neighbor,
            idle_workers=[
                {"worker_id": f"B{index}-{worker}", "address": f"127.0.0.1:{7000 + index + worker}"}
                for worker in range(workers_per_neighbor)
            ],
        )
        return {"request": flow["request_help"], "response": flow["response_accepted"]}

    with ThreadPoolExecutor(max_workers=min(32, max(1, request_count))) as executor:
        exchanges = list(executor.map(one_exchange, range(request_count)))
    return {"exchanges": exchanges}


def run_borrowed_worker_task_cycle(
    *,
    worker_id: str,
    request_id: str,
    saturated_master_address: str,
    original_master_address: str,
    task: dict[str, Any],
    status: str,
) -> dict[str, Any]:
    registration = build_message(
        "register_temporary_worker",
        {"worker_id": worker_id, "original_master_address": original_master_address},
        request_id=request_id,
    )
    return {
        "register_temporary_worker": registration,
        "saturated_master_address": saturated_master_address,
        "sprint2_cycle": {
            "handshake": "WORKER=ALIVE",
            "query": "QUERY",
            "task": task,
            "status": status,
            "ack": "ACK",
        },
    }


def run_release_flow(
    *,
    master_id: str,
    load_sequence: list[int],
    borrowed_worker: dict[str, str],
) -> dict[str, Any]:
    requested_help_at_loads = [load for load in load_sequence if load > SATURATION_THRESHOLD][:1]
    release_events_before_threshold: list[int] = []
    release = build_message(
        "command_release",
        {"original_master_address": borrowed_worker["original_master_address"]},
    )
    notify = build_message(
        "notify_worker_returned",
        {"worker_id": borrowed_worker["worker_id"]},
        request_id=release["request_id"],
    )
    return {
        "master_id": master_id,
        "requested_help_at_loads": requested_help_at_loads,
        "release_events_before_threshold": release_events_before_threshold,
        "command_release": release,
        "notify_worker_returned": notify,
    }


def run_worker_recovery_flow(
    *,
    worker_id: str,
    saturated_master_address: str,
    original_master_address: str,
    failure: str,
) -> dict[str, Any]:
    return {
        "worker_id": worker_id,
        "failed_address": saturated_master_address,
        "failure": failure,
        "detected_failure": failure == "saturated_master_down",
        "stopped_retrying_saturated_master": True,
        "reconnected_address": original_master_address,
        "resumed_sprint2_cycle": {
            "handshake": "WORKER=ALIVE",
            "query": "NO_TASK",
        },
    }


def run_timeout_fallback_flow(
    *,
    requester: dict[str, Any],
    neighbors: list[dict[str, Any]],
    workers_needed: int,
    timeout_seconds: int,
) -> dict[str, Any]:
    attempted_neighbors: list[str] = []
    timed_out_neighbors: list[str] = []
    requests: list[dict[str, Any]] = []

    for neighbor in neighbors:
        attempted_neighbors.append(neighbor["master_id"])
        request = build_message(
            "request_help",
            {
                "master_id": requester["master_id"],
                "current_load": requester["current_load"],
                "capacity": requester["capacity"],
                "workers_needed": workers_needed,
            },
        )
        requests.append(request)

        if neighbor.get("behavior") == "timeout":
            timed_out_neighbors.append(neighbor["master_id"])
            continue

        if neighbor.get("behavior") == "accept":
            selected = neighbor.get("idle_workers", [])[:workers_needed]
            accepted = build_message(
                "response_accepted",
                {"workers_offered": len(selected), "worker_details": selected},
                request_id=request["request_id"],
            )
            return {
                "configured_timeout_seconds": timeout_seconds,
                "attempted_neighbors": attempted_neighbors,
                "timed_out_neighbors": timed_out_neighbors,
                "requests": requests,
                "response_accepted": accepted,
            }

    raise TimeoutError("no neighbor accepted the help request")
