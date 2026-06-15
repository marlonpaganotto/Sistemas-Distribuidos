# Projeto Final - Sistemas Distribuídos (Equipe 5)

Implementação completa das Sprints 01–04 do projeto de Sistemas Distribuídos.

## Arquivos principais

| Arquivo | Descrição |
|---|---|
| `master.py` | Nó Master completo (Sprints 01–04) |
| `worker.py` | Nó Worker completo (Sprints 01–03) |
| `Equipe5.py` | Monitor standalone de Sprint 4 (envio local para teste) |
| `supervisor.py` | Servidor de teste local que recebe e exibe relatórios |
| `Exemplo/` | Implementação de referência fornecida |

## Requisitos

- Python 3.10+
- `psutil` (métricas reais de CPU/memória/disco)

```powershell
python -m pip install psutil
```

## Uso — master.py + worker.py

### Master A (com 10 tarefas iniciais)

```powershell
python master.py --master-id equipe5_A --port 5000 --seed-tasks 10
```

### Master B (vizinho do A)

```powershell
python master.py --master-id equipe5_B --port 5001 --neighbor equipe5_A=127.0.0.1:5000
```

### Workers do Master A

```powershell
python worker.py --worker-id W1 --master-id equipe5_A --master-address 127.0.0.1:5000 --command-port 6001
python worker.py --worker-id W2 --master-id equipe5_A --master-address 127.0.0.1:5000 --command-port 6002
```

### Workers do Master B

```powershell
python worker.py --worker-id W3 --master-id equipe5_B --master-address 127.0.0.1:5001 --command-port 6003
python worker.py --worker-id W4 --master-id equipe5_B --master-address 127.0.0.1:5001 --command-port 6004
```

## O que o master.py faz

- **Sprint 01** — responde Heartbeat de Workers.
- **Sprint 02** — distribui tarefas da fila, recebe STATUS OK/NOK, envia ACK.
- **Sprint 03** — negocia empréstimo de Workers com Masters vizinhos (`request_help`, `command_redirect`, `register_temporary_worker`, `command_release`, `notify_worker_returned`).
- **Sprint 04** — envia `performance_report` a cada 10 s via TLS para `nuted-ia.dev:443`, com métricas reais de CPU/memória/disco (psutil).

## Teste local com supervisor.py

Para testar o envio do relatório sem TLS, use `supervisor.py` e `Equipe5.py`:

```powershell
# Terminal 1
python supervisor.py --host 127.0.0.1 --port 8000

# Terminal 2
python Equipe5.py --once --host 127.0.0.1 --port 8000
```

## Opções CLI — master.py

| Flag | Padrão | Descrição |
|---|---|---|
| `--master-id` | (obrigatório) | Identificador único do Master |
| `--port` | (obrigatório) | Porta TCP |
| `--host` | `127.0.0.1` | Endereço de escuta |
| `--capacity` | `5` | Threshold de saturação da fila |
| `--release-threshold` | `2` | Threshold para devolver Workers |
| `--seed-tasks` | `0` | Tarefas iniciais simuladas |
| `--neighbor` | — | Vizinho: `ID=host:porta` (repetível) |
| `--monitor-interval` | `2.0` | Intervalo do monitor de carga (s) |

## Opções CLI — worker.py

| Flag | Padrão | Descrição |
|---|---|---|
| `--worker-id` | (obrigatório) | Identificador único do Worker |
| `--master-id` | (obrigatório) | Master original |
| `--master-address` | (obrigatório) | Endereço do Master (`host:porta`) |
| `--command-port` | (obrigatório) | Porta de comandos do Worker |
| `--command-host` | `127.0.0.1` | Host de escuta de comandos |
| `--advertised-host` | `127.0.0.1` | Host anunciado ao Master |
| `--interval` | `2.0` | Intervalo entre ciclos (s) |
| `--min-task-seconds` | `1.0` | Tempo mínimo de processamento |
| `--max-task-seconds` | `3.0` | Tempo máximo de processamento |
