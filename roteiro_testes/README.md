# Roteiro de testes automático (MIT045)

Roda um dia de movimento no robô, gera as evidências (prints) e acrescenta os roteiros na planilha
**Roteiro de Testes - MIT045**. É o mesmo processo que era feito à mão: executar, tirar prints, conferir o relatório e
documentar.

## Uso

Na pasta do robô, com o `.env` apontando para a **homologação**:

```
.venv\Scripts\python -m roteiro_testes --data-movimento 2026-09-15
.venv\Scripts\python -m roteiro_testes --data-movimento 2026-09-15 --modo executar --planilha "C:\...\Roteiro de Testes - MIT045.xlsx"
.venv\Scripts\python -m roteiro_testes --data-movimento 2026-09-15 --modo planejar --testes --planilha "...xlsx"
```

| Opção | O que faz |
|---|---|
| `--modo planejar` | Só lê os PDFs e monta o plano (não abre o Protheus) |
| `--modo ensaio` (padrão) | Abre o Protheus, preenche cada tela e **cancela** (não grava) |
| `--modo executar` | **Grava** no Protheus e concilia (use só na homologação) |
| `--limite N` / `--rotinas transferencia,tarifa` | Restringe a execução, como no `python -m conciliacao executar` |
| `--planilha ARQ` | Planilha MIT045 atual. A original **não é alterada**: sai uma cópia atualizada |
| `--testes` | Roda também os testes automatizados do robô e da API de leitura (`--repo-api`) |
| `--responsavel` | Nome no "Responsável TOTVS" (padrão: `git config user.name`) |

Pré-requisitos: os do próprio robô (API de leitura no ar, PDFs na `PASTA_EXTRATOS`, WebAgent e VPN para ensaio/executar)
e o Google Chrome (usado também para gerar as imagens das evidências).

## O que é gerado

Em `saida/evidencias/<data>_<modo>_<hora>/`:

- **PNGs** com o código do roteiro no nome (ex.: `0045_03_tarifa_341_150926_gravado.png`), e um `.txt` com o texto
  completo de cada imagem gerada a partir de console/relatório;
- `resumo.md`: tabela com código, status e observações de cada roteiro;
- `roteiros.json`: o mesmo, para outras ferramentas;
- `evidencias_para_o_drive.zip`: só as imagens, para subir no Drive;
- `<planilha> (atualizada AAAA-MM-DD HHMM).xlsx`: cópia da planilha com as linhas novas.

## Roteiros gerados

| Cenário | Roteiro | Status calculado a partir de |
|---|---|---|
| 001 Leitura de extratos | Leitura de cada PDF e conferência de saldo | `extratos_api.json`: conferência falhando = ajuste |
| 002 Planejamento do dia | Plano, resumo e pendências | código de saída do `planejar` e `relatorio.md` |
| 003 Acesso ao Protheus | Login, avisos, diálogo Moedas, data base | linhas do log (`Login concluído`, `Data base ...`) |
| 004 Lançamentos | Ensaio/execução: status de cada lançamento, recusas, falhas técnicas | código de saída e status no `relatorio.md` |
| 006 Conciliação | Saldo Protheus x extrato por conta | tabela de conciliações do `relatorio.md` |
| 008 Notificação | E-mail do relatório | linhas `conciliacao.notificacao` do log |
| 007 Qualidade | Testes automatizados (com `--testes`) | `pytest` |

A numeração continua a da planilha: se o cenário 004 já tem as ordens 1 a 5, o novo roteiro é o `0046`.
Cenário que não existir na aba Cenários é criado. A aba Evidências é criada se não existir.

**O que continua manual:** abrir ocorrências (aba Ocorrências) e colar os links do Drive (coluna "Link no Drive"). Quando
algum roteiro não tem êxito, o `resumo.md` avisa para avaliar a ocorrência.

## Como funciona

- `etapas.py` roda o robô como subprocesso (`python -m conciliacao planejar/executar`), o mesmo comando da operação, e
  interpreta console, `relatorio.md`, `extratos_api.json` e os prints que o robô já tira (`saida/<data>/prints*`).
- `evidencias.py` gera as imagens de texto (Chrome headless) e escolhe os prints desta execução (login, 1 lançamento por
  tipo e banco, contábil, recusas e conciliação).
- `xlsx.py` edita o `.xlsx` direto no XML: o openpyxl descartaria os gráficos e imagens do modelo MIT045.
- `mit045.py` sabe onde fica cada coisa na planilha (abas Início, Cenários, Roteiro e Evidências).

Testes: `tests/test_roteiro_testes.py`.
