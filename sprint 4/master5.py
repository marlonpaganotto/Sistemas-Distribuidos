"""
Master P2P - Equipe 5 - Arquitetura de Sistemas Distribuidos
Sprints 01, 02, 03 e 04

Implementa:
- Heartbeat Worker -> Master (Sprint 01)
- Ciclo de tarefas Worker <-> Master (Sprint 02)
- Negociacao Master -> Master para emprestimo de Workers (Sprint 03)
- Redirecionamento e devolucao dinamica de Workers (Sprint 03)
- Envio de relatorio de desempenho ao Supervisor via TLS (Sprint 04)

Dependencias: psutil  (pip install psutil)
Demais modulos: somente biblioteca padrao do Python.
"""

from __future__ import annotations

import argparse
import json
import math
import queue
import socket
import ssl
import threading
import time
import uuid
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional

try:
    import psutil
    _HAS_PSUTIL = True
except ImportError:
    _HAS_PSUTIL = False

# ============================================================
# CONSTANTES DO PROTOCOLO - SPRINTS 01 E 02
# ============================================================

TASK_HEARTBEAT = "HEARTBEAT"
TASK_QUERY = "QUERY"
TASK_NO_TASK = "NO_TASK"
RESPONSE_ALIVE = "ALIVE"
STATUS_ACK = "ACK"
STATUS_OK = "OK"
STATUS_NOK = "NOK"
WORKER_ALIVE = "ALIVE"

# ============================================================
# CONSTANTES DO PROTOCOLO - SPRINT 03
# ============================================================

TYPE_REQUEST_HELP = "request_help"
TYPE_RESPONSE_ACCEPTED = "response_accepted"
TYPE_RESPONSE_REJECTED = "response_rejected"
TYPE_COMMAND_REDIRECT = "command_redirect"
TYPE_REGISTER_TEMPORARY_WORKER = "register_temporary_worker"
TYPE_COMMAND_RELEASE = "command_release"
TYPE_NOTIFY_WORKER_RETURNED = "notify_worker_returned"

REASON_HIGH_LOAD = "high_load"
REASON_NO_WORKERS_AVAILABLE = "no_workers_available"
REASON_REFUSED = "refused"

SOCKET_TIMEOUT = 5
DEFAULT_BACKLOG = 30

# ============================================================
# CONSTANTES DO SUPERVISOR - SPRINT 04
# ============================================================

SUPERVISOR_HOST = "nuted-ia.dev"
SUPERVISOR_PORT = 443
SUPERVISOR_INTERVAL = 10
PAYLOAD_VERSION = "sprint4-monitor-v2"


# ============================================================
# UTILITARIOS DE SOCKET/JSON
# ============================================================


def now() -> str:
    return datetime.now().strftime("%H:%M:%S")


def log(master_id: str, message: str) -> None:
    print(f"[{now()}][MASTER {master_id}] {message}", flush=True)


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


def require_fields(payload: dict[str, Any], required: list[str]) -> Optional[str]:
    for field_name in required:
        if field_name not in payload:
            return f"Campo obrigatorio ausente: {field_name}"
    return None


def make_request_id() -> str:
    return str(uuid.uuid4())


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ============================================================
# METRICAS DO SISTEMA (SPRINT 04)
# ============================================================


def get_system_metrics() -> dict[str, Any]:
    if _HAS_PSUTIL:
        try:
            load_1m, load_5m, _ = psutil.getloadavg()
        except (AttributeError, OSError):
            load_1m, load_5m = 0.0, 0.0

        cpu_usage = psutil.cpu_percent(interval=0.1)
        mem = psutil.virtual_memory()
        disk_path = "C:\\" if psutil.WINDOWS else "/"
        disk = psutil.disk_usage(disk_path)
        uptime = int(time.time() - psutil.boot_time())
        logical = psutil.cpu_count(logical=True) or 1
        physical = psutil.cpu_count(logical=False) or 1
    else:
        load_1m = load_5m = 0.0
        cpu_usage = 0.0
        mem_total = mem_available = mem_used = 0
        disk_total = disk_free = 0.0
        disk_pct = 0.0
        uptime = int(time.time())
        logical = physical = os.cpu_count() or 1

    if _HAS_PSUTIL:
        return {
            "uptime_seconds": uptime,
            "load_average_1m": round(load_1m, 2),
            "load_average_5m": round(load_5m, 2),
            "cpu": {
                "usage_percent": cpu_usage,
                "count_logical": logical,
                "count_physical": physical,
            },
            "memory": {
                "total_mb": mem.total // (1024 * 1024),
                "available_mb": mem.available // (1024 * 1024),
                "percent_used": mem.percent,
                "memory_used": mem.used // (1024 * 1024),
            },
            "disk": {
                "total_gb": round(disk.total / (1024 ** 3), 2),
                "free_gb": round(disk.free / (1024 ** 3), 2),
                "percent_used": disk.percent,
            },
        }
    else:
        return {
            "uptime_seconds": uptime,
            "load_average_1m": 0.0,
            "load_average_5m": 0.0,
            "cpu": {
                "usage_percent": 0.0,
                "count_logical": logical,
                "count_physical": physical,
            },
            "memory": {
                "total_mb": 0,
                "available_mb": 0,
                "percent_used": 0.0,
                "memory_used": 0,
            },
            "disk": {
                "total_gb": 0.0,
                "free_gb": 0.0,
                "percent_used": 0.0,
            },
        }


# ============================================================
# ESTADO INTERNO
# ============================================================


@dataclass
class WorkerState:
    worker_id: str
    borrowed_from_master_id: Optional[str] = None
    borrowed_from_address: Optional[str] = None
    worker_address: Optional[str] = None
    busy: bool = False
    current_task: Optional[str] = None
    last_seen: float = field(default_factory=time.time)

    @property
    def is_borrowed(self) -> bool:
        return self.borrowed_from_master_id is not None or self.borrowed_from_address is not None


@dataclass
class NeighborMaster:
    master_id: str
    address: str


# ============================================================
# MASTER
# ============================================================


class MasterNode:
    def __init__(
        self,
        master_id: str,
        host: str,
        port: int,
        capacity: int,
        release_threshold: int,
        seed_tasks: int,
        neighbors: dict[str, NeighborMaster],
        monitor_interval: float,
    ) -> None:
        self.master_id = master_id
        self.host = host
        self.port = port
        self.address = f"{host}:{port}"
        self.capacity = capacity
        self.release_threshold = release_threshold
        self.neighbors = neighbors
        self.monitor_interval = monitor_interval

        self.task_queue: queue.Queue[str] = queue.Queue()
        for index in range(1, seed_tasks + 1):
            self.task_queue.put(f"USER-{master_id}-{index:03d}")

        self.lock = threading.RLock()
        self.workers: dict[str, WorkerState] = {}
        self.local_workers: set[str] = set()
        self.borrowed_workers: dict[str, WorkerState] = {}
        self.outgoing_loaned_workers: dict[str, str] = {}  # worker_id -> borrower_master_id
        self.pending_worker_commands: dict[str, dict[str, Any]] = {}

        self.stop_event = threading.Event()
        self.help_in_progress = False
        self.total_completed = 0
        self.total_failed = 0

    # --------------------------------------------------------
    # Ciclo principal do servidor TCP
    # --------------------------------------------------------

    def start(self) -> None:
        monitor_thread = threading.Thread(target=self.monitor_load_loop, daemon=True)
        monitor_thread.start()

        supervisor_thread = threading.Thread(target=self.supervisor_loop, daemon=True)
        supervisor_thread.start()

        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as server:
            server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            server.bind((self.host, self.port))
            server.listen(DEFAULT_BACKLOG)
            server.settimeout(1)

            log(self.master_id, f"Escutando TCP em {self.address}")

            try:
                while not self.stop_event.is_set():
                    try:
                        conn, addr = server.accept()
                    except socket.timeout:
                        continue

                    thread = threading.Thread(
                        target=self.handle_connection,
                        args=(conn, addr),
                        daemon=True,
                    )
                    thread.start()

            except KeyboardInterrupt:
                log(self.master_id, "Encerramento solicitado pelo terminal.")

            finally:
                self.stop_event.set()
                monitor_thread.join(timeout=2)
                supervisor_thread.join(timeout=2)
                log(self.master_id, "Servidor encerrado.")

    # --------------------------------------------------------
    # SPRINT 04 - Supervisor de metricas
    # --------------------------------------------------------

    def supervisor_loop(self) -> None:
        while not self.stop_event.is_set():
            try:
                self.send_performance_report()
            except Exception as e:
                log(self.master_id, f"Supervisor erro: {e}")
            time.sleep(SUPERVISOR_INTERVAL)

    def build_performance_report(self) -> dict[str, Any]:
        with self.lock:
            workers_total = len(self.workers)
            workers_busy = sum(1 for w in self.workers.values() if w.busy)
            workers_received = sum(
                1 for w in self.workers.values() if w.borrowed_from_master_id
            )
            workers_borrowed_count = len(self.outgoing_loaned_workers)

            borrowed_workers_list: list[dict[str, str]] = []
            for worker_id, borrower_id in self.outgoing_loaned_workers.items():
                borrowed_workers_list.append({"direction": "out", "peer_uuid": borrower_id})
            for state in self.borrowed_workers.values():
                if state.borrowed_from_master_id:
                    borrowed_workers_list.append(
                        {"direction": "in", "peer_uuid": state.borrowed_from_master_id}
                    )

            neighbors_list = [
                {
                    "server_uuid": n.master_id,
                    "status": "available",
                    "last_heartbeat": utc_now_iso(),
                }
                for n in self.neighbors.values()
            ]

        return {
            "server_uuid": self.master_id,
            "hostname": socket.gethostname(),
            "role": "master",
            "task": "performance_report",
            "timestamp": utc_now_iso(),
            "message_id": str(uuid.uuid4()),
            "payload_version": PAYLOAD_VERSION,
            "performance": {
                "system": get_system_metrics(),
                "farm_state": {
                    "workers": {
                        "total_registered": workers_total,
                        "workers_utilization": workers_busy,
                        "workers_alive": workers_total,
                        "workers_idle": workers_total - workers_busy,
                        "workers_borrowed": workers_borrowed_count,
                        "workers_received": workers_received,
                        "workers_failed": self.total_failed,
                        "workers_home": len(self.local_workers),
                        "workers_available_capacity": workers_total - workers_busy,
                        "borrowed_workers": borrowed_workers_list,
                    },
                    "tasks": {
                        "tasks_pending": self.task_queue.qsize(),
                        "tasks_running": workers_busy,
                        "tasks_completed": self.total_completed,
                        "tasks_failed": self.total_failed,
                        "oldest_task_age_s": 0,
                    },
                },
                "config_thresholds": {
                    "max_task": self.capacity,
                    "warn_cpu_percent": 85,
                    "warn_memory_percent": 85,
                    "release_task": self.release_threshold,
                },
                "neighbors": neighbors_list,
            },
        }

    def send_performance_report(self) -> None:
        payload = self.build_performance_report()
        data = (json.dumps(payload, ensure_ascii=False) + "\n").encode("utf-8")

        context = ssl.create_default_context()
        with socket.create_connection((SUPERVISOR_HOST, SUPERVISOR_PORT), timeout=10) as sock:
            with context.wrap_socket(sock, server_hostname=SUPERVISOR_HOST) as tls:
                tls.sendall(data)

        log(self.master_id, f"performance_report enviado | message_id={payload['message_id']}")

    # --------------------------------------------------------
    # Despacho de conexoes entrantes
    # --------------------------------------------------------

    def handle_connection(self, conn: socket.socket, addr: tuple[str, int]) -> None:
        conn.settimeout(SOCKET_TIMEOUT)
        try:
            with conn:
                with conn.makefile("rwb") as stream:
                    first_payload = recv_json_line(stream)
                    if first_payload is None:
                        return

                    if not isinstance(first_payload, dict):
                        log(self.master_id, f"Payload ignorado de {addr}: nao e objeto JSON")
                        return

                    if "type" in first_payload:
                        self.handle_typed_message(first_payload, stream, addr)
                        return

                    self.handle_worker_loop(first_payload, stream, addr)
        except socket.timeout:
            log(self.master_id, f"Timeout atendendo conexao {addr}")
        except json.JSONDecodeError:
            log(self.master_id, f"JSON invalido recebido de {addr}")
        except ConnectionResetError:
            log(self.master_id, f"Conexao resetada por {addr}")
        except Exception as error:
            log(self.master_id, f"Erro inesperado em {addr}: {error}")

    # --------------------------------------------------------
    # SPRINTS 01 E 02 - Worker <-> Master
    # --------------------------------------------------------

    def handle_worker_loop(self, first_payload: dict[str, Any], stream, addr: tuple[str, int]) -> None:
        payload: Optional[dict[str, Any]] = first_payload
        worker_id_for_log = "desconhecido"

        while payload is not None and not self.stop_event.is_set():
            if payload.get("TASK") == TASK_HEARTBEAT:
                self.handle_heartbeat(payload, stream, addr)

            elif payload.get("WORKER") == WORKER_ALIVE:
                worker_id_for_log = str(payload.get("WORKER_UUID", "desconhecido"))
                keep_connection = self.handle_worker_presentation(payload, stream, addr)
                if not keep_connection:
                    return

            else:
                log(self.master_id, f"Payload Worker invalido de {addr}: {payload}")
                return

            try:
                payload = recv_json_line(stream)
            except socket.timeout:
                log(self.master_id, f"Timeout aguardando proxima mensagem do Worker {worker_id_for_log}")
                return

    def handle_heartbeat(self, payload: dict[str, Any], stream, addr: tuple[str, int]) -> None:
        expected_server_uuid = payload.get("SERVER_UUID")
        if expected_server_uuid != self.master_id:
            log(self.master_id, f"Heartbeat com SERVER_UUID incorreto de {addr}: {payload}")
            send_json_line(stream, {
                "SERVER_UUID": self.master_id,
                "TASK": TASK_HEARTBEAT,
                "RESPONSE": "INVALID_REQUEST",
            })
            return

        send_json_line(stream, {
            "SERVER_UUID": self.master_id,
            "TASK": TASK_HEARTBEAT,
            "RESPONSE": RESPONSE_ALIVE,
        })
        log(self.master_id, f"Heartbeat respondido para {addr}")

    def handle_worker_presentation(self, payload: dict[str, Any], stream, addr: tuple[str, int]) -> bool:
        error = require_fields(payload, ["WORKER", "WORKER_UUID"])
        if error:
            log(self.master_id, f"Apresentacao invalida: {error}. Payload={payload}")
            return False

        if payload.get("WORKER") != WORKER_ALIVE:
            log(self.master_id, f"Valor WORKER invalido: {payload}")
            return False

        worker_id = str(payload["WORKER_UUID"])
        origin_master_id = payload.get("SERVER_UUID")
        worker_address = None

        if isinstance(payload.get("WORKER_HOST"), str) and isinstance(payload.get("WORKER_PORT"), int):
            worker_address = f"{payload['WORKER_HOST']}:{payload['WORKER_PORT']}"

        with self.lock:
            state = self.workers.get(worker_id)
            if state is None:
                state = WorkerState(worker_id=worker_id)
                self.workers[worker_id] = state

            state.last_seen = time.time()
            if worker_address:
                state.worker_address = worker_address

            if origin_master_id and origin_master_id != self.master_id:
                state.borrowed_from_master_id = str(origin_master_id)
                if not state.borrowed_from_address:
                    neighbor = self.neighbors.get(str(origin_master_id))
                    state.borrowed_from_address = neighbor.address if neighbor else None
                self.borrowed_workers[worker_id] = state
                self.local_workers.discard(worker_id)
                tipo = "EMPRESTADO"
                origem = state.borrowed_from_master_id
            else:
                if worker_id not in self.outgoing_loaned_workers:
                    self.local_workers.add(worker_id)
                tipo = "LOCAL"
                origem = self.master_id

            pending_command = self.pending_worker_commands.pop(worker_id, None)

        if pending_command is not None:
            send_json_line(stream, pending_command)
            log(self.master_id, f"Comando pendente enviado ao Worker {worker_id}: {pending_command}")
            return True

        try:
            task_user = self.task_queue.get_nowait()
        except queue.Empty:
            send_json_line(stream, {"TASK": TASK_NO_TASK})
            log(self.master_id, f"Worker {worker_id} ({tipo}, origem={origem}) sem tarefa")
            return True

        with self.lock:
            state = self.workers[worker_id]
            state.busy = True
            state.current_task = task_user

        send_json_line(stream, {"TASK": TASK_QUERY, "USER": task_user})
        log(self.master_id, f"Tarefa enviada ao Worker {worker_id} ({tipo}, origem={origem}): USER={task_user}")

        try:
            status_payload = recv_json_line(stream)
        except socket.timeout:
            log(self.master_id, f"Timeout aguardando STATUS do Worker {worker_id}")
            self.mark_worker_finished(worker_id, failed=True)
            return False

        if status_payload is None:
            log(self.master_id, f"Worker {worker_id} desconectou antes de enviar STATUS")
            self.mark_worker_finished(worker_id, failed=True)
            return False

        self.handle_worker_status(status_payload, stream, worker_id, task_user)
        return True

    def handle_worker_status(self, payload: dict[str, Any], stream, expected_worker_id: str, task_user: str) -> None:
        error = require_fields(payload, ["STATUS", "TASK", "WORKER_UUID"])
        if error:
            log(self.master_id, f"STATUS invalido do Worker {expected_worker_id}: {error}. Payload={payload}")
            self.mark_worker_finished(expected_worker_id, failed=True)
            return

        status = payload.get("STATUS")
        worker_id = payload.get("WORKER_UUID")
        task = payload.get("TASK")

        if worker_id != expected_worker_id or task != TASK_QUERY or status not in {STATUS_OK, STATUS_NOK}:
            log(self.master_id, f"STATUS inconsistente recebido: {payload}")
            self.mark_worker_finished(expected_worker_id, failed=True)
            return

        failed = status == STATUS_NOK
        self.mark_worker_finished(expected_worker_id, failed=failed)

        send_json_line(stream, {"STATUS": STATUS_ACK, "WORKER_UUID": expected_worker_id})

        with self.lock:
            state = self.workers.get(expected_worker_id)
            borrowed_info = "EMPRESTADO" if state and state.is_borrowed else "LOCAL"

        log(
            self.master_id,
            f"STATUS {status} de {expected_worker_id} ({borrowed_info}) para tarefa {task_user}; ACK enviado.",
        )

    def mark_worker_finished(self, worker_id: str, failed: bool) -> None:
        with self.lock:
            state = self.workers.get(worker_id)
            if state:
                state.busy = False
                state.current_task = None
                state.last_seen = time.time()
            if failed:
                self.total_failed += 1
            else:
                self.total_completed += 1

    # --------------------------------------------------------
    # SPRINT 03 - Mensagens tipadas Master-to-Master
    # --------------------------------------------------------

    def handle_typed_message(self, message: dict[str, Any], stream, addr: tuple[str, int]) -> None:
        msg_type = message.get("type")
        request_id = message.get("request_id")
        payload = message.get("payload")

        if not isinstance(msg_type, str) or not isinstance(request_id, str) or not isinstance(payload, dict):
            log(self.master_id, f"Mensagem tipada invalida de {addr}: {message}")
            return

        log(self.master_id, f"M2M type={msg_type} request_id={request_id}")

        if msg_type == TYPE_REQUEST_HELP:
            response = self.handle_request_help(request_id, payload)
            send_json_line(stream, response)
            return

        if msg_type == TYPE_NOTIFY_WORKER_RETURNED:
            self.handle_notify_worker_returned(request_id, payload)
            send_json_line(stream, {"STATUS": STATUS_ACK, "request_id": request_id})
            return

        if msg_type == TYPE_REGISTER_TEMPORARY_WORKER:
            self.handle_register_temporary_worker(request_id, payload)
            send_json_line(stream, {
                "STATUS": STATUS_ACK,
                "WORKER_UUID": payload.get("worker_id"),
                "request_id": request_id,
            })
            return

        log(self.master_id, f"Tipo desconhecido ignorado: {msg_type}")

    def handle_request_help(self, request_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        error = require_fields(payload, ["master_id", "current_load", "capacity", "workers_needed"])
        if error:
            log(self.master_id, f"request_help invalido: {error}")
            return self.make_rejected_response(request_id, REASON_REFUSED)

        requester_id = str(payload["master_id"])
        workers_needed = int(payload["workers_needed"])
        requester_address = payload.get("master_address")
        if not isinstance(requester_address, str):
            neighbor = self.neighbors.get(requester_id)
            requester_address = neighbor.address if neighbor else None

        with self.lock:
            my_load = self.task_queue.qsize()
            if my_load > self.capacity:
                log(self.master_id, f"Pedido recusado: carga local alta ({my_load}>{self.capacity})")
                return self.make_rejected_response(request_id, REASON_HIGH_LOAD)

            candidates = [
                state for worker_id in sorted(self.local_workers)
                if (state := self.workers.get(worker_id))
                and not state.busy
                and worker_id not in self.outgoing_loaned_workers
            ]

            offered = candidates[:max(0, workers_needed)]
            if not offered or requester_address is None:
                reason = REASON_NO_WORKERS_AVAILABLE if not offered else REASON_REFUSED
                log(self.master_id, f"Pedido recusado: reason={reason}")
                return self.make_rejected_response(request_id, reason)

            for state in offered:
                self.outgoing_loaned_workers[state.worker_id] = requester_id
                self.local_workers.discard(state.worker_id)

        worker_details = [
            {"id": state.worker_id, "address": state.worker_address or self.address}
            for state in offered
        ]

        response = {
            "type": TYPE_RESPONSE_ACCEPTED,
            "request_id": request_id,
            "payload": {
                "workers_offered": len(offered),
                "worker_details": worker_details,
            },
        }

        log(self.master_id, f"Pedido aceito para Master {requester_id}: {len(offered)} worker(s)")

        for state in offered:
            command = {
                "type": TYPE_COMMAND_REDIRECT,
                "request_id": make_request_id(),
                "payload": {
                    "new_master_address": requester_address,
                    "new_master_id": requester_id,
                },
            }
            self.send_command_to_worker_or_queue(state.worker_id, command)

        self.print_state()
        return response

    def make_rejected_response(self, request_id: str, reason: str) -> dict[str, Any]:
        return {
            "type": TYPE_RESPONSE_REJECTED,
            "request_id": request_id,
            "payload": {"reason": reason},
        }

    def handle_register_temporary_worker(self, request_id: str, payload: dict[str, Any]) -> None:
        error = require_fields(payload, ["worker_id", "original_master_address"])
        if error:
            log(self.master_id, f"register_temporary_worker invalido: {error}")
            return

        worker_id = str(payload["worker_id"])
        original_master_address = str(payload["original_master_address"])
        original_master_id = str(payload.get("original_master_id") or "UNKNOWN_ORIGIN")
        worker_address = payload.get("worker_address")

        with self.lock:
            state = self.workers.get(worker_id)
            if state is None:
                state = WorkerState(worker_id=worker_id)
                self.workers[worker_id] = state
            state.borrowed_from_master_id = original_master_id
            state.borrowed_from_address = original_master_address
            if isinstance(worker_address, str):
                state.worker_address = worker_address
            state.last_seen = time.time()
            self.borrowed_workers[worker_id] = state
            self.local_workers.discard(worker_id)

        log(
            self.master_id,
            f"Worker temporario registrado: worker_id={worker_id} origem={original_master_id}@{original_master_address}",
        )
        self.print_state()

    def handle_notify_worker_returned(self, request_id: str, payload: dict[str, Any]) -> None:
        error = require_fields(payload, ["worker_id"])
        if error:
            log(self.master_id, f"notify_worker_returned invalido: {error}")
            return

        worker_id = str(payload["worker_id"])
        with self.lock:
            self.outgoing_loaned_workers.pop(worker_id, None)
            self.local_workers.add(worker_id)
            if worker_id in self.workers:
                self.workers[worker_id].borrowed_from_master_id = None
                self.workers[worker_id].borrowed_from_address = None

        log(self.master_id, f"Worker devolvido: {worker_id}")
        self.print_state()

    # --------------------------------------------------------
    # Envio de comandos aos Workers
    # --------------------------------------------------------

    def send_command_to_worker_or_queue(self, worker_id: str, command: dict[str, Any]) -> None:
        with self.lock:
            state = self.workers.get(worker_id)
            worker_address = state.worker_address if state else None

        if worker_address:
            try:
                host, port = parse_address(worker_address)
                with socket.create_connection((host, port), timeout=SOCKET_TIMEOUT) as sock:
                    sock.settimeout(SOCKET_TIMEOUT)
                    with sock.makefile("rwb") as stream:
                        send_json_line(stream, command)
                        recv_json_line(stream)
                log(self.master_id, f"Comando enviado diretamente ao Worker {worker_id}: type={command.get('type')}")
                return
            except Exception as error:
                log(self.master_id, f"Falha ao enviar comando direto ao Worker {worker_id}: {error}. Enfileirando.")

        with self.lock:
            self.pending_worker_commands[worker_id] = command
        log(self.master_id, f"Comando enfileirado para Worker {worker_id}: type={command.get('type')}")

    # --------------------------------------------------------
    # Monitor de saturacao e devolucao
    # --------------------------------------------------------

    def monitor_load_loop(self) -> None:
        while not self.stop_event.is_set():
            time.sleep(self.monitor_interval)
            try:
                self.check_saturation_and_request_help()
                self.check_release_borrowed_workers()
            except Exception as error:
                log(self.master_id, f"Erro no monitor de carga: {error}")

    def check_saturation_and_request_help(self) -> None:
        current_load = self.task_queue.qsize()
        if current_load <= self.capacity:
            return

        with self.lock:
            if self.help_in_progress:
                return
            self.help_in_progress = True

        try:
            excess = current_load - self.capacity
            workers_needed = min(max(1, math.ceil(excess / max(1, self.capacity))), 5)
            log(
                self.master_id,
                f"SATURACAO detectada: current_load={current_load} capacity={self.capacity} workers_needed={workers_needed}",
            )

            for neighbor in self.neighbors.values():
                if self.request_help_from_neighbor(neighbor, current_load, workers_needed):
                    break
        finally:
            with self.lock:
                self.help_in_progress = False

    def request_help_from_neighbor(self, neighbor: NeighborMaster, current_load: int, workers_needed: int) -> bool:
        request_id = make_request_id()
        message = {
            "type": TYPE_REQUEST_HELP,
            "request_id": request_id,
            "payload": {
                "master_id": self.master_id,
                "master_address": self.address,
                "current_load": current_load,
                "capacity": self.capacity,
                "workers_needed": workers_needed,
            },
        }

        log(self.master_id, f"Enviando request_help para {neighbor.master_id}@{neighbor.address}")
        try:
            host, port = parse_address(neighbor.address)
            with socket.create_connection((host, port), timeout=SOCKET_TIMEOUT) as sock:
                sock.settimeout(SOCKET_TIMEOUT)
                with sock.makefile("rwb") as stream:
                    send_json_line(stream, message)
                    response = recv_json_line(stream)
        except socket.timeout:
            log(self.master_id, f"Timeout aguardando resposta de {neighbor.master_id}")
            return False
        except Exception as error:
            log(self.master_id, f"Falha ao negociar com {neighbor.master_id}: {error}")
            return False

        if not isinstance(response, dict):
            log(self.master_id, f"Resposta invalida de {neighbor.master_id}: {response}")
            return False

        if response.get("request_id") != request_id:
            log(self.master_id, f"request_id incorreto na resposta de {neighbor.master_id}")
            return False

        response_type = response.get("type")
        if response_type == TYPE_RESPONSE_ACCEPTED:
            log(self.master_id, f"Ajuda aceita por {neighbor.master_id}")
            return True

        if response_type == TYPE_RESPONSE_REJECTED:
            reason = response.get("payload", {}).get("reason")
            log(self.master_id, f"Ajuda recusada por {neighbor.master_id}: reason={reason}")
            return False

        log(self.master_id, f"Tipo de resposta inesperado de {neighbor.master_id}: {response_type}")
        return False

    def check_release_borrowed_workers(self) -> None:
        current_load = self.task_queue.qsize()
        if current_load > self.release_threshold:
            return

        with self.lock:
            borrowed = list(self.borrowed_workers.values())

        if not borrowed:
            return

        log(self.master_id, f"Carga normalizada: current_load={current_load} <= release_threshold={self.release_threshold}")

        for state in borrowed:
            if state.busy or not state.borrowed_from_address:
                continue

            command = {
                "type": TYPE_COMMAND_RELEASE,
                "request_id": make_request_id(),
                "payload": {
                    "original_master_address": state.borrowed_from_address,
                    "original_master_id": state.borrowed_from_master_id,
                },
            }
            self.send_command_to_worker_or_queue(state.worker_id, command)
            self.notify_worker_returned(state)

            with self.lock:
                self.borrowed_workers.pop(state.worker_id, None)
                self.workers.pop(state.worker_id, None)

        self.print_state()

    def notify_worker_returned(self, state: WorkerState) -> None:
        if not state.borrowed_from_address:
            return

        message = {
            "type": TYPE_NOTIFY_WORKER_RETURNED,
            "request_id": make_request_id(),
            "payload": {"worker_id": state.worker_id},
        }

        try:
            host, port = parse_address(state.borrowed_from_address)
            with socket.create_connection((host, port), timeout=SOCKET_TIMEOUT) as sock:
                sock.settimeout(SOCKET_TIMEOUT)
                with sock.makefile("rwb") as stream:
                    send_json_line(stream, message)
                    recv_json_line(stream)
            log(self.master_id, f"notify_worker_returned enviado para {state.borrowed_from_address}")
        except Exception as error:
            log(self.master_id, f"Falha ao notificar devolucao para {state.borrowed_from_address}: {error}")

    # --------------------------------------------------------
    # Observabilidade
    # --------------------------------------------------------

    def print_state(self) -> None:
        with self.lock:
            local = sorted(self.local_workers)
            borrowed = sorted(self.borrowed_workers.keys())
            loaned = dict(self.outgoing_loaned_workers)
            pending = self.task_queue.qsize()
            completed = self.total_completed
            failed = self.total_failed

        log(
            self.master_id,
            f"ESTADO | fila={pending} concluidas={completed} falhas={failed} "
            f"workers_locais={local} workers_emprestados={borrowed} workers_cedidos={loaned}",
        )


# ============================================================
# CLI
# ============================================================


def parse_neighbors(values: list[str]) -> dict[str, NeighborMaster]:
    neighbors: dict[str, NeighborMaster] = {}
    for value in values:
        if "=" not in value:
            raise ValueError(f"Vizinho invalido: {value}. Use MASTER_ID=host:porta")
        master_id, address = value.split("=", 1)
        parse_address(address)
        neighbors[master_id] = NeighborMaster(master_id=master_id, address=address)
    return neighbors


def main() -> None:
    parser = argparse.ArgumentParser(description="Master P2P - Equipe 5 - Sprints 01-04")
    parser.add_argument("--master-id", required=True, help="Identificador unico do Master. Ex: equipe5_A")
    parser.add_argument("--host", default="127.0.0.1", help="Host TCP do Master")
    parser.add_argument("--port", type=int, required=True, help="Porta TCP do Master")
    parser.add_argument("--capacity", type=int, default=5, help="Threshold de saturacao da fila (default: 5)")
    parser.add_argument("--release-threshold", type=int, default=2, help="Threshold de liberacao/histerese (default: 2)")
    parser.add_argument("--seed-tasks", type=int, default=0, help="Quantidade inicial de tarefas simuladas")
    parser.add_argument(
        "--neighbor",
        action="append",
        default=[],
        help="Master vizinho no formato MASTER_ID=host:porta. Pode repetir.",
    )
    parser.add_argument("--monitor-interval", type=float, default=2.0, help="Intervalo do monitor de carga (segundos)")

    args = parser.parse_args()
    neighbors = parse_neighbors(args.neighbor)

    master = MasterNode(
        master_id=args.master_id,
        host=args.host,
        port=args.port,
        capacity=args.capacity,
        release_threshold=args.release_threshold,
        seed_tasks=args.seed_tasks,
        neighbors=neighbors,
        monitor_interval=args.monitor_interval,
    )
    master.start()


if __name__ == "__main__":
    main()
