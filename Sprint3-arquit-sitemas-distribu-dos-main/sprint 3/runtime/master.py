"""# Master Hybrid Node - Sprint 03 Production Release

Componente: no orquestrador hibrido do ecossistema P2P.
Versao: CEUB 2026 / Sprint 03 / Production-Ready.

Responsabilidades:
- Receber Workers locais e emprestados via TCP.
- Atuar como cliente e servidor M2M para negociacao de carga.
- Preservar compatibilidade com o protocolo legado da Sprint 02.
- Aplicar strict parsing, timeout de 5 segundos, locks e fechamento seguro.
"""

from __future__ import annotations

import argparse
import json
import logging
import socket
import threading
import time
from collections import deque
from dataclasses import dataclass
from typing import Any

try:
    from .protocol import (
        SOCKET_TIMEOUT_SECONDS,
        ProtocolError,
        build_message,
        configure_socket,
        log_event,
        make_logger,
        new_request_id,
        parse_address,
        parse_sprint3_message,
        recv_json,
        recv_line,
        send_json,
        send_line,
        SPRINT2_ACK,
        SPRINT2_NO_TASK,
        SPRINT2_NOK,
        SPRINT2_OK,
        SPRINT2_QUERY,
        SPRINT2_WORKER_ALIVE,
    )
except ImportError:  # pragma: no cover - permits direct execution.
    from protocol import (
        SOCKET_TIMEOUT_SECONDS,
        ProtocolError,
        build_message,
        configure_socket,
        log_event,
        make_logger,
        new_request_id,
        parse_address,
        parse_sprint3_message,
        recv_json,
        recv_line,
        send_json,
        send_line,
        SPRINT2_ACK,
        SPRINT2_NO_TASK,
        SPRINT2_NOK,
        SPRINT2_OK,
        SPRINT2_QUERY,
        SPRINT2_WORKER_ALIVE,
    )


DEFAULT_CAPACITY = 100
DEFAULT_SATURATION_THRESHOLD = 100
DEFAULT_RELEASE_THRESHOLD = 70
DEFAULT_MASTER_PORT = 10000
SERVER_BACKLOG = 20


@dataclass
class WorkerRecord:
    worker_id: str
    address: str
    sock: socket.socket | None = None
    idle: bool = True
    temporary: bool = False
    original_master_address: str | None = None
    offering_master_address: str | None = None
    request_id: str | None = None
    server_uuid: str | None = None


class MasterNode:
    """Dual-role Master: TCP server for peers/workers and M2M client."""

    def __init__(
        self,
        master_id: str,
        address: str,
        neighbors: list[str] | None = None,
        *,
        capacity: int = DEFAULT_CAPACITY,
        saturation_threshold: int = DEFAULT_SATURATION_THRESHOLD,
        release_threshold: int = DEFAULT_RELEASE_THRESHOLD,
    ) -> None:
        self.master_id = master_id
        self.address = address
        self.host, self.port = parse_address(address)
        self.neighbors = list(neighbors or [])
        self.capacity = capacity
        self.saturation_threshold = saturation_threshold
        self.release_threshold = release_threshold

        self.tasks: deque[Any] = deque()
        self.workers: dict[str, WorkerRecord] = {}
        self.borrowed_workers: dict[str, WorkerRecord] = {}
        self.lock = threading.RLock()

        self._server: socket.socket | None = None
        self._stop = threading.Event()
        self.logger = make_logger(f"master.{master_id}")

    @property
    def current_load(self) -> int:
        with self.lock:
            return len(self.tasks)

    def add_task(self, task: Any) -> None:
        with self.lock:
            self.tasks.append(task)

    def start(self) -> None:
        server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server.settimeout(SOCKET_TIMEOUT_SECONDS)
        server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server.bind((self.host, self.port))
        server.listen(SERVER_BACKLOG)
        self._server = server

        thread = threading.Thread(target=self._accept_loop, name=f"master-{self.master_id}", daemon=True)
        thread.start()
        log_event(self.logger, logging.INFO, f"listening on {self.address}", message_type="server")

    def stop(self) -> None:
        self._stop.set()
        if self._server is not None:
            _close_socket(self._server)
            self._server = None

    def run_forever(self) -> None:
        self.start()
        try:
            while not self._stop.is_set():
                self.evaluate_load()
                time.sleep(1)
        finally:
            self.stop()

    def evaluate_load(self) -> None:
        load = self.current_load
        if load > self.saturation_threshold:
            self.request_help_from_neighbors(self._workers_needed(load))
        elif load < self.release_threshold:
            self.release_borrowed_workers()

    def request_help_from_neighbors(self, workers_needed: int) -> dict[str, Any] | None:
        request_id = new_request_id()
        request = build_message(
            "request_help",
            {
                "master_id": self.master_id,
                "current_load": self.current_load,
                "capacity": self.capacity,
                "workers_needed": workers_needed,
                "master_address": self.address,
            },
            request_id=request_id,
        )

        for neighbor in self._neighbors_snapshot():
            try:
                response = self._send_m2m(neighbor, request)
                parsed = parse_sprint3_message(response)
                if parsed.request_id != request_id:
                    self._log_request_mismatch(parsed.type, parsed.request_id)
                    continue
                if parsed.type == "response_accepted":
                    log_event(
                        self.logger,
                        logging.INFO,
                        f"help accepted by {neighbor}",
                        message_type=parsed.type,
                        request_id=request_id,
                    )
                    return parsed.raw
                log_event(
                    self.logger,
                    logging.INFO,
                    f"help rejected by {neighbor}: {parsed.payload.get('reason')}",
                    message_type=parsed.type,
                    request_id=request_id,
                )
            except (TimeoutError, socket.timeout):
                log_event(
                    self.logger,
                    logging.WARNING,
                    f"neighbor timed out after {SOCKET_TIMEOUT_SECONDS:g}s: {neighbor}",
                    message_type="request_help",
                    request_id=request_id,
                )
            except (ConnectionError, OSError, ProtocolError, ValueError) as exc:
                log_event(
                    self.logger,
                    logging.ERROR,
                    f"neighbor failed: {neighbor}: {exc}",
                    message_type="request_help",
                    request_id=request_id,
                )
        return None

    def release_borrowed_workers(self) -> None:
        with self.lock:
            borrowed = list(self.borrowed_workers.values())

        for worker in borrowed:
            if not worker.original_master_address:
                continue
            request_id = worker.request_id or new_request_id()
            try:
                release = build_message(
                    "command_release",
                    {"original_master_address": worker.original_master_address},
                    request_id=request_id,
                )
                if worker.sock is not None:
                    send_json(worker.sock, release)
                self._notify_worker_returned(worker, request_id)
                self._forget_worker(worker.worker_id)
                log_event(
                    self.logger,
                    logging.INFO,
                    f"released borrowed worker {worker.worker_id}",
                    message_type="command_release",
                    request_id=request_id,
                )
            except (ConnectionError, OSError, ProtocolError, ValueError) as exc:
                log_event(
                    self.logger,
                    logging.ERROR,
                    f"failed to release worker {worker.worker_id}: {exc}",
                    message_type="command_release",
                    request_id=request_id,
                )

    def redirect_worker(self, worker: WorkerRecord, target_master_address: str, request_id: str) -> None:
        if worker.sock is None:
            return

        redirect = build_message(
            "command_redirect",
            {"new_master_address": target_master_address, "original_master_address": self.address},
            request_id=request_id,
        )
        send_json(worker.sock, redirect)
        with self.lock:
            worker.idle = False
        log_event(
            self.logger,
            logging.INFO,
            f"redirected worker {worker.worker_id}",
            message_type="command_redirect",
            request_id=request_id,
        )

    def _accept_loop(self) -> None:
        assert self._server is not None
        while not self._stop.is_set():
            try:
                client, _ = self._server.accept()
                configure_socket(client)
                threading.Thread(target=self._handle_connection, args=(client,), daemon=True).start()
            except socket.timeout:
                continue
            except OSError:
                if not self._stop.is_set():
                    log_event(self.logger, logging.ERROR, "accept failed", message_type="server")

    def _handle_connection(self, client: socket.socket) -> None:
        should_close = True
        try:
            first_line = recv_line(client)
            if first_line in {SPRINT2_WORKER_ALIVE, "ALIVE"}:
                should_close = False
                self._handle_worker_line_protocol(client)
                return

            data = _decode_json_line(first_line)
            if _is_legacy_worker_handshake(data):
                self._handle_worker_json_cycle(client, data)
                return

            message = parse_sprint3_message(data)
            should_close = self._handle_sprint3_message(client, message)
        except (json.JSONDecodeError, ProtocolError, ConnectionError, OSError, ValueError) as exc:
            log_event(self.logger, logging.ERROR, f"invalid connection: {exc}", message_type="invalid")
        finally:
            if should_close:
                _close_socket(client)

    def _handle_sprint3_message(self, client: socket.socket, message: Any) -> bool:
        log_event(
            self.logger,
            logging.INFO,
            "received Sprint 3 message",
            message_type=message.type,
            request_id=message.request_id,
        )

        if message.type == "request_help":
            send_json(client, self._handle_request_help(message))
            return True
        if message.type == "register_temporary_worker":
            self._register_temporary_worker(client, message)
            self._handle_worker_line_protocol(client, worker_id=message.payload["worker_id"])
            return False
        if message.type == "notify_worker_returned":
            self._mark_worker_returned(message.payload["worker_id"])
            return True

        log_event(
            self.logger,
            logging.WARNING,
            f"unsupported inbound type: {message.type}",
            message_type=message.type,
            request_id=message.request_id,
        )
        return True

    def _handle_request_help(self, message: Any) -> dict[str, Any]:
        if self.current_load > self.saturation_threshold:
            return build_message("response_rejected", {"reason": "high_load"}, request_id=message.request_id)

        selected = self._reserve_idle_local_workers(message.payload["workers_needed"])
        if not selected:
            return build_message("response_rejected", {"reason": "no_workers_available"}, request_id=message.request_id)

        details = [{"worker_id": worker.worker_id, "address": worker.address} for worker in selected]
        response = build_message(
            "response_accepted",
            {"workers_offered": len(selected), "worker_details": details},
            request_id=message.request_id,
        )

        saturated_address = message.payload.get("master_address")
        if isinstance(saturated_address, str):
            for worker in selected:
                self.redirect_worker(worker, saturated_address, message.request_id)
        return response

    def _handle_worker_line_protocol(self, client: socket.socket, worker_id: str | None = None) -> None:
        worker_id = worker_id or f"worker-{id(client)}"
        self._upsert_worker(WorkerRecord(worker_id=worker_id, address="connected", sock=client))
        log_event(self.logger, logging.INFO, f"worker connected: {worker_id}", message_type=SPRINT2_WORKER_ALIVE)

        try:
            while not self._stop.is_set():
                command = recv_line(client)
                if command == SPRINT2_QUERY:
                    task = self._next_task()
                    send_line(client, SPRINT2_NO_TASK if task is None else str(task))
                    continue
                if command in {SPRINT2_OK, SPRINT2_NOK} or command.startswith("STATUS="):
                    send_line(client, SPRINT2_ACK)
                    continue
                log_event(self.logger, logging.ERROR, f"unknown Sprint 2 command: {command}", message_type=command)
                break
        except (ConnectionError, OSError):
            pass
        finally:
            self._forget_worker(worker_id)
            _close_socket(client)

    def _handle_worker_json_cycle(self, client: socket.socket, handshake: dict[str, Any]) -> None:
        worker_id = str(handshake.get("WORKER_UUID") or f"worker-{id(client)}")
        server_uuid = handshake.get("SERVER_UUID")
        self._upsert_worker(
            WorkerRecord(
                worker_id=worker_id,
                address="connected",
                sock=client,
                temporary=bool(server_uuid),
                server_uuid=str(server_uuid) if server_uuid else None,
            )
        )
        log_event(self.logger, logging.INFO, f"legacy worker connected: {worker_id}", message_type="ALIVE")

        try:
            task = self._next_task()
            if task is None:
                send_json(client, {"TASK": SPRINT2_NO_TASK})
                return
            send_json(client, task if isinstance(task, dict) else {"TASK": SPRINT2_QUERY, "VALUE": task})

            status_payload = recv_json(client)
            status = str(status_payload.get("STATUS", "")).upper()
            if status not in {SPRINT2_OK, SPRINT2_NOK}:
                raise ProtocolError("legacy worker status must be OK or NOK")
            send_json(client, {"STATUS": SPRINT2_ACK, "WORKER_UUID": worker_id})
        finally:
            self._forget_worker(worker_id)

    def _register_temporary_worker(self, client: socket.socket, message: Any) -> None:
        worker_id = message.payload["worker_id"]
        original_master_address = message.payload["original_master_address"]
        record = WorkerRecord(
            worker_id=worker_id,
            address="connected",
            sock=client,
            temporary=True,
            original_master_address=original_master_address,
            server_uuid=original_master_address,
            request_id=message.request_id,
        )
        with self.lock:
            self.workers[worker_id] = record
            self.borrowed_workers[worker_id] = record
        log_event(
            self.logger,
            logging.INFO,
            f"temporary worker registered: {worker_id}",
            message_type="register_temporary_worker",
            request_id=message.request_id,
        )

    def _notify_worker_returned(self, worker: WorkerRecord, request_id: str) -> None:
        if not worker.offering_master_address:
            return
        notice = build_message("notify_worker_returned", {"worker_id": worker.worker_id}, request_id=request_id)
        self._send_m2m(worker.offering_master_address, notice, expect_response=False)

    def _send_m2m(
        self,
        address: str,
        message: dict[str, Any],
        *,
        expect_response: bool = True,
    ) -> dict[str, Any] | None:
        host, port = parse_address(address)
        with configure_socket(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as sock:
            sock.connect((host, port))
            send_json(sock, message)
            return recv_json(sock) if expect_response else None

    def _workers_needed(self, load: int) -> int:
        overload = max(0, load - self.capacity)
        return max(1, (overload + self.capacity - 1) // self.capacity)

    def _next_task(self) -> Any | None:
        with self.lock:
            return self.tasks.popleft() if self.tasks else None

    def _reserve_idle_local_workers(self, count: int) -> list[WorkerRecord]:
        selected: list[WorkerRecord] = []
        with self.lock:
            for worker in self.workers.values():
                if worker.idle and not worker.temporary:
                    worker.idle = False
                    selected.append(worker)
                    if len(selected) == count:
                        break
        return selected

    def _upsert_worker(self, worker: WorkerRecord) -> None:
        with self.lock:
            existing = self.workers.get(worker.worker_id)
            if existing is None:
                self.workers[worker.worker_id] = worker
                return
            existing.sock = worker.sock
            existing.address = worker.address
            existing.temporary = worker.temporary
            existing.server_uuid = worker.server_uuid

    def _forget_worker(self, worker_id: str) -> None:
        with self.lock:
            self.workers.pop(worker_id, None)
            self.borrowed_workers.pop(worker_id, None)

    def _mark_worker_returned(self, worker_id: str) -> None:
        with self.lock:
            worker = self.workers.get(worker_id)
            if worker is not None:
                worker.temporary = False
                worker.idle = True

    def _neighbors_snapshot(self) -> list[str]:
        with self.lock:
            return list(self.neighbors)

    def _log_request_mismatch(self, message_type: str, request_id: str) -> None:
        log_event(
            self.logger,
            logging.ERROR,
            "discarded response with mismatched request_id",
            message_type=message_type,
            request_id=request_id,
        )


def _decode_json_line(first_line: str) -> dict[str, Any]:
    data = json.loads(first_line)
    if not isinstance(data, dict):
        raise ProtocolError("first JSON line must be an object")
    return data


def _is_legacy_worker_handshake(data: dict[str, Any]) -> bool:
    return str(data.get("WORKER", "")).upper() == "ALIVE"


def _close_socket(sock: socket.socket) -> None:
    try:
        sock.shutdown(socket.SHUT_RDWR)
    except OSError:
        pass
    finally:
        sock.close()


def _get_local_ip() -> str:
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        try:
            s.connect(("8.8.8.8", 80))
            return s.getsockname()[0]
        except OSError:
            return "127.0.0.1"


def main() -> None:
    parser = argparse.ArgumentParser(description="Sprint 3 dual-role Master")
    parser.add_argument("--id", required=True, dest="master_id")
    parser.add_argument("--address", required=True)
    parser.add_argument("--neighbor", action="append", default=[])
    args = parser.parse_args()
    _, port = parse_address(args.address)
    local_ip = _get_local_ip()
    print(f"Master {args.master_id} iniciando...")
    print(f"  IP local:  {local_ip}")
    print(f"  Porta:     {port}")
    print(f"  Endereco:  {local_ip}:{port}")
    print(f"  Workers devem conectar em: {local_ip}:{port}")
    MasterNode(args.master_id, args.address, args.neighbor).run_forever()


if __name__ == "__main__":
    main()
