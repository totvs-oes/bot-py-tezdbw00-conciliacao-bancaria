"""Etapas do roteiro de testes: roda o robô (python -m conciliacao ...) e transforma o resultado em roteiros + evidências.

O robô roda como subprocesso, exatamente como na operação: o que é testado é o mesmo comando do dia a dia. As
evidências saem do console, do relatório (saida/<data>/relatorio.md), da leitura da API (extratos_api.json) e dos
prints que o robô já tira no Protheus.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Callable, Optional

from roteiro_testes.evidencias import Evidencia, Renderizador, copiar, recorte, selecionar_prints
from roteiro_testes.mit045 import AJUSTE, ERRO, EXITO, NAO_INICIADO, Roteiro

RAIZ = Path(__file__).resolve().parent.parent
RE_LOG = re.compile(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}),\d+ (\w+) ([\w.]+): (.*)$")


@dataclass
class Contexto:
    data: date
    modo: str                         # planejar | ensaio | executar
    pasta_evidencias: Path
    pasta_saida_robo: Path            # saida/ do robô (PASTA_SAIDA)
    render: Renderizador
    reservar: Callable[[Roteiro], Roteiro]   # dá o código do roteiro (continua a numeração da planilha)
    limite: Optional[int] = None
    rotinas: Optional[str] = None

    @property
    def dia(self) -> str:
        return self.data.strftime("%d/%m/%Y")

    @property
    def pasta_dia(self) -> Path:
        return self.pasta_saida_robo / self.data.isoformat()


@dataclass
class Execucao:
    codigo: int
    saida: str
    inicio: datetime
    fim: datetime

    @property
    def segundos(self) -> int:
        return int((self.fim - self.inicio).total_seconds())

    def linhas(self, trecho: str) -> list[str]:
        return [l for l in self.saida.splitlines() if trecho in l]


def rodar(argumentos: list[str], cwd: Path = RAIZ, python: Optional[str] = None) -> Execucao:
    inicio = datetime.now()
    ambiente = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    proc = subprocess.run([python or sys.executable, *argumentos], cwd=cwd, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", env=ambiente)
    return Execucao(proc.returncode, (proc.stdout or "") + (proc.stderr or ""), inicio, datetime.now())


def _console(execucao: Execucao, comando: str) -> str:
    """Console sem as linhas repetitivas de HTTP (o essencial para a evidência)."""
    linhas = [l for l in execucao.saida.splitlines() if "httpx:" not in l and "Enviando " not in l]
    return "\n".join(linhas + [f"\n[código de saída: {execucao.codigo} | duração: {execucao.segundos // 60} min "
                               f"{execucao.segundos % 60} s]"])


def _png(ctx: Contexto, roteiro: Roteiro, nome: str, titulo: str, legenda: str, texto: str, descricao: str) -> Evidencia:
    arquivo = f"{roteiro.codigo}_{nome}.png"
    ctx.render.texto(ctx.pasta_evidencias / arquivo, f"MIT045 · {roteiro.codigo} - {titulo}", legenda, texto)
    return Evidencia(arquivo, descricao)


def _relatorio(ctx: Contexto) -> str:
    caminho = ctx.pasta_dia / "relatorio.md"
    return caminho.read_text(encoding="utf-8") if caminho.exists() else ""


def _tabela(md: str) -> list[list[str]]:
    """Linhas de dados de uma tabela markdown (sem cabeçalho e separador)."""
    linhas = [l for l in md.splitlines() if l.startswith("|")]
    return [[c.strip() for c in l.strip().strip("|").split("|")] for l in linhas[2:]]


# ---------------------------------------------------------------------------------------------------------------------
# 1) Plano do dia: leitura dos PDFs (cenário 001) e planejamento (cenário 002)
# ---------------------------------------------------------------------------------------------------------------------
def plano(ctx: Contexto) -> tuple[list[Roteiro], bool]:
    """Devolve os roteiros e se o plano foi gerado (sem plano não há execução no Protheus)."""
    comando = f"python -m conciliacao planejar --data-movimento {ctx.data.isoformat()}"
    execucao = rodar(["-m", "conciliacao", "planejar", "--data-movimento", ctx.data.isoformat()])
    pasta = next((l.split(" extrato(s) em ", 1)[1] for l in execucao.linhas(" extrato(s) em ")), "?")

    planejamento = ctx.reservar(Roteiro(
        "002", "Planejamento", f"Plano do dia {ctx.dia}",
        f"Rodar \"planejar\" do movimento de {ctx.dia}: o robô busca os PDFs da pasta do dia, envia à API de leitura e "
        "monta o plano, sem abrir o Protheus.",
        "Plano e relatório gerados, com lançamentos, saldo esperado por conta e pendências com motivo e ação.", EXITO, ""))
    planejamento.evidencias.append(_png(ctx, planejamento, "planejar", f"Plano do dia {ctx.dia}", comando,
                                        _console(execucao, comando), "Execução do planejar (console)"))
    md = _relatorio(ctx)
    if execucao.codigo != 0 or not md:
        ultima = next((l for l in reversed(execucao.saida.splitlines()) if l.strip()), "")
        planejamento.status, planejamento.observacoes = ERRO, f"O plano não foi gerado: {ultima[:300]}"
        return [planejamento], False

    resumo = {l[0]: l[1] for l in _tabela(recorte(md, "## Resumo", "## Extratos"))}
    pendencias = _tabela(recorte(md, "## Pendências", "## Fora"))
    planejamento.observacoes = (
        f"Pasta: {pasta}. Extratos lidos: {resumo.get('Extratos lidos')}; transferências: {resumo.get('Transferências')}; "
        f"tarifas: {resumo.get('Tarifas')}; rendimentos: {resumo.get('Rendimentos')}; conciliações: "
        f"{resumo.get('Conciliações')}; pendências: {len(pendencias)}.")
    planejamento.evidencias.append(_png(ctx, planejamento, "relatorio_resumo", f"Relatório do plano de {ctx.dia}",
                                        "relatorio.md - Resumo e extratos lidos",
                                        recorte(md, "## Resumo", "## Transferências"), "Relatório: resumo e extratos lidos"))
    if pendencias:
        texto = "\n".join(f"{p[0][:34]:34} {p[2][:40]:40} {p[3]:>14}  {p[4][:90]}" for p in pendencias if len(p) >= 5)
        planejamento.evidencias.append(_png(ctx, planejamento, "pendencias", f"Pendências de {ctx.dia}",
                                            "relatorio.md - Pendências (ação humana)", texto,
                                            "Pendências com motivo e ação"))
    return [_leitura(ctx), planejamento], True


def _leitura(ctx: Contexto) -> Roteiro:
    roteiro = ctx.reservar(Roteiro(
        "001", "Leitura de extratos", f"Extratos do movimento {ctx.dia}",
        f"Ler pela API todos os PDFs da pasta do movimento de {ctx.dia} (extratos e relatórios de tarifas).",
        "Todos lidos; saldo anterior + créditos − débitos = saldo final, ao centavo, nos extratos que trazem saldo.",
        EXITO, ""))
    caminho = ctx.pasta_dia / "extratos_api.json"
    resposta = json.loads(caminho.read_text(encoding="utf-8")) if caminho.exists() else {}
    linhas, contagem = [], Counter()
    for chave, extratos in resposta.items():
        for e in extratos:
            conf = e.get("conferencia") or {}
            ok = conf.get("ok")
            contagem["total"] += 1
            contagem["ia" if e.get("metodo") == "ia" else "layout"] += 1
            contagem["com_saldo"] += ok is not None
            contagem["ok"] += ok is True
            contagem["falhou"] += ok is False
            linhas.append(f"{str(e.get('arquivo'))[:42]:42} {chave:12} {str(e.get('conta'))[:18]:18} "
                          f"{str(e.get('metodo'))[:22]:22} {len(e.get('lancamentos', [])):>5} "
                          f"{'sem saldo' if ok is None else 'ok' if ok else 'FALHOU':>9}")
    cabecalho = f"{'Arquivo':42} {'Banco':12} {'Conta':18} {'Leitura':22} {'Lanç.':>5} {'Conferência':>9}\n" + "-" * 114
    rodape = (f"\n{'-' * 114}\n{contagem['total']} PDFs | leitor fixo: {contagem['layout']} | IA: {contagem['ia']} | "
              f"com saldo: {contagem['com_saldo']}, conferem: {contagem['ok']}, falharam: {contagem['falhou']}")
    roteiro.evidencias.append(_png(ctx, roteiro, "leitura", f"Leitura dos extratos de {ctx.dia}",
                                   f"{caminho} (resposta da API de leitura)", cabecalho + "\n" + "\n".join(linhas) + rodape,
                                   "Resultado da leitura de cada PDF e da conferência de saldo"))
    roteiro.observacoes = (f"{contagem['total']} PDFs: {contagem['layout']} pelo leitor fixo, {contagem['ia']} pela IA. "
                           f"Com saldo: {contagem['ok']}/{contagem['com_saldo']} conferem.")
    if contagem["falhou"]:
        roteiro.status = AJUSTE
        roteiro.observacoes += f" {contagem['falhou']} com conferência falhando: a conta não é lançada (vira pendência)."
    return roteiro


# ---------------------------------------------------------------------------------------------------------------------
# 2) Protheus: login (003), lançamentos (004), conciliação (006) e e-mail (008)
# ---------------------------------------------------------------------------------------------------------------------
def protheus(ctx: Contexto) -> list[Roteiro]:
    argumentos = ["-m", "conciliacao", "executar", "--data-movimento", ctx.data.isoformat()]
    if ctx.modo == "ensaio":
        argumentos.append("--ensaio")
    if ctx.limite:
        argumentos += ["--limite", str(ctx.limite)]
    if ctx.rotinas:
        argumentos += ["--rotinas", ctx.rotinas]
    comando = "python " + " ".join(argumentos)
    execucao = rodar(argumentos)
    prints = selecionar_prints(ctx.pasta_dia / ("prints_ensaio" if ctx.modo == "ensaio" else "prints"), execucao.inicio)
    md = _relatorio(ctx)
    return [_login(ctx, execucao, prints), _lancamentos(ctx, execucao, prints, md, comando),
            _conciliacao(ctx, execucao, prints, md), _email(ctx, execucao)]


def _primeiro_erro(execucao: Execucao) -> str:
    """Primeira linha de erro do log, sem o prefixo de data (ex.: "Page.goto: net::ERR_CONNECTION_TIMED_OUT")."""
    for linha in execucao.saida.splitlines():
        if (m := RE_LOG.match(linha)) and m.group(2) == "ERROR":
            return m.group(4)[:300]
    return ""


def _segundos(linha: str) -> Optional[datetime]:
    m = RE_LOG.match(linha)
    return datetime.strptime(m.group(1), "%Y-%m-%d %H:%M:%S") if m else None


def _login(ctx: Contexto, execucao: Execucao, prints) -> Roteiro:
    roteiro = ctx.reservar(Roteiro(
        "003", "Acesso ao Protheus", f"Login e data base ({ctx.dia})",
        "Login automático no WebApp (usuário e senha do .env), Grupo/Filial/Ambiente, avisos e data base do movimento.",
        f"Login concluído, avisos fechados e data base {ctx.dia} no cabeçalho.", EXITO, ""))
    inicio = next((_segundos(l) for l in execucao.linhas("Login no Protheus")), None)
    fim = next((_segundos(l) for l in execucao.linhas("Login concluído")), None)
    data_base = execucao.linhas("Data base alterada para") + execucao.linhas("Data base já é")
    if fim:
        partes = [f"Login em {(fim - inicio).seconds if inicio else '?'} s"]
        partes.append(f"avisos fechados: {len(execucao.linhas('Aviso do Protheus fechado'))}")
        if execucao.linhas("Diálogo 'Moedas'"):
            partes.append("diálogo de cotações (Moedas) cancelado")
        partes.append("data base ok" if data_base else "data base NÃO confirmada")
        roteiro.observacoes = "; ".join(partes) + "."
        if not data_base:
            roteiro.status = AJUSTE
    else:
        roteiro.status = ERRO
        roteiro.observacoes = f"Login não concluído. {_primeiro_erro(execucao)}"
    roteiro.evidencias = copiar(prints.login, ctx.pasta_evidencias, roteiro.codigo)
    return roteiro


def _status_dos_itens(md: str) -> Counter:
    corpo = recorte(md, "## Transferências", "## Conciliações")
    return Counter(m.group(1) for m in re.finditer(r"^\| (\w+) \| ", corpo, re.M) if m.group(1) != "Status")


def _lancamentos(ctx: Contexto, execucao: Execucao, prints, md: str, comando: str) -> Roteiro:
    ensaio = ctx.modo == "ensaio"
    roteiro = ctx.reservar(Roteiro(
        "004", "Movimento Bancário", f"{'Ensaio' if ensaio else 'Execução'} do dia {ctx.dia}",
        f"{'Ensaio (preenche e cancela, sem gravar)' if ensaio else 'Gravar'} os lançamentos do plano de {ctx.dia}"
        + (f" (no máximo {ctx.limite})" if ctx.limite else "") + (f", rotinas {ctx.rotinas}" if ctx.rotinas else "") + ".",
        "Formulários preenchidos com contas, natureza, documento, valor e histórico do plano"
        + ("; nada gravado." if ensaio else "; lançamentos gravados com documento e contábil."), EXITO, ""))
    status = _status_dos_itens(md)
    processados = len([l for l in execucao.linhas("conciliacao.executor: ") if " INFO " in l])
    recusas = [l for l in execucao.linhas("Protheus recusou")]
    tecnicas = execucao.linhas("Falha técnica")
    roteiro.observacoes = (
        f"Nesta execução: {processados} lançamentos processados em {execucao.segundos // 60} min. No dia: "
        + ", ".join(f"{v} {k}" for k, v in sorted(status.items())) + "."
        + (f" Recusas do Protheus: {len(recusas)} (" + "; ".join(r.split('Protheus recusou: ', 1)[1][:80] for r in recusas[:3])
           + ")." if recusas else "")
        + (f" Falhas técnicas: {len(tecnicas)}." if tecnicas else ""))
    if execucao.codigo == 2:
        roteiro.status = ERRO
        roteiro.observacoes += f" A execução terminou em FALHA (TI notificada por e-mail): {_primeiro_erro(execucao)}"
    elif status.get("erro") or status.get("incerto") or recusas:
        roteiro.status = AJUSTE
    roteiro.evidencias.append(_png(ctx, roteiro, "console", f"{'Ensaio' if ensaio else 'Execução'} de {ctx.dia}", comando,
                                   _console(execucao, comando), "Log da execução no Protheus"))
    itens = recorte(md, "## Transferências", "## Conciliações")
    roteiro.evidencias.append(_png(ctx, roteiro, "relatorio_lancamentos", f"Lançamentos de {ctx.dia}",
                                   "relatorio.md - status de cada lançamento", itens,
                                   "Relatório: status e documento de cada lançamento"))
    n = len(roteiro.evidencias)
    roteiro.evidencias += copiar(prints.lancamentos + prints.falhas, ctx.pasta_evidencias, roteiro.codigo, n + 1)
    return roteiro


def _conciliacao(ctx: Contexto, execucao: Execucao, prints, md: str) -> Roteiro:
    roteiro = ctx.reservar(Roteiro(
        "006", "Conciliação", f"Saldos e conciliação de {ctx.dia}",
        "Comparar no Novo Conciliador Backoffice o saldo do Protheus de cada conta com o saldo do extrato no fim do dia.",
        "Conta com saldo igual é conciliada; com diferença, não é, e a diferença vai para o relatório.", EXITO, ""))
    tabela = _tabela(recorte(md, "## Conciliações", "## Pendências"))
    contagem = Counter(l[2] for l in tabela if len(l) >= 3)
    roteiro.observacoes = (f"{len(tabela)} contas: " + ", ".join(f"{v} {k}" for k, v in sorted(contagem.items())) + ". "
                           "Divergência é esperada na homologação quando a base não tem os lançamentos reais.")
    if execucao.codigo == 2:
        roteiro.status = NAO_INICIADO
        roteiro.observacoes = "Não executada: a execução no Protheus terminou em falha antes da conciliação."
    elif not tabela:
        roteiro.status, roteiro.observacoes = AJUSTE, "Nenhuma conciliação no relatório."
    elif all(l[2].startswith("não execut") for l in tabela if len(l) >= 3):
        roteiro.status = AJUSTE
        roteiro.observacoes = f"Nenhuma das {len(tabela)} contas foi conciliada nem comparada (lançamentos pendentes)."
    texto = "\n".join(f"{l[0][:44]:44} {l[1]:>16}  {l[2]:14} {l[3][:90] if len(l) > 3 else ''}" for l in tabela)
    roteiro.evidencias.append(_png(ctx, roteiro, "relatorio_conciliacoes", f"Conciliações de {ctx.dia}",
                                   "relatorio.md - Conciliações (saldo do extrato, status e diferença)", texto,
                                   "Relatório: saldo Protheus x extrato por conta"))
    roteiro.evidencias += copiar(prints.conciliacao, ctx.pasta_evidencias, roteiro.codigo, 2)
    return roteiro


def _email(ctx: Contexto, execucao: Execucao) -> Roteiro:
    roteiro = ctx.reservar(Roteiro(
        "008", "Notificação", f"E-mail do relatório ({ctx.dia})",
        "Conferir o envio do relatório por e-mail ao fim da execução.",
        "E-mail enviado com o resumo (pendências e contas não conciliadas) e o relatório anexo.", EXITO, ""))
    linhas = execucao.linhas("conciliacao.notificacao")
    if any("Falha ao enviar" in l for l in linhas):
        roteiro.status = ERRO
    elif not any("E-mail enviado" in l for l in linhas):
        roteiro.status = AJUSTE
    roteiro.observacoes = "; ".join(l.split("conciliacao.notificacao: ", 1)[-1][:200] for l in linhas) or "Sem registro de e-mail."
    roteiro.evidencias.append(_png(ctx, roteiro, "email", f"E-mail de {ctx.dia}", "execucao.log - notificação",
                                   "\n".join(linhas) or "(nenhuma linha de notificação)", "Envio do e-mail registrado no log"))
    return roteiro


# ---------------------------------------------------------------------------------------------------------------------
# 3) Testes automatizados (007)
# ---------------------------------------------------------------------------------------------------------------------
def testes(ctx: Contexto, repo_api: Optional[Path]) -> Roteiro:
    roteiro = ctx.reservar(Roteiro(
        "007", "Qualidade", "Testes automatizados",
        "Rodar os testes automatizados do robô" + (" e da API de leitura." if repo_api else "."),
        "Todos os testes passam.", EXITO, ""))
    alvos = [("robo", "Robô", RAIZ, None)]
    if repo_api:
        python_api = next((str(p) for p in (repo_api / "venv/Scripts/python.exe", repo_api / ".venv/Scripts/python.exe",
                                            repo_api / "venv/bin/python", repo_api / ".venv/bin/python") if p.exists()), None)
        alvos.append(("api", "API de leitura", repo_api, python_api))
    resumos = []
    for nome, rotulo, pasta, python in alvos:
        execucao = rodar(["-m", "pytest", "-q", "-p", "no:cacheprovider"], cwd=pasta, python=python)
        ultima = next((l for l in reversed(execucao.saida.splitlines()) if "passed" in l or "failed" in l or "error" in l), "")
        resumos.append(f"{rotulo}: {ultima.strip('= ')}")
        if execucao.codigo != 0:
            roteiro.status = ERRO
        roteiro.evidencias.append(_png(ctx, roteiro, f"testes_{nome}", f"Testes automatizados - {rotulo}",
                                       "python -m pytest -q", "\n".join(execucao.saida.splitlines()[-25:]),
                                       f"Resultado dos testes automatizados ({rotulo})"))
    roteiro.observacoes = ". ".join(resumos) + "."
    return roteiro
