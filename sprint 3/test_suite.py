"""# Sprint 03 Integration Test Suite - Production Release

Projeto: P2P com Balanceamento de Carga Dinamico (CEUB 2026)
Componente: Harness automatizado CT01-CT09

Execute:
    python "sprint 3/test_suite.py"
"""

from __future__ import annotations

import socket
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable
from uuid import UUID


SPRINT_ROOT = Path(__file__).resolve().parent
RUNTIME_ROOT = SPRINT_ROOT / "runtime"
if str(RUNTIME_ROOT) not in sys.path:
    sys.path.insert(0, str(RUNTIME_ROOT))

import harness_api as api  # noqa: E402
from master import MasterNode  # noqa: E402
from protocol import recv_json  # noqa: E402


GREEN = "\033[92m"
RED = "\033[91m"
CYAN = "\033[96m"
BOLD = "\033[1m"
RESET = "\033[0m"

HOST = "127.0.0.1"
PORT_BASE = 5001
SOCKET_TIMEOUT_SECONDS = 5


@dataclass(frozen=True)
class TestCase:
    id: str
    name: str
    run: Callable[[], None]


def assert_message_type(message: dict[str, Any], expected_type: str) -> None:
    """Garante que a mensagem respeita `type`, `payload` e `request_id` UUID v4."""
    assert isinstance(message, dict), "message must be a dict"
    assert message.get("type") == expected_type, f"expected type={expected_type}"
    assert isinstance(message.get("payload"), dict), "payload must be a dict"
    assert_uuid_v4(message["request_id"])


def assert_uuid_v4(value: str) -> None:
    """Valida formato e versao do UUID usado para correlacao M2M."""
    parsed = UUID(value)
    assert parsed.version == 4, "request_id must be UUID v4"
    assert str(parsed) == value.lower(), "request_id must be canonical lowercase"


def assert_invalid_contract(message: dict[str, Any]) -> None:
    """Confirma que strict parsing rejeita mensagens invalidas sem aceitar lixo."""
    try:
        api.validate_message(message)
    except (AssertionError, TypeError, ValueError):
        return
    raise AssertionError("invalid message was accepted by strict parser")


def test_ct01_help_request_accepted() -> None:
    """CT01: Master B ocioso aceita ajuda e gera redirect para worker disponivel."""
    result = api.run_help_request_flow(
        requester={"master_id": "A", "current_load": 150, "capacity": 100, "address": f"{HOST}:5001"},
        neighbor={"master_id": "B", "current_load": 20, "capacity": 100, "address": f"{HOST}:5002"},
        workers_needed=2,
        idle_workers=[
            {"worker_id": "B1", "address": f"{HOST}:5003"},
            {"worker_id": "B2", "address": f"{HOST}:5004"},
        ],
    )
    request = result["request_help"]
    accepted = result["response_accepted"]
    redirect = result["command_redirect"]

    assert_message_type(request, "request_help")
    assert_message_type(accepted, "response_accepted")
    assert_message_type(redirect, "command_redirect")
    assert accepted["request_id"] == request["request_id"]
    assert redirect["request_id"] == request["request_id"]
    assert accepted["payload"]["workers_offered"] == 2


def test_ct02_help_request_rejected() -> None:
    """CT02: Master em alta carga recusa ajuda com reason=high_load."""
    result = api.run_help_request_flow(
        requester={"master_id": "A", "current_load": 150, "capacity": 100},
        neighbor={"master_id": "B", "current_load": 130, "capacity": 100},
        workers_needed=1,
        idle_workers=[{"worker_id": "B1", "address": f"{HOST}:5003"}],
    )
    rejected = result["response_rejected"]

    assert_message_type(rejected, "response_rejected")
    assert rejected["payload"] == {"reason": "high_load"}
    assert "command_redirect" not in result


def test_ct03_request_id_correlation() -> None:
    """CT03: requisicoes concorrentes mantem respostas vinculadas ao request_id original."""
    result = api.run_concurrent_help_requests(request_count=12, workers_per_neighbor=1)
    exchanges = result["exchanges"]
    request_ids = [exchange["request"]["request_id"] for exchange in exchanges]

    assert len(exchanges) == 12
    assert len(set(request_ids)) == len(request_ids)
    for exchange in exchanges:
        assert_message_type(exchange["request"], "request_help")
        assert exchange["response"]["request_id"] == exchange["request"]["request_id"]
        assert_uuid_v4(exchange["response"]["request_id"])


def test_ct04_temporary_worker_registration() -> None:
    """CT04: worker redirecionado registra origem para permitir retorno futuro."""
    result = api.run_borrowed_worker_task_cycle(
        worker_id="B1",
        request_id="550e8400-e29b-41d4-a716-446655440000",
        saturated_master_address=f"{HOST}:5001",
        original_master_address=f"{HOST}:5002",
        task={"task_id": "T-42", "payload": {"value": 21}},
        status="OK",
    )
    registration = result["register_temporary_worker"]

    assert_message_type(registration, "register_temporary_worker")
    assert registration["payload"] == {"worker_id": "B1", "original_master_address": f"{HOST}:5002"}
    assert api.validate_message(registration) is True


def test_ct05_borrowed_worker_task_cycle() -> None:
    """CT05: Master entrega QUERY, Worker reporta OK e Master finaliza com ACK."""
    result = api.run_borrowed_worker_task_cycle(
        worker_id="B1",
        request_id="550e8400-e29b-41d4-a716-446655440000",
        saturated_master_address=f"{HOST}:5001",
        original_master_address=f"{HOST}:5002",
        task={"task_id": "T-99", "payload": {"value": 42}},
        status="OK",
    )
    cycle = result["sprint2_cycle"]

    assert cycle["handshake"] == "WORKER=ALIVE"
    assert cycle["query"] == "QUERY"
    assert cycle["task"]["task_id"] == "T-99"
    assert cycle["status"] == "OK"
    assert cycle["ack"] == "ACK"


def test_ct06_hysteresis_release() -> None:
    """CT06: queda abaixo da histerese libera worker e notifica Master de origem."""
    result = api.run_release_flow(
        master_id="A",
        load_sequence=[101, 95, 70, 59],
        borrowed_worker={"worker_id": "B1", "original_master_address": f"{HOST}:5002"},
    )
    release = result["command_release"]
    notify = result["notify_worker_returned"]

    assert result["requested_help_at_loads"] == [101]
    assert result["release_events_before_threshold"] == []
    assert_message_type(release, "command_release")
    assert_message_type(notify, "notify_worker_returned")
    assert notify["request_id"] == release["request_id"]


def test_ct07_m2m_timeout_fallback() -> None:
    """CT07: no silencioso estoura 5s e o fluxo segue para o proximo vizinho."""
    result = api.run_timeout_fallback_flow(
        requester={"master_id": "A", "current_load": 150, "capacity": 100},
        neighbors=[
            {"master_id": "B", "behavior": "timeout"},
            {
                "master_id": "C",
                "behavior": "accept",
                "idle_workers": [{"worker_id": "C1", "address": f"{HOST}:5007"}],
            },
        ],
        workers_needed=1,
        timeout_seconds=SOCKET_TIMEOUT_SECONDS,
    )

    assert result["configured_timeout_seconds"] == SOCKET_TIMEOUT_SECONDS
    assert result["attempted_neighbors"] == ["B", "C"]
    assert result["timed_out_neighbors"] == ["B"]
    assert result["response_accepted"]["request_id"] == result["requests"][1]["request_id"]


def test_ct08_receiver_failure_recovery() -> None:
    """CT08: worker emprestado detecta queda do receptor e retorna ao Master original."""
    result = api.run_worker_recovery_flow(
        worker_id="B1",
        saturated_master_address=f"{HOST}:5001",
        original_master_address=f"{HOST}:5002",
        failure="saturated_master_down",
    )

    assert result["detected_failure"] is True
    assert result["stopped_retrying_saturated_master"] is True
    assert result["reconnected_address"] == f"{HOST}:5002"
    assert result["resumed_sprint2_cycle"]["handshake"] == "WORKER=ALIVE"


def test_ct09_unknown_type_and_malformed_tcp() -> None:
    """CT09: strict parsing rejeita type desconhecido e Master fecha JSON quebrado."""
    unknown_type = {
        "type": "unexpected_command",
        "request_id": "550e8400-e29b-41d4-a716-446655440000",
        "payload": {},
    }
    assert_invalid_contract(unknown_type)

    master = MasterNode("B", f"{HOST}:5009")
    master.start()
    time.sleep(0.2)
    sock: socket.socket | None = None
    try:
        sock = socket.create_connection((HOST, 5009), timeout=SOCKET_TIMEOUT_SECONDS)
        sock.settimeout(SOCKET_TIMEOUT_SECONDS)
        sock.sendall(b'{"type":"request_help","request_id":"not-a-uuid"\n')
        try:
            recv_json(sock)
        except (ConnectionError, TimeoutError, socket.timeout):
            return
        raise AssertionError("malformed TCP payload did not close or time out")
    finally:
        if sock is not None:
            sock.close()
        master.stop()
        time.sleep(0.2)


def build_suite() -> list[TestCase]:
    return [
        TestCase("CT01", "Pedido de ajuda aceito", test_ct01_help_request_accepted),
        TestCase("CT02", "Pedido de ajuda recusado", test_ct02_help_request_rejected),
        TestCase("CT03", "Correlacao de request_id", test_ct03_request_id_correlation),
        TestCase("CT04", "Registro temporario", test_ct04_temporary_worker_registration),
        TestCase("CT05", "Tarefa no Worker emprestado", test_ct05_borrowed_worker_task_cycle),
        TestCase("CT06", "Devolucao por histerese", test_ct06_hysteresis_release),
        TestCase("CT07", "Timeout M2M", test_ct07_m2m_timeout_fallback),
        TestCase("CT08", "Queda do receptor", test_ct08_receiver_failure_recovery),
        TestCase("CT09", "Tipo desconhecido", test_ct09_unknown_type_and_malformed_tcp),
    ]


def run_suite() -> int:
    suite = build_suite()
    passed = 0

    print(f"{BOLD}{CYAN}Sprint 03 Integration Test Suite - CT01 a CT09{RESET}")
    for case in suite:
        try:
            case.run()
        except Exception as exc:  # noqa: BLE001 - runner must report every failure cleanly.
            print(f"{RED}FAIL{RESET} {case.id} - {case.name}: {exc}")
        else:
            passed += 1
            print(f"{GREEN}PASS{RESET} {case.id} - {case.name}")

    total = len(suite)
    color = GREEN if passed == total else RED
    print(f"\n{BOLD}Resultado:{RESET} {color}{passed}/{total} testes passaram{RESET}")
    return 0 if passed == total else 1


if __name__ == "__main__":
    raise SystemExit(run_suite())
