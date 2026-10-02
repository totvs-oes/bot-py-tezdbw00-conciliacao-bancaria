"""CLI do RPA de conciliação.

    python -m conciliacao planejar  [--data-movimento AAAA-MM-DD] [--pasta DIR | --extratos-json ARQ]
    python -m conciliacao executar  [--data-movimento AAAA-MM-DD] [--rotinas transferencia,tarifa,...] [--dry-run | --ensaio] [--limite N]
    python -m conciliacao mapear    [--data-movimento AAAA-MM-DD]   # abre o Protheus logado + Playwright Inspector
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import date
from pathlib import Path

from conciliacao.configuracao import Ambiente, Cadastro, carregar_ambiente, carregar_cadastro
from conciliacao.extratos import cliente_api
from conciliacao.extratos.leitura import ler_resposta_api
from conciliacao.extratos.pastas import dia_util_anterior, dia_util_seguinte, listar_extratos, pasta_do_dia
from conciliacao.modelos import Plano, Rotina
from conciliacao.planejamento import planejar
from conciliacao.registro import Registro
from conciliacao import notificacao, relatorio

log = logging.getLogger("conciliacao")


def _configurar_log(pasta: Path) -> None:
    pasta.mkdir(parents=True, exist_ok=True)
    formato = "%(asctime)s %(levelname)s %(name)s: %(message)s"
    logging.basicConfig(level=logging.INFO, format=formato, handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(pasta / "execucao.log", encoding="utf-8"),
    ])


def _data(texto: str) -> date:
    return date.fromisoformat(texto)


def _obter_resposta_api(args, ambiente: Ambiente, cadastro: Cadastro, data_movimento: date, pasta_saida: Path) -> dict:
    if args.extratos_json:
        return json.loads(Path(args.extratos_json).read_text(encoding="utf-8"))

    if args.pasta:
        pasta_dia, pasta_movimento = Path(args.pasta), None
    else:
        data_pasta = args.data_pasta or dia_util_seguinte(data_movimento, ambiente.feriados)
        pasta_dia = pasta_do_dia(ambiente.pasta_extratos, data_pasta)
        pasta_movimento = pasta_do_dia(ambiente.pasta_extratos, data_movimento)
    arquivos = listar_extratos(pasta_dia, pasta_movimento, cadastro)
    if not arquivos:
        raise SystemExit(f"Nenhum PDF encontrado em {pasta_dia}")
    log.info("%d extrato(s) em %s", len(arquivos), pasta_dia)

    resposta = cliente_api.transcrever(arquivos, ambiente.api_url, ambiente.api_token)
    # Guarda a resposta: permite reprocessar/depurar sem chamar a API de novo (--extratos-json)
    (pasta_saida / "extratos_api.json").write_text(json.dumps(resposta, ensure_ascii=False, indent=1), encoding="utf-8")
    return resposta


def _montar_plano(args, ambiente: Ambiente, cadastro: Cadastro) -> tuple[Plano, Path]:
    data_movimento = args.data_movimento or dia_util_anterior(date.today(), ambiente.feriados)
    pasta_saida = ambiente.pasta_saida / data_movimento.isoformat()
    _configurar_log(pasta_saida)
    log.info("Movimento de %s", data_movimento.strftime("%d/%m/%Y"))

    resposta = _obter_resposta_api(args, ambiente, cadastro, data_movimento, pasta_saida)
    extratos = ler_resposta_api(resposta, cadastro)
    return planejar(extratos, cadastro, data_movimento, ambiente.feriados), pasta_saida


def comando_planejar(args, ambiente: Ambiente, cadastro: Cadastro) -> int:
    plano, pasta_saida = _montar_plano(args, ambiente, cadastro)
    registro = Registro(ambiente.pasta_saida / "controle.sqlite")
    with registro.simulacao():  # documentos provisórios, sem gravar nada
        registro.registrar_plano(plano)
        caminho = relatorio.salvar(plano, pasta_saida, simulado=True)
    log.info("Plano: %d itens, %d conciliações, %d pendências -> %s",
             len(plano.itens()), len(plano.conciliacoes), len(plano.pendencias), caminho)
    return 0


def comando_executar(args, ambiente: Ambiente, cadastro: Cadastro) -> int:
    if args.dry_run:
        return comando_planejar(args, ambiente, cadastro)

    from conciliacao.executor import Executor  # só carrega Playwright quando vai usar

    plano, pasta_saida = _montar_plano(args, ambiente, cadastro)
    rotinas = {Rotina(r) for r in args.rotinas.split(",")} if args.rotinas else set(Rotina)
    registro = Registro(ambiente.pasta_saida / "controle.sqlite")
    resultado = Executor(plano, cadastro, ambiente, registro, pasta_saida, rotinas,
                         ensaio=args.ensaio, limite=args.limite).executar()
    caminho = relatorio.salvar(plano, pasta_saida, resultado, simulado=False)

    dia = plano.data_movimento.strftime("%d/%m/%Y")
    divergentes = [c for c, (status, _) in resultado.conciliacoes.items() if status != "conciliado"]
    if resultado.erro_geral:
        notificacao.enviar(ambiente, ambiente.email_ti + ambiente.email_operacao,
                           f"[Conciliação] FALHA na execução de {dia}", resultado.erro_geral, caminho)
        return 2
    assunto = (f"[Conciliação] {dia}: {len(plano.pendencias)} pendência(s), "
               f"{len(divergentes)} conta(s) não conciliada(s)")
    notificacao.enviar(ambiente, ambiente.email_operacao, assunto, caminho.read_text(encoding="utf-8"), caminho)
    return 1 if plano.pendencias or divergentes else 0


def comando_mapear(args, ambiente: Ambiente, cadastro: Cadastro) -> int:
    """Abre o Protheus logado com o Playwright Inspector para calibrar seletores."""
    from conciliacao.protheus.sessao import SessaoProtheus

    ambiente.protheus_visivel = True
    data_base = args.data_movimento or dia_util_anterior(date.today(), ambiente.feriados)
    with SessaoProtheus(ambiente, data_base, cadastro.constantes["filial"], ambiente.pasta_saida / "mapear",
                        pausar_se_falhar_login=True, cdp_url=ambiente.protheus_cdp_url) as sessao:
        sessao.tela.page.pause()
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="conciliacao", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="comando", required=True)

    def comum(p):
        p.add_argument("--data-movimento", type=_data, help="Data dos lançamentos (padrão: dia útil anterior a hoje)")
        p.add_argument("--data-pasta", type=_data, help="Data da pasta dos extratos (padrão: dia útil seguinte ao movimento)")
        p.add_argument("--pasta", help="Pasta exata com os PDFs (ignora a estrutura de Z:)")
        p.add_argument("--extratos-json", help="Usa uma resposta da API salva em vez de chamar a API")

    comum(p_planejar := sub.add_parser("planejar", help="Gera plano e relatório, sem tocar no Protheus"))
    comum(p_executar := sub.add_parser("executar", help="Lança e concilia no Protheus"))
    p_executar.add_argument("--rotinas", help="Lista separada por vírgula: " + ",".join(r.value for r in Rotina))
    p_executar.add_argument("--dry-run", action="store_true", help="Igual a 'planejar'")
    p_executar.add_argument("--ensaio", action="store_true",
                            help="Percorre as telas preenchendo e conferindo tudo, mas CANCELA em vez de gravar")
    p_executar.add_argument("--limite", type=int, help="No máximo N lançamentos nesta execução")
    p_mapear = sub.add_parser("mapear", help="Abre o Protheus com o Playwright Inspector")
    p_mapear.add_argument("--data-movimento", type=_data)

    args = parser.parse_args(argv)
    comandos = {"planejar": comando_planejar, "executar": comando_executar, "mapear": comando_mapear}
    return comandos[args.comando](args, carregar_ambiente(), carregar_cadastro())


if __name__ == "__main__":
    sys.exit(main())
