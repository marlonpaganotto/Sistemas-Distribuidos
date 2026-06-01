"""Sprint 3 protocol primitives.

The runtime keeps Sprint 2 compatibility while adding strict Sprint 3 parsing.
Messages are one JSON object per line over TCP using utf-8.
"""

from __future__ import annotations

import json
import logging
import socket
import uuid
from dataclasses import dataclass
from typing import Any, Callable


SOCKET_TIMEOUT_SECONDS = 5.0
ENCODING = "utf-8"
DELIMITER = b"\n"

SPRINT2_WORKER_ALIVE = "WORKER=ALIVE"
SPRINT2_QUERY = "QUERY"
SPRINT2_NO_TASK = "NO_TASK"
SPRINT2_OK = "OK"
SPRINT2_NOK = "NOK"
SPRINT2_ACK = "ACK"

SPRINT3_TYPES = {
    "request_help",
    "response_accepted",
    "response_rejected",
    "command_redirect",
    "register_temporary_worker",
    "command_release",
    "notify_worker_returned",
}


class ProtocolError(ValueError):
    """Raised when a protocol message is invalid."""


@dataclass(frozen=True)
class ParsedMessage:
    type: str
    request_id: str
    payload: dict[str, Any]
    raw: dict[str, Any]


def new_request_id() -> str:
    return str(uuid.uuid4())


def is_uuid4(value: str) -> bool:
    try:
        parsed = uuid.UUID(value, version=4)
    except (TypeError, ValueError, AttributeError):
        return False
    return str(parsed) == value.lower() and parsed.version == 4


def _require_fields(data: dict[str, Any], fields: tuple[str, ...], scope: str) -> None:
    missing = [field for field in fields if field not in data]
    if missing:
        raise ProtocolError(f"{scope} missing required fields: {', '.join(missing)}")


def _require_int(payload: dict[str, Any], field: str, minimum: int, inclusive: bool = True) -> None:
    value = payload.get(field)
    if not isinstance(value, int):
        raise ProtocolError(f"payload.{field} must be an integer")
    if inclusive and value < minimum:
        raise ProtocolError(f"payload.{field} must be >= {minimum}")
    if not inclusive and value <= minimum:
        raise ProtocolError(f"payload.{field} must be > {minimum}")


def _require_str(payload: dict[str, Any], field: str) -> None:
    if not isinstance(payload.get(field), str) or not payload[field]:
        raise ProtocolError(f"payload.{field} must be a non-empty string")


def build_message(message_type: str, payload: dict[str, Any], request_id: str | None = None) -> dict[str, Any]:
    request_id = request_id or new_request_id()
    message = {"type": message_type, "request_id": request_id, "payload": dict(payload)}
    parse_sprint3_message(message)
    return message


def parse_sprint3_message(data: Any) -> ParsedMessage:
    if not isinstance(data, dict):
        raise ProtocolError("message must be a JSON object")

    _require_fields(data, ("type", "request_id", "payload"), "message")
    message_type = data["type"]
    request_id = data["request_id"]
    payload = data["payload"]

    if not isinstance(message_type, str) or message_type not in SPRINT3_TYPES:
        raise ProtocolError("message.type must be a known lowercase Sprint 3 type")
    if message_type.lower() != message_type:
        raise ProtocolError("message.type must be lowercase strict")
    if not isinstance(request_id, str) or not is_uuid4(request_id):
        raise ProtocolError("message.request_id must be a UUID v4 string")
    if not isinstance(payload, dict):
        raise ProtocolError("message.payload must be an object")

    validator = _VALIDATORS[message_type]
    validator(payload)
    return ParsedMessage(type=message_type, request_id=request_id, payload=payload, raw=data)


def _validate_request_help(payload: dict[str, Any]) -> None:
    _require_fields(payload, ("master_id", "current_load", "capacity", "workers_needed"), "payload")
    _require_str(payload, "master_id")
    _require_int(payload, "current_load", 0, inclusive=True)
    _require_int(payload, "capacity", 0, inclusive=False)
    _require_int(payload, "workers_needed", 0, inclusive=False)


def _validate_response_accepted(payload: dict[str, Any]) -> None:
    _require_fields(payload, ("workers_offered", "worker_details"), "payload")
    _require_int(payload, "workers_offered", 0, inclusive=False)
    details = payload["worker_details"]
    if not isinstance(details, list):
        raise ProtocolError("payload.worker_details must be a list")
    if len(details) != payload["workers_offered"]:
        raise ProtocolError("payload.worker_details length must match workers_offered")
    for item in details:
        if not isinstance(item, dict):
            raise ProtocolError("payload.worker_details items must be objects")
        _require_fields(item, ("worker_id", "address"), "payload.worker_details[]")
        _require_str(item, "worker_id")
        _require_str(item, "address")


def _validate_response_rejected(payload: dict[str, Any]) -> None:
    _require_fields(payload, ("reason",), "payload")
    if payload["reason"] not in {"high_load", "no_workers_available", "refused"}:
        raise ProtocolError("payload.reason is not allowed")


def _validate_command_redirect(payload: dict[str, Any]) -> None:
    _require_fields(payload, ("new_master_address", "original_master_address"), "payload")
    _require_str(payload, "new_master_address")
    _require_str(payload, "original_master_address")


def _validate_register_temporary_worker(payload: dict[str, Any]) -> None:
    _require_fields(payload, ("worker_id", "original_master_address"), "payload")
    _require_str(payload, "worker_id")
    _require_str(payload, "original_master_address")


def _validate_command_release(payload: dict[str, Any]) -> None:
    _require_fields(payload, ("original_master_address",), "payload")
    _require_str(payload, "original_master_address")


def _validate_notify_worker_returned(payload: dict[str, Any]) -> None:
    _require_fields(payload, ("worker_id",), "payload")
    _require_str(payload, "worker_id")


_VALIDATORS: dict[str, Callable[[dict[str, Any]], None]] = {
    "request_help": _validate_request_help,
    "response_accepted": _validate_response_accepted,
    "response_rejected": _validate_response_rejected,
    "command_redirect": _validate_command_redirect,
    "register_temporary_worker": _validate_register_temporary_worker,
    "command_release": _validate_command_release,
    "notify_worker_returned": _validate_notify_worker_returned,
}


def send_json(sock: socket.socket, payload: dict[str, Any]) -> None:
    sock.sendall(json.dumps(payload, separators=(",", ":")).encode(ENCODING) + DELIMITER)


def recv_json(sock: socket.socket) -> dict[str, Any]:
    buffer = bytearray()
    while True:
        chunk = sock.recv(1)
        if not chunk:
            raise ConnectionError("socket closed before delimiter")
        if chunk == DELIMITER:
            break
        buffer.extend(chunk)
    decoded = buffer.decode(ENCODING)
    data = json.loads(decoded)
    if not isinstance(data, dict):
        raise ProtocolError("JSON payload must be an object")
    return data


def send_line(sock: socket.socket, value: str) -> None:
    sock.sendall(value.encode(ENCODING) + DELIMITER)


def recv_line(sock: socket.socket) -> str:
    buffer = bytearray()
    while True:
        chunk = sock.recv(1)
        if not chunk:
            raise ConnectionError("socket closed before delimiter")
        if chunk == DELIMITER:
            break
        buffer.extend(chunk)
    return buffer.decode(ENCODING)


def configure_socket(sock: socket.socket) -> socket.socket:
    sock.settimeout(SOCKET_TIMEOUT_SECONDS)
    return sock


def parse_address(address: str) -> tuple[str, int]:
    host, separator, port_text = address.rpartition(":")
    if not separator or not host:
        raise ValueError(f"invalid address: {address!r}")
    return host, int(port_text)


def make_logger(name: str) -> logging.Logger:
    logger = logging.getLogger(name)
    if not logger.handlers:
        handler = logging.StreamHandler()
        formatter = logging.Formatter(
            "%(asctime)s %(levelname)s node=%(name)s type=%(message_type)s request_id=%(request_id)s %(message)s"
        )
        handler.setFormatter(formatter)
        logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    logger.propagate = False
    return logger


def log_event(
    logger: logging.Logger,
    level: int,
    text: str,
    *,
    message_type: str = "-",
    request_id: str = "-",
) -> None:
    logger.log(level, text, extra={"message_type": message_type, "request_id": request_id})
