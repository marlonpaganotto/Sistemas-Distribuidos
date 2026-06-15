"""
Worker P2P - Equipe 5 - Arquitetura de Sistemas Distribuidos
Sprints 01, 02 e 03

Implementa:
- Heartbeat periodico para o Master atual (Sprint 01)
- Apresentacao e ciclo de tarefas (Sprint 02)
- Execucao simulada de QUERY (Sprint 02)
- Recebimento de command_redirect e command_release (Sprint 03)
- Registro como Worker temporario quando emprestado (Sprint 03)

Somente biblioteca padrao do Python.
"""

from __future__ import annotations

import argparse
import json
import random
import socket
import threading
import time
import uuid
from datetime import datetime
from typing import Any, Optional


TASK_HEARTBEAT = "HEARTBEAT"
TASK_QUERY = "QUERY"
TASK_NO_TASK = "NO_TASK"
RESPONSE_ALIVE = "ALIVE"
STATUS_ACK = "ACK"
STATUS_OK = "OK"
WORKER_ALIVE = "ALIVE"

TYPE_COMMAND_REDIRECT = "command_redirect"
TYPE_REGISTER_TEMPORARY_WORKER = "register_temporary_worker"
TYPE_COMMAND_RELEASE = "command_release"

SOCKET_TIMEOUT = 5


# ============================================================
# UTILITARIOS
# ============================================================


def now() -> str:
    return datetime.now().strftime("%H:%M:%S")


def log(worker_id: str, message: str) -> None:
    print(f"[{now()}][WORKER {worker_id}] {message}", flush=True)


def parse_address(address: str) -> tuple[str, int]:
    if ":" not in address:
        raise ValueError(f"Endereco invalido: {address}. Use host:porta.")
    host, port_text = address.rsplit(":", 1)
    return host, int(port_text)


def send_json_line(stream, payload: dict[str, Any]) -> None:
    message = json.dumps(payload, ensure_ascii=False) + "\n"
    stream.write(message.encode("utf-8"))
    stream.flush()


def recv_json_line(stream) -> Optional[dict[str, Any]]:
    line = stream.readline()
    if not line:
        return None
    return json.loads(line.decode("utf-8"))


def make_request_id() -> str:
    return str(uuid.uuid4())


# ============================================================
# WORKER
# ============================================================


class WorkerNode:
    def __init__(
        self,
        worker_id: str,
        original_master_id: str,
        original_master_address: str,
        command_host: str,
        command_port: int,
        advertised_host: str,
        interval: float,
        min_task_seconds: float,
        max_task_seconds: float,
    ) -> None:
        self.worker_id = worker_id
        self.original_master_id = original_master_id
        self.original_master_address = original_master_address
        self.current_master_address = original_master_address
        self.current_master_id = original_master_id

        self.command_host = command_host
        self.command_port = command_port
        self.advertised_host = advertised_host
        self.worker_address = f"{advertised_host}:{command_port}"

        self.interval = interval
        self.min_task_seconds = min_task_seconds
        self.max_task_seconds = max_task_seconds

        self.borrowed = False
        self.temporary_registered_address: Optional[str] = None
        self.stop_event = threading.Event()
        self.command_lock = threading.RLock()
        self.pending_command: Optional[dict[str, Any]] = None

    # --------------------------------------------------------
    # Servidor de comandos do Worker - Sprint 03
    # --------------------------------------------------------

    def start_command_server(self) -> threading.Thread:
        thread = threading.Thread(target=self.command_server_loop, daemon=True)
        thread.start()
        return thread

    def command_server_loop(self) -> None:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as server:
            server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            server.bind((self.command_host, self.command_port))
            server.listen(10)
            server.settimeout(1)

            log(self.worker_id, f"Servidor de comandos ativo em {self.command_host}:{self.command_port}")

            while not self.stop_event.is_set():
                try:
                    conn, addr = server.accept()
                except socket.timeout:
                    continue

                thread = threading.Thread(target=self.handle_command_connection, args=(conn, addr), daemon=True)
                thread.start()

    def handle_command_connection(self, conn: socket.socket, addr: tuple[str, int]) -> None:
        conn.settimeout(SOCKET_TIMEOUT)
        try:
            with conn:
                with conn.makefile("rwb") as stream:
                    message = recv_json_line(stream)
                    if not isinstance(message, dict):
                        return

                    msg_type = message.get("type")
                    if msg_type not in {TYPE_COMMAND_REDIRECT, TYPE_COMMAND_RELEASE}:
                        log(self.worker_id, f"Comando desconhecido ignorado de {addr}: type={msg_type}")
                        return

                    with self.command_lock:
                        self.pending_command = message

                    send_json_line(stream, {
                        "STATUS": STATUS_ACK,
                        "WORKER_UUID": self.worker_id,
                        "request_id": message.get("request_id"),
                    })
                    log(self.worker_id, f"Comando recebido e enfileirado: type={msg_type}")
        except Exception as error:
            log(self.worker_id, f"Erro no servidor de comandos: {error}")

    def take_pending_command(self) -> Optional[dict[str, Any]]:
        with self.command_lock:
            command = self.pending_command
            self.pending_command = None
            return command

    def apply_command(self, command: dict[str, Any]) -> None:
        msg_type = command.get("type")
        payload = command.get("payload", {})

        if msg_type == TYPE_COMMAND_REDIRECT:
            new_master_address = payload.get("new_master_address")
            new_master_id = payload.get("new_master_id") or "BORROWER"
            if not isinstance(new_master_address, str):
                log(self.worker_id, f"command_redirect invalido: {command}")
                return

            self.current_master_address = new_master_address
            self.current_master_id = str(new_master_id)
            self.borrowed = True
            self.temporary_registered_address = None
            log(self.worker_id, f"Redirecionado para Master {self.current_master_id}@{self.current_master_address}")
            return

        if msg_type == TYPE_COMMAND_RELEASE:
            original_master_address = payload.get("original_master_address") or self.original_master_address
            original_master_id = payload.get("original_master_id") or self.original_master_id

            self.current_master_address = str(original_master_address)
            self.current_master_id = str(original_master_id)
            self.borrowed = False
            self.temporary_registered_address = None
            log(self.worker_id, f"Liberado para retornar ao Master original {self.current_master_id}@{self.current_master_address}")
            return

    # --------------------------------------------------------
    # Cliente Worker -> Master - Sprints 01, 02 e 03
    # --------------------------------------------------------

    def start(self) -> None:
        command_thread = self.start_command_server()

        log(self.worker_id, f"WORKER_UUID={self.worker_id}")
        log(self.worker_id, f"Master original={self.original_master_id}@{self.original_master_address}")
        log(self.worker_id, f"Endereco de comandos={self.worker_address}")
        log(self.worker_id, "Pressione Ctrl+C para encerrar.")

        try:
            while not self.stop_event.is_set():
                command = self.take_pending_command()
                if command:
                    self.apply_command(command)

                host, port = parse_address(self.current_master_address)
                try:
                    with socket.create_connection((host, port), timeout=SOCKET_TIMEOUT) as client:
                        client.settimeout(SOCKET_TIMEOUT)
                        log(self.worker_id, f"Conectado ao Master atual {self.current_master_id}@{self.current_master_address}")

                        with client.makefile("rwb") as stream:
                            if self.borrowed and self.temporary_registered_address != self.current_master_address:
                                self.register_temporary_worker(stream)
                                self.temporary_registered_address = self.current_master_address
                                continue
                            self.worker_master_loop(stream)

                except KeyboardInterrupt:
                    raise
                except Exception as error:
                    log(self.worker_id, f"Conexao com Master falhou: {error}")
                    if self.borrowed:
                        log(self.worker_id, "Retornando ao Master original por falha do Master temporario.")
                        self.current_master_address = self.original_master_address
                        self.current_master_id = self.original_master_id
                        self.borrowed = False
                        self.temporary_registered_address = None
                    time.sleep(self.interval)

        except KeyboardInterrupt:
            log(self.worker_id, "Encerramento solicitado pelo terminal.")
        finally:
            self.stop_event.set()
            command_thread.join(timeout=2)
            log(self.worker_id, "Worker encerrado.")

    def worker_master_loop(self, stream) -> None:
        while not self.stop_event.is_set():
            command = self.take_pending_command()
            if command:
                self.apply_command(command)
                return

            self.send_heartbeat(stream)

            command = self.take_pending_command()
            if command:
                self.apply_command(command)
                return

            self.request_task_or_command(stream)
            time.sleep(self.interval)

    def send_heartbeat(self, stream) -> None:
        payload = {"SERVER_UUID": self.current_master_id, "TASK": TASK_HEARTBEAT}
        send_json_line(stream, payload)
        log(self.worker_id, f"Heartbeat enviado para {self.current_master_id}")

        response = recv_json_line(stream)
        if response is None:
            raise ConnectionError("Master nao respondeu ao heartbeat")

        if response.get("TASK") != TASK_HEARTBEAT or response.get("RESPONSE") != RESPONSE_ALIVE:
            raise ValueError(f"Resposta invalida de heartbeat: {response}")

        log(self.worker_id, "ALIVE recebido")

    def request_task_or_command(self, stream) -> None:
        payload: dict[str, Any] = {
            "WORKER": WORKER_ALIVE,
            "WORKER_UUID": self.worker_id,
            "WORKER_HOST": self.advertised_host,
            "WORKER_PORT": self.command_port,
        }

        if self.borrowed:
            payload["SERVER_UUID"] = self.original_master_id

        send_json_line(stream, payload)
        log(self.worker_id, f"Apresentacao enviada (borrowed={self.borrowed})")

        response = recv_json_line(stream)
        if response is None:
            raise ConnectionError("Master encerrou sem responder a apresentacao")

        if "type" in response:
            self.apply_command(response)
            return

        task_type = response.get("TASK")
        if task_type == TASK_NO_TASK:
            log(self.worker_id, "Master informou fila vazia")
            return

        if task_type == TASK_QUERY:
            self.process_query(response, stream)
            return

        raise ValueError(f"Resposta de tarefa invalida: {response}")

    def process_query(self, response: dict[str, Any], stream) -> None:
        user = response.get("USER", "UNKNOWN")
        duration = random.uniform(self.min_task_seconds, self.max_task_seconds)
        log(self.worker_id, f"Processando QUERY USER={user} por {duration:.2f}s")
        time.sleep(duration)

        status_payload = {"STATUS": STATUS_OK, "TASK": TASK_QUERY, "WORKER_UUID": self.worker_id}
        send_json_line(stream, status_payload)
        log(self.worker_id, f"STATUS OK enviado para tarefa USER={user}")

        ack = recv_json_line(stream)
        if ack is None:
            raise ConnectionError("Master nao enviou ACK apos STATUS")
        if ack.get("STATUS") != STATUS_ACK:
            raise ValueError(f"ACK invalido: {ack}")

        log(self.worker_id, "ACK final recebido")

    def register_temporary_worker(self, stream) -> None:
        payload = {
            "type": TYPE_REGISTER_TEMPORARY_WORKER,
            "request_id": make_request_id(),
            "payload": {
                "worker_id": self.worker_id,
                "original_master_address": self.original_master_address,
                "original_master_id": self.original_master_id,
                "worker_address": self.worker_address,
            },
        }
        send_json_line(stream, payload)
        log(self.worker_id, f"register_temporary_worker enviado para {self.current_master_id}")

        ack = recv_json_line(stream)
        if ack is None:
            raise ConnectionError("Master temporario nao confirmou registro")
        if ack.get("STATUS") != STATUS_ACK:
            raise ValueError(f"ACK invalido no registro temporario: {ack}")
        log(self.worker_id, "Registro temporario confirmado")


# ============================================================
# CLI
# ============================================================


def main() -> None:
    parser = argparse.ArgumentParser(description="Worker P2P - Equipe 5 - Sprints 01-03")
    parser.add_argument("--worker-id", required=True, help="Identificador unico do Worker. Ex: equipe5_W1")
    parser.add_argument("--master-id", required=True, help="Master original do Worker. Ex: equipe5_A")
    parser.add_argument("--master-address", required=True, help="Endereco do Master original. Ex: 127.0.0.1:5000")
    parser.add_argument("--command-host", default="127.0.0.1", help="Host em que o Worker escuta comandos")
    parser.add_argument("--command-port", type=int, required=True, help="Porta TCP de comandos do Worker")
    parser.add_argument("--advertised-host", default="127.0.0.1", help="Host anunciado ao Master")
    parser.add_argument("--interval", type=float, default=2.0, help="Intervalo entre ciclos (segundos)")
    parser.add_argument("--min-task-seconds", type=float, default=1.0, help="Tempo minimo de processamento simulado")
    parser.add_argument("--max-task-seconds", type=float, default=3.0, help="Tempo maximo de processamento simulado")

    args = parser.parse_args()
    parse_address(args.master_address)

    worker = WorkerNode(
        worker_id=args.worker_id,
        original_master_id=args.master_id,
        original_master_address=args.master_address,
        command_host=args.command_host,
        command_port=args.command_port,
        advertised_host=args.advertised_host,
        interval=args.interval,
        min_task_seconds=args.min_task_seconds,
        max_task_seconds=args.max_task_seconds,
    )
    worker.start()


if __name__ == "__main__":
    main()
