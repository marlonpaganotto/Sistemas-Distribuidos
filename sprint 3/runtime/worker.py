"""# Resilient Worker Node - Sprint 03 Production Release

Componente: no processador resiliente do ecossistema P2P.
Versao: CEUB 2026 / Sprint 03 / Production-Ready.

Responsabilidades:
- Executar ciclo legado Sprint 02 com tokens em caixa alta.
- Atender `command_redirect` e registrar-se via `register_temporary_worker`.
- Atender `command_release` e retornar ao Master original.
- Recuperar autonomamente a rota original em falhas de socket.
"""

from __future__ import annotations

import argparse
import json
import logging
import socket
import time
from typing import Callable

try:
    from .protocol import (
        ProtocolError,
        build_message,
        configure_socket,
        log_event,
        make_logger,
        new_request_id,
        parse_address,
        parse_sprint3_message,
        recv_line,
        send_json,
        send_line,
        SPRINT2_ACK,
        SPRINT2_NOK,
        SPRINT2_NO_TASK,
        SPRINT2_OK,
        SPRINT2_QUERY,
        SPRINT2_WORKER_ALIVE,
    )
except ImportError:  # pragma: no cover - permits direct execution.
    from protocol import (
        ProtocolError,
        build_message,
        configure_socket,
        log_event,
        make_logger,
        new_request_id,
        parse_address,
        parse_sprint3_message,
        recv_line,
        send_json,
        send_line,
        SPRINT2_ACK,
        SPRINT2_NOK,
        SPRINT2_NO_TASK,
        SPRINT2_OK,
        SPRINT2_QUERY,
        SPRINT2_WORKER_ALIVE,
    )


RETRY_DELAY_SECONDS = 1.0


class WorkerNode:
    """Worker state machine with redirect, release and autonomous recovery."""

    def __init__(
        self,
        worker_id: str,
        master_address: str,
        *,
        task_handler: Callable[[str], bool] | None = None,
    ) -> None:
        self.worker_id = worker_id
        self.master_address = master_address
        self.original_master_address = master_address
        self.current_master_address = master_address
        self.borrowed = False
        self.task_handler = task_handler or self._default_task_handler
        self.logger = make_logger(f"worker.{worker_id}")

    def run_forever(self) -> None:
        while True:
            try:
                self.run_once()
            except (ConnectionError, OSError, TimeoutError, ProtocolError) as exc:
                self._handle_master_failure(exc)
                time.sleep(RETRY_DELAY_SECONDS)

    def run_once(self) -> None:
        host, port = parse_address(self.current_master_address)
        with configure_socket(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as sock:
            sock.connect((host, port))
            self._send_handshake(sock)
            self._work_loop(sock)

    def _send_handshake(self, sock: socket.socket) -> None:
        if self.borrowed:
            request_id = new_request_id()
            registration = build_message(
                "register_temporary_worker",
                {
                    "worker_id": self.worker_id,
                    "original_master_address": self.original_master_address,
                },
                request_id=request_id,
            )
            send_json(sock, registration)
            log_event(
                self.logger,
                logging.INFO,
                "registered as temporary worker",
                message_type="register_temporary_worker",
                request_id=request_id,
            )
            return

        send_line(sock, SPRINT2_WORKER_ALIVE)
        log_event(self.logger, logging.INFO, "sent legacy alive", message_type="ALIVE")

    def _work_loop(self, sock: socket.socket) -> None:
        while True:
            send_line(sock, SPRINT2_QUERY)
            response = self._read_task_or_control_message(sock)

            if response == SPRINT2_NO_TASK:
                time.sleep(RETRY_DELAY_SECONDS)
                continue
            if response in {"command_redirect", "command_release"}:
                return

            status = SPRINT2_OK if self.task_handler(response) else SPRINT2_NOK
            send_line(sock, status)
            ack = recv_line(sock)
            if ack != SPRINT2_ACK:
                raise ProtocolError(f"expected ACK, got {ack!r}")

    def _read_task_or_control_message(self, sock: socket.socket) -> str:
        line = recv_line(sock)
        if not line.startswith("{"):
            return line

        parsed = parse_sprint3_message(json.loads(line))
        if parsed.type == "command_redirect":
            self._apply_redirect(parsed.payload["new_master_address"], parsed.payload["original_master_address"])
            log_event(
                self.logger,
                logging.INFO,
                "redirected to temporary master",
                message_type="command_redirect",
                request_id=parsed.request_id,
            )
            return parsed.type
        if parsed.type == "command_release":
            self._apply_release(parsed.payload["original_master_address"])
            log_event(
                self.logger,
                logging.INFO,
                "released back to original master",
                message_type="command_release",
                request_id=parsed.request_id,
            )
            return parsed.type

        raise ProtocolError(f"unexpected worker control type: {parsed.type}")

    def _apply_redirect(self, new_master_address: str, original_master_address: str) -> None:
        self.original_master_address = original_master_address
        self.current_master_address = new_master_address
        self.borrowed = True

    def _apply_release(self, original_master_address: str) -> None:
        self.current_master_address = original_master_address
        self.original_master_address = original_master_address
        self.borrowed = False

    def _handle_master_failure(self, exc: Exception) -> None:
        log_event(self.logger, logging.WARNING, f"master unavailable: {exc}", message_type="recovery")
        if self.borrowed:
            self.current_master_address = self.original_master_address
            self.borrowed = False

    @staticmethod
    def _default_task_handler(_task: str) -> bool:
        return True


def main() -> None:
    parser = argparse.ArgumentParser(description="Sprint 3 resilient Worker")
    parser.add_argument("--id", required=True, dest="worker_id")
    parser.add_argument("--master", required=True, dest="master_address")
    args = parser.parse_args()
    WorkerNode(args.worker_id, args.master_address).run_forever()


if __name__ == "__main__":
    main()
