# Atividade de Sistemas Distribuídos

## Teste de comunicação concorrente entre Master, Worker e Worker2

Esta atividade implementa uma arquitetura simples de **sistemas distribuídos** baseada em sockets TCP, com um processo **Master** centralizando as conexões e dois processos **Workers** enviando mensagens periódicas de *heartbeat*.

### Objetivo
Verificar se o `Worker2` consegue funcionar **ao mesmo tempo** que o `Worker`, comunicando-se com o `Master` sem bloquear o outro processo.

### Estrutura
- `Master.py`: servidor principal que escuta conexões na porta `5000` e cria uma **thread separada para cada conexão recebida**.
- `Worker.py`: cliente que envia mensagens `HEARTBEAT` ao Master.
- `Worker2.py`: segundo cliente, com comportamento equivalente ao primeiro, usando outro identificador.

## Resultado do teste
O teste confirmou que **sim, o Worker2 funciona simultaneamente ao Worker** ao tentar se comunicar com o Master.

### Evidências observadas
Durante a execução:
- o `Master` recebeu heartbeats dos dois workers;
- cada conexão foi tratada sem interromper a outra;
- ambos receberam a resposta `ALIVE` do Master;
- isso mostra que o uso de **threads no Master** permitiu atendimento concorrente.

### Saída resumida do teste
Exemplo do comportamento observado:

```text
[+] Heartbeat recebido de: WORKER-Marlon-....
[->] Resposta 'ALIVE' enviada para WORKER-Marlon-....
[+] Heartbeat recebido de: WORKER-Lucas-....
[->] Resposta 'ALIVE' enviada para WORKER-Lucas-....
```

Isso indica que os dois workers conseguiram estabelecer conexão e trocar mensagens com o Master dentro do mesmo período de execução.

Ou seja:
- **a lógica do sistema funciona**;
- para rodar em outra máquina ou laboratório, é preciso ajustar o IP para o endereço válido do host onde o `Master` estiver sendo executado.

## Como executar
### 1. Iniciar o Master
```bash
python Master.py
```

### 2. Iniciar o Worker
```bash
python Worker.py
```

### 3. Iniciar o Worker2
```bash
python Worker2.py
```

## Conclusão
O sistema demonstrou que o `Worker` e o `Worker2` conseguem operar em paralelo com o `Master`, validando a proposta da atividade. O principal motivo para isso é o uso de **threads no servidor**, permitindo múltiplas conexões quase simultâneas.

## Colaboradores
- Marlon Paganotto
- Rodrigo Criszostimo
- Lucas Adriano Rodrigues
