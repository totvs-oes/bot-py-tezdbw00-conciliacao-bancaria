"""Roteiro de testes automático (MIT045): roda o dia no robô, gera as evidências e preenche a planilha.

    python -m roteiro_testes --data-movimento 2026-09-15                       # ensaio (preenche e cancela, não grava)
    python -m roteiro_testes --data-movimento 2026-09-15 --modo executar --planilha "Roteiro de Testes - MIT045.xlsx"
    python -m roteiro_testes --data-movimento 2026-09-15 --modo planejar --testes   # sem Protheus + testes automatizados

Saída em saida/evidencias/<data>_<modo>_<hora>/: PNGs, resumo.md, roteiros.json, zip para o Drive e a cópia atualizada
da planilha (a original não é alterada).
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import zipfile
from dataclasses import asdict
from datetime import date, datetime
from pathlib import Path

from roteiro_testes import etapas
from roteiro_testes.evidencias import Renderizador
from roteiro_testes.mit045 import EXITO, Numeracao, Roteiro, gravar

MODOS = ("planejar", "ensaio", "executar")


def _responsavel_padrao() -> str:
    try:
        return subprocess.run(["git", "config", "user.name"], capture_output=True, text=True, encoding="utf-8",
                              errors="replace", cwd=etapas.RAIZ).stdout.strip()
    except OSError:
        return ""


def _argumentos(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="python -m roteiro_testes", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--data-movimento", required=True, type=date.fromisoformat, help="AAAA-MM-DD")
    p.add_argument("--modo", choices=MODOS, default="ensaio",
                   help="planejar = só leitura e plano; ensaio = Protheus sem gravar (padrão); executar = grava")
    p.add_argument("--limite", type=int, help="No máximo N lançamentos (ensaio/executar)")
    p.add_argument("--rotinas", help="Ex.: transferencia,tarifa (padrão: todas)")
    p.add_argument("--planilha", type=Path, help="Planilha MIT045 atual: a cópia atualizada vai para a pasta de evidências")
    p.add_argument("--saida-planilha", type=Path, help="Onde gravar a planilha atualizada (padrão: na pasta de evidências)")
    p.add_argument("--responsavel", default=_responsavel_padrao(), help="Responsável TOTVS (padrão: git user.name)")
    p.add_argument("--testes", action="store_true", help="Roda também os testes automatizados (robô e API de leitura)")
    p.add_argument("--repo-api", type=Path, default=etapas.RAIZ.parent / "svc-py-tezdbw00-leitura-extratos",
                   help="Repositório da API de leitura (para --testes)")
    return p.parse_args(argv)


def _resumo_md(args, roteiros: list[Roteiro], pasta: Path, planilha: Path | None) -> str:
    linhas = [f"# Roteiro de testes - movimento de {args.data_movimento:%d/%m/%Y} ({args.modo})", "",
              f"Gerado em {datetime.now():%d/%m/%Y %H:%M} por {args.responsavel or '-'}.", "",
              "| Código | Processo | Subprocesso | Status | Observações |", "|---|---|---|---|---|"]
    for r in roteiros:
        linhas.append(f"| {r.codigo} | {r.processo} | {r.subprocesso} | {r.status} | {r.observacoes.replace('|', '/')} |")
    linhas += ["", f"Evidências: {sum(len(r.evidencias) for r in roteiros)} arquivos em `{pasta}`."]
    if planilha:
        linhas.append(f"Planilha atualizada: `{planilha}` (preencha a coluna \"Link no Drive\" da aba Evidências).")
    if any(r.status != EXITO for r in roteiros):
        linhas += ["", "Há roteiros sem êxito: avalie se é o caso de abrir ocorrência na aba Ocorrências."]
    return "\n".join(linhas) + "\n"


def main(argv: list[str] | None = None) -> int:
    args = _argumentos(sys.argv[1:] if argv is None else argv)
    if args.planilha and not args.planilha.exists():
        print(f"Planilha não encontrada: {args.planilha}")
        return 2
    from conciliacao.configuracao import carregar_ambiente
    ambiente = carregar_ambiente()
    pasta = ambiente.pasta_saida / "evidencias" / f"{args.data_movimento.isoformat()}_{args.modo}_{datetime.now():%H%M%S}"
    pasta.mkdir(parents=True, exist_ok=True)
    numeracao = Numeracao.da_planilha(args.planilha)
    if args.modo == "executar":
        print("ATENÇÃO: modo executar GRAVA no Protheus configurado no .env (use a homologação).")

    print(f"Roteiro de testes de {args.data_movimento:%d/%m/%Y} ({args.modo}) -> {pasta}")
    with Renderizador(ambiente.protheus_navegador or "chrome") as render:
        ctx = etapas.Contexto(args.data_movimento, args.modo, pasta, ambiente.pasta_saida, render, numeracao.reservar,
                              args.limite, args.rotinas)
        print("1) Plano do dia (leitura dos PDFs)...")
        roteiros, plano_ok = etapas.plano(ctx)
        if plano_ok and args.modo != "planejar":
            print(f"2) {args.modo.capitalize()} no Protheus (pode levar horas no dia completo)...")
            roteiros += etapas.protheus(ctx)
        if args.testes:
            print("3) Testes automatizados...")
            roteiros.append(etapas.testes(ctx, args.repo_api if args.repo_api.exists() else None))

    destino = None
    if args.planilha:
        destino = args.saida_planilha or pasta / f"{args.planilha.stem} (atualizada {datetime.now():%Y-%m-%d %H%M}).xlsx"
        gravar(args.planilha, destino, roteiros, date.today(), args.responsavel,
               f"testes automáticos do movimento de {args.data_movimento:%d/%m/%Y} ({args.modo})")
    (pasta / "roteiros.json").write_text(json.dumps([asdict(r) for r in roteiros], ensure_ascii=False, indent=1),
                                         encoding="utf-8")
    (pasta / "resumo.md").write_text(_resumo_md(args, roteiros, pasta, destino), encoding="utf-8")
    with zipfile.ZipFile(pasta / "evidencias_para_o_drive.zip", "w", zipfile.ZIP_DEFLATED) as z:
        for r in roteiros:
            for e in r.evidencias:
                z.write(pasta / e.arquivo, e.arquivo)

    print()
    for r in roteiros:
        print(f"  {r.codigo}  {r.status:38} {r.processo} - {r.subprocesso}")
    print(f"\nResumo: {pasta / 'resumo.md'}" + (f"\nPlanilha: {destino}" if destino else ""))
    return 0 if all(r.status == EXITO for r in roteiros) else 1


if __name__ == "__main__":
    sys.exit(main())
