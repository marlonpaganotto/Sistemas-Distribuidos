from __future__ import annotations

import importlib
import sys
from pathlib import Path
from uuid import UUID

import pytest


SPRINT_ROOT = Path(__file__).resolve().parents[1]
RUNTIME_ROOT = SPRINT_ROOT / "runtime"

if RUNTIME_ROOT.exists():
    sys.path.insert(0, str(RUNTIME_ROOT))


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line("markers", "ct01: pedido de ajuda aceito")
    config.addinivalue_line("markers", "ct02: pedido de ajuda recusado")
    config.addinivalue_line("markers", "ct03: correlacao concorrente por request_id")
    config.addinivalue_line("markers", "ct04: ciclo completo no worker emprestado")
    config.addinivalue_line("markers", "ct06: devolucao completa com histerese")
    config.addinivalue_line("markers", "ct07: timeout M2M e fallback")
    config.addinivalue_line("markers", "ct08: recuperacao autonoma do worker")


def _load_harness_api():
    if not RUNTIME_ROOT.exists():
        pytest.skip(
            "sprint 3/runtime ainda nao existe; testes preparados para importar "
            "runtime/harness_api.py quando o Agente de Construcao terminar.",
            allow_module_level=False,
        )

    candidates = ("harness_api", "runtime_harness", "api")
    errors = []
    for module_name in candidates:
        try:
            return importlib.import_module(module_name)
        except ModuleNotFoundError as exc:
            errors.append(f"{module_name}: {exc}")

    pytest.skip(
        "Nenhum modulo de harness encontrado em sprint 3/runtime. "
        "Forneca harness_api.py expondo as funcoes usadas por estes testes. "
        f"Tentativas: {'; '.join(errors)}",
        allow_module_level=False,
    )


@pytest.fixture(scope="session")
def runtime_api():
    return _load_harness_api()


def require_callable(api, name: str):
    value = getattr(api, name, None)
    if not callable(value):
        pytest.skip(f"runtime harness precisa expor a funcao callable {name}()")
    return value


def assert_uuid_v4(value: str) -> None:
    parsed = UUID(value)
    assert parsed.version == 4
    assert str(parsed) == value.lower()


def assert_message_type(message: dict, expected_type: str) -> None:
    assert isinstance(message, dict)
    assert message.get("type") == expected_type
    assert "payload" in message and isinstance(message["payload"], dict)
    assert_uuid_v4(message["request_id"])


def assert_contract_valid(api, message: dict) -> None:
    validate_message = require_callable(api, "validate_message")
    result = validate_message(message)
    assert result is True or isinstance(result, dict)


def assert_contract_invalid(api, message: dict) -> None:
    validate_message = require_callable(api, "validate_message")
    with pytest.raises((ValueError, TypeError, AssertionError)):
        validate_message(message)

