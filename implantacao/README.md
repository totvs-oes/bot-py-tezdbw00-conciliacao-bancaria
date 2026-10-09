# Implantação no servidor da cliente (Windows, sem Docker)

O robô e a API de leitura rodam direto no Windows Server da cliente: sem Docker (a VM não tem virtualização
aninhada) e sem SFTP/VPN (`FONTE_EXTRATOS=local`). Pastas esperadas:

```
C:\RPA\bot-py-tezdbw00-conciliacao-bancaria      (este repositório, com .venv)
C:\RPA\svc-py-tezdbw00-leitura-extratos          (API de leitura, com venv)
```

Tudo abaixo é feito **logado como o usuário do robô** (`rpa.aeroflex`): as credenciais e as tarefas ficam no perfil dele.

## 1. Credenciais no Gerenciador de Credenciais do Windows

Os segredos saem do `.env` e vão para o Gerenciador de Credenciais (credencial genérica `AEROFLEX RPA/<NOME>`).
O `.env` fica só com configurações (URLs, pastas, e-mails, feriados).

```
cd C:\RPA\bot-py-tezdbw00-conciliacao-bancaria
.venv\Scripts\python -m conciliacao.credenciais importar-env     # copia do .env para o Gerenciador e apaga do .env
.venv\Scripts\python -m conciliacao.credenciais listar           # onde está cada um (sem mostrar o valor)

cd C:\RPA\svc-py-tezdbw00-leitura-extratos
venv\Scripts\python -m services.credenciais importar-env
venv\Scripts\python -m services.credenciais listar
```

| Projeto | Segredos |
|---|---|
| Robô | `PROTHEUS_USUARIO`, `PROTHEUS_SENHA`, `SMTP_USUARIO`, `SMTP_SENHA`, `API_EXTRATOS_TOKEN`, `RPA_API_TOKEN` |
| API de leitura | `TOKEN`, `TOKEN_API_LLM`, `SFTP_USUARIO`, `SFTP_SENHA` |

Trocar uma senha: `... -m conciliacao.credenciais definir PROTHEUS_SENHA` (digitada sem eco). Também dá para ver e editar
em Painel de Controle > Gerenciador de Credenciais > Credenciais do Windows > "AEROFLEX RPA/...".

Ordem de leitura no código: variável de ambiente / `.env` (se preenchido) e depois o Gerenciador. Por isso Docker e
desenvolvimento continuam funcionando com o `.env`.

## 2. Tarefas no Agendador de Tarefas

Importe os dois arquivos em **Agendador de Tarefas > Ação > Importar tarefa...** (ou
`schtasks /Create /XML implantacao\tarefa_robo.xml /TN "RPA Conciliacao AEROFLEX - Robo"`):

| Arquivo | Tarefa | Quando | Estado |
|---|---|---|---|
| `tarefa_api_leitura.xml` | API de leitura em `127.0.0.1:5000`, sem janela (`executar_api.py`), log em `logs\api.log` | todo dia 07:50 (se já estiver no ar, não sobe outra); reinicia até 3x se cair | habilitada |
| `tarefa_robo.xml` | `python -m conciliacao executar` (movimento do dia útil anterior) | seg a sex 08:00 | **desabilitada** |

Na importação, confira na aba Geral o usuário (`rpa.aeroflex`) e "Executar somente quando o usuário estiver conectado".
Os caminhos das ações são `C:\RPA\...`: ajuste na aba Ações se as pastas forem outras.

- Habilitar depois da homologação: botão direito na tarefa do robô > **Habilitar**.
- Testar agora: botão direito > **Executar**.
- Log do robô: `saida\AAAA-MM-DD\execucao.log`; o histórico de cada execução aparece na aba Histórico da tarefa.
- Fim de semana e feriados (`FERIADOS` do `.env`): o robô termina em seguida, sem processar nada.
- Segunda-feira processa a sexta.

No `.env` do robô: `API_EXTRATOS_URL=http://127.0.0.1:5000/extratos` (no Windows, `localhost` resolve primeiro para o
IPv6 `::1`, e a API escuta só no IPv4).

## Sessão do usuário

As tarefas rodam **só com a sessão do `rpa.aeroflex` aberta**: o TOTVS WebAgent e o Chrome precisam dela. Ao sair do
servidor, feche a janela da Área de Trabalho Remota (desconectar), **não faça logoff**. Confira também se o WebAgent
abre junto com o logon do usuário (ícone na bandeja).
