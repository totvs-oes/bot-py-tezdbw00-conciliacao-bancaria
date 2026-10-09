"""CLI do RPA de conciliação.

    python -m conciliacao planejar  [--data-movimento AAAA-MM-DD] [--pasta DIR | --extratos-json ARQ]
    python -m conciliacao executar  [--data-movimento AAAA-MM-DD] [--rotinas transferencia,tarifa,...] [--dry-run | --ensaio] [--limite N]
    python -m conciliacao mapear    [--data-movimento AAAA-MM-DD]   # abre o Protheus logado + Playwright Inspector
    python -m conciliacao api       [--host 0.0.0.0] [--port 5001]  # API HTTP para sistemas externos dispararem execuções
"""
from __future__ import annotations

import argparse
import logging
import sys
from datetime import date

from conciliacao.configuracao import Ambiente, Cadastro, carregar_ambiente, carregar_cadastro
from conciliacao.extratos.pastas import dia_util_anterior, eh_dia_util
from conciliacao.modelos import Rotina
from conciliacao.servico import Modo, Pedido, executar_dia


def _data(texto: str) -> date:
    return date.fromisoformat(texto)


def _pedido(args, modo: Modo) -> Pedido:
    return Pedido(
        data_movimento=args.data_movimento, modo=modo,
        rotinas={Rotina(r) for r in args.rotinas.split(",")} if getattr(args, "rotinas", None) else None,
        limite=getattr(args, "limite", None), data_pasta=args.data_pasta, pasta=args.pasta,
        extratos_json=args.extratos_json,
    )


def comando_planejar(args, ambiente: Ambiente, cadastro: Cadastro) -> int:
    executar_dia(_pedido(args, Modo.PLANEJAR), ambiente, cadastro)
    return 0


def _hoje() -> date:
    return date.today()


def comando_executar(args, ambiente: Ambiente, cadastro: Cadastro) -> int:
    # Sem data (execução agendada): em fim de semana/feriado a pasta do dia não existe -> nada a fazer
    if args.data_movimento is None and not eh_dia_util(_hoje(), ambiente.feriados):
        logging.getLogger("conciliacao").info("Hoje (%s) não é dia útil: nada a fazer.", _hoje().strftime("%d/%m/%Y"))
        return 0
    modo = Modo.PLANEJAR if args.dry_run else Modo.ENSAIO if args.ensaio else Modo.EXECUTAR
    return executar_dia(_pedido(args, modo), ambiente, cadastro).codigo_saida


def comando_mapear(args, ambiente: Ambiente, cadastro: Cadastro) -> int:
    """Abre o Protheus logado com o Playwright Inspector para calibrar seletores."""
    from conciliacao.protheus.sessao import SessaoProtheus

    ambiente.protheus_visivel = True
    data_base = args.data_movimento or dia_util_anterior(date.today(), ambiente.feriados)
    with SessaoProtheus(ambiente, data_base, cadastro.constantes["filial"], ambiente.pasta_saida / "mapear",
                        pausar_se_falhar_login=True, cdp_url=ambiente.protheus_cdp_url) as sessao:
        sessao.tela.page.pause()
    return 0


def comando_api(args, ambiente: Ambiente, cadastro: Cadastro) -> int:
    import uvicorn  # só carrega o servidor HTTP quando vai usar

    uvicorn.run("conciliacao.api:app", host=args.host, port=args.port)
    return 0


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, stream=sys.stdout,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
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
    p_api = sub.add_parser("api", help="Sobe a API HTTP (POST /execucoes) para sistemas externos")
    p_api.add_argument("--host", default="0.0.0.0")
    p_api.add_argument("--port", type=int, default=5001)

    args = parser.parse_args(argv)
    comandos = {"planejar": comando_planejar, "executar": comando_executar, "mapear": comando_mapear,
                "api": comando_api}
    return comandos[args.comando](args, carregar_ambiente(), carregar_cadastro())


if __name__ == "__main__":
    sys.exit(main())
