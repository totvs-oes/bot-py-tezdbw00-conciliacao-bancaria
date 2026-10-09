# Implantação no servidor da cliente (Windows, sem Docker)

O robô e a API de leitura rodam direto no Windows Server da cliente (sem Docker: a VM não tem virtualização aninhada;
sem SFTP/VPN: `FONTE_EXTRATOS=local`). Pastas esperadas, lado a lado:

```
C:\RPA\bot-py-tezdbw00-conciliacao-bancaria      (este repositório, com .venv)
C:\RPA\svc-py-tezdbw00-leitura-extratos          (API de leitura, com venv)
```

## Execução diária: `rodar_dia.ps1`

1. Sobe o TOTVS WebAgent se ele não estiver no ar (porta 21021).
2. Sobe a API de leitura em `127.0.0.1:5000` (uvicorn sem reload) se ela não estiver no ar.
3. Roda `python -m conciliacao executar` (data padrão: dia útil anterior, com os `FERIADOS` do `.env`).
4. Desliga a API se foi o script que a ligou. Em feriado do `.env` não faz nada.

Logs em `saida\agendador\`: `<data_hora>.log` (resumo), `_robo.log` (log do robô), `_api.*.log` (API).
Código de saída: 0 ok, 1 com pendências, 2 falha, 3 a API não subiu.

Teste manual:
```
powershell -ExecutionPolicy Bypass -File implantacao\rodar_dia.ps1 -Modo ensaio -DataMovimento 2026-09-15
```

No `.env` do robô use `API_EXTRATOS_URL=http://127.0.0.1:5000/extratos` (no Windows, `localhost` resolve primeiro para
o IPv6 `::1`, e a API escuta só no IPv4).

## Agendamento: `registrar_tarefa.ps1`

Cria a tarefa "RPA Conciliacao AEROFLEX" no Agendador de Tarefas, **segunda a sexta às 08:00, desabilitada**:
```
powershell -ExecutionPolicy Bypass -File implantacao\registrar_tarefa.ps1                 # 08:00, desabilitada
powershell -ExecutionPolicy Bypass -File implantacao\registrar_tarefa.ps1 -Hora 07:30
Enable-ScheduledTask  -TaskName "RPA Conciliacao AEROFLEX"                                 # depois da homologação
Start-ScheduledTask   -TaskName "RPA Conciliacao AEROFLEX"                                 # rodar agora
```

Rode como o usuário do robô (`rpa.aeroflex`). A tarefa roda **só com a sessão desse usuário aberta** (pode ser uma
sessão de Área de Trabalho Remota desconectada; não faça logoff): o WebAgent e o Chrome precisam da sessão.
Segunda-feira processa a sexta; sábado e domingo não rodam.

Os `.ps1` ficam sem acento: o Windows PowerShell 5.1 lê arquivo sem BOM como ANSI.
