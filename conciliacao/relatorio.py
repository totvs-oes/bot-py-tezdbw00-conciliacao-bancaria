"""Relatório de divergências (markdown) e plano em JSON para auditoria."""
from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from conciliacao.modelos import ItemPlano, Plano, Tarifa, Transferencia, formatar_brl
from conciliacao.texto import normalizar


@dataclass
class ResultadoExecucao:
    """Preenchido pelo executor; vazio no modo plano/dry-run."""
    status_itens: dict[str, str] = field(default_factory=dict)       # chave -> status
    detalhes_itens: dict[str, str] = field(default_factory=dict)     # chave -> mensagem
    conciliacoes: dict[str, tuple[str, Optional[str]]] = field(default_factory=dict)  # conta -> (status, detalhe)
    erro_geral: Optional[str] = None


def _descricao(item: ItemPlano) -> str:
    if isinstance(item, Transferencia):
        return f"{item.origem} → {item.destino}"
    if isinstance(item, Tarifa):
        return str(item.conta)
    return ""


def gerar_markdown(plano: Plano, resultado: Optional[ResultadoExecucao] = None, simulado: bool = True) -> str:
    resultado = resultado or ResultadoExecucao()
    dia = f"{plano.data_movimento:%d/%m/%Y}"
    linhas = [f"# Conciliação bancária — movimento de {dia}", ""]
    if simulado:
        linhas += ["> **Simulação (dry-run)**: nada foi lançado no Protheus. Documentos são provisórios.", ""]
    if resultado.erro_geral:
        linhas += [f"> ❌ **Execução interrompida:** {resultado.erro_geral}", ""]

    linhas += [
        "## Resumo", "",
        "| Item | Quantidade |", "|---|---|",
        f"| Extratos lidos | {len(plano.extratos)} |",
        f"| Transferências | {len(plano.transferencias)} |",
        f"| Tarifas | {len(plano.tarifas)} |",
        f"| Rendimentos | {len(plano.rendimentos)} |",
        f"| Conciliações | {len(plano.conciliacoes)} |",
        f"| **Pendências** | **{len(plano.pendencias)}** |",
        f"| Fora do escopo (informativo) | {len(plano.fora_do_escopo)} |", "",
    ]

    linhas += ["## Extratos", "", "| Arquivo | Conta | Leitura | Conferência | Lançamentos no dia |", "|---|---|---|---|---|"]
    for extrato in plano.extratos:
        no_dia = sum(1 for l in extrato.lancamentos if l.data == plano.data_movimento)
        conferencia = {True: "ok", False: "**falhou**", None: "sem saldo"}[extrato.conferencia_ok]
        tipo = "detalhe de tarifas" if extrato.eh_detalhe_tarifas else ""
        conta = extrato.conta.chave if extrato.conta else "**não mapeada**"
        linhas.append(f"| {extrato.arquivo} {tipo} | {conta} | {extrato.metodo} | {conferencia} | {no_dia} |")
    linhas.append("")

    for titulo, itens in (("Transferências (Rotina 2)", plano.transferencias),
                          ("Tarifas (Rotina 3)", plano.tarifas),
                          ("Rendimentos (Rotina 4)", plano.rendimentos)):
        linhas += [f"## {titulo}", ""]
        if not itens:
            linhas += ["Nenhum.", ""]
            continue
        linhas += ["| Status | Documento | Contas | Valor | Histórico | Origem | Obs. |", "|---|---|---|---:|---|---|---|"]
        for item in itens:
            status = resultado.status_itens.get(item.chave, "simulado" if simulado else "não executado")
            obs = " ".join(filter(None, [item.aviso, resultado.detalhes_itens.get(item.chave)]))
            linhas.append(f"| {status} | {item.documento or ''} | {_descricao(item)} | {formatar_brl(item.valor)} | "
                          f"{item.historico} | {item.origem_extrato} | {obs} |")
        linhas.append("")

    linhas += ["## Conciliações (Rotina 5)", ""]
    if plano.conciliacoes:
        linhas += ["| Conta | Saldo do extrato | Status | Detalhe |", "|---|---:|---|---|"]
        for conciliacao in plano.conciliacoes:
            status, detalhe = resultado.conciliacoes.get(conciliacao.conta.chave,
                                                         ("simulado" if simulado else "não executado", None))
            linhas.append(f"| {conciliacao.conta.chave} ({conciliacao.conta}) | {formatar_brl(conciliacao.saldo_extrato)} "
                          f"| {status} | {detalhe or ''} |")
    else:
        linhas.append("Nenhuma.")
    linhas.append("")

    linhas += ["## Pendências (ação humana)", ""]
    if plano.pendencias:
        linhas += ["| Arquivo | Data | Histórico | Valor | Motivo | Ação |", "|---|---|---|---:|---|---|"]
        for p in plano.pendencias:
            data = f"{p.data:%d/%m/%Y}" if p.data else ""
            valor = formatar_brl(p.valor) if p.valor is not None else ""
            linhas.append(f"| {p.arquivo} | {data} | {p.historico or ''} | {valor} | {p.motivo} | {p.acao} |")
    else:
        linhas.append("Nenhuma. ✅")
    linhas.append("")

    if plano.fora_do_escopo:
        contagem = Counter((l.conta.chave, normalizar(l.historico)[:35], l.operacao) for l in plano.fora_do_escopo)
        linhas += ["## Fora do escopo do robô (informativo)", "", "| Conta | Histórico | Operação | Qtde |", "|---|---|---|---:|"]
        for (conta, historico, operacao), qtde in sorted(contagem.items()):
            linhas.append(f"| {conta} | {historico} | {operacao} | {qtde} |")
        linhas.append("")
    return "\n".join(linhas)


def plano_para_dict(plano: Plano) -> dict:
    def item(i: ItemPlano) -> dict:
        base = {"chave": i.chave, "rotina": i.rotina.value, "data": i.data.isoformat(), "valor": str(i.valor),
                "historico": i.historico, "documento": i.documento, "origem_extrato": i.origem_extrato, "aviso": i.aviso}
        if isinstance(i, Transferencia):
            base |= {"origem": i.origem.chave, "destino": i.destino.chave}
        else:
            base |= {"conta": i.conta.chave}
        return base

    return {
        "data_movimento": plano.data_movimento.isoformat(),
        "itens": [item(i) for i in plano.itens()],
        "conciliacoes": [{"conta": c.conta.chave, "saldo_extrato": str(c.saldo_extrato)} for c in plano.conciliacoes],
        "pendencias": [{"arquivo": p.arquivo, "data": p.data.isoformat() if p.data else None, "historico": p.historico,
                        "valor": str(p.valor) if p.valor is not None else None, "motivo": p.motivo, "acao": p.acao}
                       for p in plano.pendencias],
    }


def salvar(plano: Plano, pasta: Path, resultado: Optional[ResultadoExecucao] = None, simulado: bool = True) -> Path:
    pasta.mkdir(parents=True, exist_ok=True)
    (pasta / "plano.json").write_text(json.dumps(plano_para_dict(plano), ensure_ascii=False, indent=1), encoding="utf-8")
    caminho = pasta / "relatorio.md"
    caminho.write_text(gerar_markdown(plano, resultado, simulado), encoding="utf-8")
    return caminho
