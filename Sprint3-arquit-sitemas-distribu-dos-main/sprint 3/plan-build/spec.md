# Sprint 3 Specification Sheet & Progress Tracker

**Contexto:** P2P com Balanceamento de Carga Dinâmico (CEUB 2026) [cite: 116, 118]  
**Abordagem:** Harness Engineering (Dual-Agent Collaboration)

## 1. Sumário do Histórico (Sprint 01 & 02) - Completo

- **Sprint 01:** Mecanismo de Heartbeat estruturado via TCP (`\n` como delimitador) [cite: 175, 183]. Payload de verificação `ALIVE` operacional [cite: 191].
- **Sprint 02:** Ciclo de vida de tarefas funcional [cite: 221]. O Master gerencia uma fila thread-safe (`deque` + `Lock`) [cite: 232]. O Worker solicita tarefas, processa (`QUERY`/`NO_TASK`), reporta `STATUS` (`OK`/`NOK`) e recebe `ACK` [cite: 234, 238, 241]. Suporte ao campo opcional `SERVER_UUID` integrado para nós emprestados [cite: 249].

## 2. Backlog de Tarefas da Sprint 03 (M2M) & Status de Progresso

Abaixo está o controle de estado para o Agente Executor e Agente Validador.

- [x] **TASK 00: Alinhamento Base**  
  Validar repositório e injetar funções base `send_json` e `recv_json` com delimitador `\n` [cite: 409].

- [x] **TASK 01: Servidor Híbrido Dual-Role (Master)**  
  Habilitar o Master para agir simultaneamente como Servidor, escutando Workers e outros Masters, e Cliente, conectando em vizinhos [cite: 407].

- [x] **TASK 02: Mecanismo de Saturação e Histerese**  
  Implementar thresholds de saturação (`capacity=100`) e liberação, com histerese para evitar efeito ping-pong [cite: 411, 412, 463].

- [x] **TASK 03: Protocolo de Negociação M2M**  
  Implementar envio de `request_help`, com UUID v4 em `request_id`, e tratamento de `response_accepted` / `response_rejected` [cite: 417, 418, 460].

- [x] **TASK 04: Redirecionamento Dinâmico**  
  Master A envia `command_redirect` para o Worker [cite: 423]. Worker encerra conexão graciosamente, conecta no novo Master e envia `register_temporary_worker` [cite: 424].

- [x] **TASK 05: Devolução Voluntária por Alívio de Carga**  
  Carga abaixo do threshold de liberação dispara `command_release` para o Worker e `notify_worker_returned` para o Master de origem [cite: 429, 430, 431].

- [x] **TASK 06: Resiliência de Rede e Timeouts**  
  Timeout de 5 segundos para resposta M2M [cite: 420]. Se o Master receptor cair, o Worker emprestado deve detectar a queda e retornar ao Master original de forma autônoma [cite: 436].

- [x] **TASK 07: Observabilidade e Strict Parsing**  
  Logs detalhados com timestamps, tipo e `request_id` [cite: 440]. Ignorar campos desconhecidos e falhar em campos obrigatórios ausentes [cite: 438, 458].

## 3. Matriz de Cobertura de Testes Exigida (Harness Target)

O Agente de Testes deve garantir sucesso absoluto nos seguintes cenários:

- [x] **CT01:** Pedido de ajuda aceito: Master B cede workers ociosos [cite: 455].
- [x] **CT02:** Pedido de ajuda recusado por alta carga: `reason = high_load` [cite: 455].
- [x] **CT03:** Correlação rigorosa de `request_id` em requisições concorrentes [cite: 455].
- [x] **CT04:** Registro e execução de ciclo de tarefas completo no Worker Emprestado [cite: 455].
- [x] **CT06:** Fluxo de devolução completa com Histerese [cite: 455, 463].
- [x] **CT07:** Timeout de 5 segundos disparando busca no próximo vizinho [cite: 456, 462].
- [x] **CT08:** Recuperação de estado consistente caso o Master saturado caia [cite: 456].

## 4. Decisões de Implementação

- O protocolo legado da Sprint 02 permanece suportado para `WORKER=ALIVE`, `QUERY`, `NO_TASK`, `STATUS=OK/NOK` e `ACK`.
- Mensagens novas da Sprint 03 usam o campo `type` em lowercase estrito.
- Todo payload M2M usa `request_id` UUID v4 para correlação.
- Campos desconhecidos devem ser ignorados para compatibilidade futura.
- Campos obrigatórios ausentes invalidam a mensagem e devem gerar log de erro.
