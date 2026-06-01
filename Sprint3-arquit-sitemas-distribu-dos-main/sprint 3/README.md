# P2P Dynamic Load Balancing

Sistema P2P descentralizado para **balanceamento horizontal de carga dinamico** entre nos Master e Farms de Workers, desenvolvido no contexto de Sistemas Distribuidos - CEUB 2026.

Status da Sprint 03: **HOMOLOGADO**  
Resultado do Harness: **100% aprovado - CT01 a CT09**

```bash
python "sprint 3/test_suite.py"
# Resultado: 9/9 testes passaram
```

---

## 1. Arquitetura Geral

O ecossistema e composto por Masters hibridos, Workers locais e uma rede de Masters vizinhos. Cada Master atua em dois papeis simultaneos:

| Componente | Papel | Responsabilidade |
|---|---|---|
| **Master Node** | Servidor + Cliente M2M | Recebe Workers, escuta mensagens de outros Masters e solicita ajuda quando saturado. |
| **Worker Node** | Processador resiliente | Solicita tarefas, processa jobs, reporta status e pode ser redirecionado temporariamente. |
| **Master Vizinho** | Peer P2P | Avalia pedidos de ajuda, cede Workers ociosos e recebe notificacoes de devolucao. |
| **Harness** | Validacao automatizada | Exercita CT01 a CT09, incluindo cenarios felizes, timeouts, falhas e payloads invalidos. |

### Topologia

```text
                 Rede P2P de Masters

        request_help / response_accepted
        response_rejected / notify_worker_returned

    +------------------+              +------------------+
    |   Master A       |<------------>|   Master B       |
    |   Saturado       |              |   Vizinho        |
    |   :10000         |              |   :10001         |
    +--------+---------+              +--------+---------+
             |                                 |
             | QUERY / ACK                     | command_redirect
             | register_temporary_worker       |
             v                                 v
      +-------------+                   +-------------+
      | Worker A1   |                   | Worker B1   |
      +-------------+                   +-------------+
                                              |
                                              | emprestimo temporario
                                              v
                                        +-------------+
                                        | Master A    |
                                        +-------------+
```

---

## 2. Protocolo Consensual de Mensageria

### Regras globais

| Regra | Valor |
|---|---|
| Transporte | TCP sockets |
| Encoding | `utf-8` |
| Delimitador | `\n` |
| Timeout | `5 segundos` |
| Payload | 1 objeto JSON por linha |
| Sprint 03 `type` | lowercase estrito |
| Tokens Sprint 02 | uppercase estrito |
| Campos desconhecidos | Ignorados |
| Campos obrigatorios ausentes | Rejeitados com log |

### Tokens legados Sprint 02

```text
ALIVE / WORKER=ALIVE
QUERY
NO_TASK
OK
NOK
ACK
```

### Tipos M2M e M2W Sprint 03

| Tipo | Direcao | Objetivo |
|---|---|---|
| `request_help` | Master -> Master | Solicitar Workers a um vizinho. |
| `response_accepted` | Master -> Master | Aceitar pedido e informar Workers disponiveis. |
| `response_rejected` | Master -> Master | Recusar pedido com motivo controlado. |
| `command_redirect` | Master -> Worker | Redirecionar Worker para Master saturado. |
| `register_temporary_worker` | Worker -> Master | Registrar Worker emprestado no Master receptor. |
| `command_release` | Master -> Worker | Liberar Worker emprestado para retornar a origem. |
| `notify_worker_returned` | Master -> Master | Notificar Master de origem sobre devolucao. |

---

## 3. Fluxos da Sprint 03

### 3.1 Pedido de Ajuda: `request_help`

O Master saturado dispara ajuda quando sua carga ultrapassa a capacidade nominal.

```json
{
  "type": "request_help",
  "request_id": "550e8400-e29b-41d4-a716-446655440000",
  "payload": {
    "master_id": "A",
    "current_load": 150,
    "capacity": 100,
    "workers_needed": 2
  }
}
```

Regra:

```text
current_load > saturation_threshold
150 > 100 => saturado
```

### 3.2 Negociacao Sincrona

Se o Master vizinho possui capacidade disponivel e Workers ociosos, responde com aceite:

```json
{
  "type": "response_accepted",
  "request_id": "550e8400-e29b-41d4-a716-446655440000",
  "payload": {
    "workers_offered": 2,
    "worker_details": [
      {
        "worker_id": "B1",
        "address": "127.0.0.1:5003"
      },
      {
        "worker_id": "B2",
        "address": "127.0.0.1:5004"
      }
    ]
  }
}
```

Se o vizinho tambem esta sob alta carga:

```json
{
  "type": "response_rejected",
  "request_id": "550e8400-e29b-41d4-a716-446655440000",
  "payload": {
    "reason": "high_load"
  }
}
```

Motivos suportados:

```text
high_load
no_workers_available
refused
```

### 3.3 Redirecionamento Dinamico

Apos aceite, o Master ofertante redireciona o Worker local para o Master saturado:

```json
{
  "type": "command_redirect",
  "request_id": "550e8400-e29b-41d4-a716-446655440000",
  "payload": {
    "new_master_address": "127.0.0.1:10000",
    "original_master_address": "127.0.0.1:10001"
  }
}
```

O Worker fecha o ciclo atual de forma controlada, conecta no novo Master e registra sua origem:

```json
{
  "type": "register_temporary_worker",
  "request_id": "550e8400-e29b-41d4-a716-446655440000",
  "payload": {
    "worker_id": "B1",
    "original_master_address": "127.0.0.1:10001"
  }
}
```

### 3.4 Histerese e Devolucao

Para mitigar o efeito ping-pong, o sistema usa barreiras distintas:

| Barreira | Valor | Efeito |
|---|---:|---|
| Saturacao | `100` | Aciona `request_help` quando `current_load > 100`. |
| Liberacao | `70` | Aciona devolucao quando `current_load < 70`. |

Fluxo de devolucao:

```text
Master A detecta alivio de carga
        |
        +-- command_release ---------> Worker B1
        |
        +-- notify_worker_returned --> Master B
```

Payload para o Worker:

```json
{
  "type": "command_release",
  "request_id": "550e8400-e29b-41d4-a716-446655440000",
  "payload": {
    "original_master_address": "127.0.0.1:10001"
  }
}
```

Notificacao ao Master de origem:

```json
{
  "type": "notify_worker_returned",
  "request_id": "550e8400-e29b-41d4-a716-446655440000",
  "payload": {
    "worker_id": "B1"
  }
}
```

---

## 4. Harness Engineering

A Sprint 03 foi validada com uma suite automatizada em duas camadas:

| Camada | Arquivo | Finalidade |
|---|---|---|
| Runner executivo | `test_suite.py` | Executa CT01 a CT09 com saida visual no terminal. |
| Suite pytest | `tests/` | Mantem testes modulares e agressivos de rede. |
| API deterministica | `runtime/harness_api.py` | Simula fluxos criticos sem depender de topologias longas. |
| Runtime real | `runtime/master.py`, `runtime/worker.py` | Executaveis de producao validados por smoke TCP. |

### Matriz de cobertura

| ID | Cenario | Validacao | Status |
|---|---|---|---|
| CT01 | Pedido de ajuda aceito | `request_help` -> `response_accepted` -> `command_redirect` | PASS |
| CT02 | Pedido recusado | `response_rejected(reason=high_load)` | PASS |
| CT03 | Correlacao | `request_id` preservado em fluxos concorrentes | PASS |
| CT04 | Registro temporario | `register_temporary_worker` valido | PASS |
| CT05 | Tarefa no emprestado | `QUERY` -> `OK` -> `ACK` | PASS |
| CT06 | Devolucao | `command_release` + `notify_worker_returned` | PASS |
| CT07 | Timeout M2M | Estouro de 5s e fallback para proximo vizinho | PASS |
| CT08 | Queda do receptor | Worker retorna ao Master original | PASS |
| CT09 | Tipo desconhecido | Strict parsing descarta sem crash | PASS |

### Execucao da suite unificada

```bash
python "sprint 3/test_suite.py"
```

Saida esperada:

```text
Sprint 03 Integration Test Suite - CT01 a CT09
PASS CT01 - Pedido de ajuda aceito
PASS CT02 - Pedido de ajuda recusado
PASS CT03 - Correlacao de request_id
PASS CT04 - Registro temporario
PASS CT05 - Tarefa no Worker emprestado
PASS CT06 - Devolucao por histerese
PASS CT07 - Timeout M2M
PASS CT08 - Queda do receptor
PASS CT09 - Tipo desconhecido

Resultado: 9/9 testes passaram
```

### Execucao via pytest

```bash
python -m pytest "sprint 3/tests" -q
```

Saida homologada:

```text
11 passed
```

---

## 5. Guia Rapido de Execucao

> Os comandos abaixo devem ser executados a partir da raiz do workspace.

### 5.1 Inicializar Master B, vizinho ofertante

```bash
python "sprint 3/runtime/master.py" --id B --address 127.0.0.1:10001
```

### 5.2 Inicializar Master A, saturado e conectado ao vizinho B

```bash
python "sprint 3/runtime/master.py" --id A --address 127.0.0.1:10000 --neighbor 127.0.0.1:10001
```

### 5.3 Inicializar Worker local do Master B

```bash
python "sprint 3/runtime/worker.py" --id B1 --master 127.0.0.1:10001
```

### 5.4 Inicializar Worker local do Master A

```bash
python "sprint 3/runtime/worker.py" --id A1 --master 127.0.0.1:10000
```

### 5.5 Simular injecao de carga e negociacao M2M

Para uma simulacao reproduzivel e automatizada do fluxo completo, use o runner homologado:

```bash
python "sprint 3/test_suite.py"
```

Para uma simulacao programatica de saturacao em um Master local:

```bash
python - <<'PY'
import sys
from pathlib import Path

sys.path.insert(0, str(Path("sprint 3/runtime").resolve()))

from master import MasterNode

master_a = MasterNode("A", "127.0.0.1:10000", neighbors=["127.0.0.1:10001"])

for index in range(150):
    master_a.add_task({"TASK": "QUERY", "USER": f"user-{index}"})

print("Carga injetada:", master_a.current_load)
print("Workers necessarios:", master_a._workers_needed(master_a.current_load))
print("Execute Master B e Workers em terminais separados para observar a negociacao real.")
PY
```

No Windows PowerShell, a alternativa equivalente e:

```powershell
@'
import sys
from pathlib import Path

sys.path.insert(0, str(Path("sprint 3/runtime").resolve()))

from master import MasterNode

master_a = MasterNode("A", "127.0.0.1:10000", neighbors=["127.0.0.1:10001"])

for index in range(150):
    master_a.add_task({"TASK": "QUERY", "USER": f"user-{index}"})

print("Carga injetada:", master_a.current_load)
print("Workers necessarios:", master_a._workers_needed(master_a.current_load))
'@ | python -
```

---

## 6. Estrutura de Arquivos

```text
sprint 3/
|-- README.md
|-- test_suite.py
|-- plan-build/
|   |-- contract.md
|   `-- spec.md
|-- runtime/
|   |-- __init__.py
|   |-- harness_api.py
|   |-- master.py
|   |-- protocol.py
|   |-- verify_runtime.py
|   `-- worker.py
`-- tests/
    |-- conftest.py
    |-- test_m2m_negotiation.py
    |-- test_network_aggressive.py
    |-- test_timeout_fallback.py
    `-- test_worker_lifecycle.py
```

---

## 7. Definicao de Pronto

| Criterio | Evidencia | Status |
|---|---|---|
| Interoperabilidade por contrato | Payloads definidos em `contract.md` | OK |
| Strict parsing | Campos obrigatorios rejeitados; campos extras ignorados | OK |
| Timeout M2M | Janela de 5 segundos validada no CT07 | OK |
| Resiliencia de Worker | Retorno autonomo validado no CT08 | OK |
| Robustez contra payload invalido | CT09 e testes agressivos | OK |
| Recursos de rede | Sockets fechados em `finally` ou context manager | OK |
| Concorrencia | Locks em filas e mapas compartilhados | OK |

**Veredito:** Sprint 03 pronta para interoperabilidade com implementacoes de outras equipes.

---

## 8. Equipe

| Nome | Curso |
|---|---|
| Lucas Adriano | Sistemas de Informacao - CEUB |
| Rodrigo Crizostimo | Sistemas de Informacao - CEUB |
| Marlon Paganotto | Sistemas de Informacao - CEUB |

Disciplina: Sistemas Distribuidos — CEUB 2026
