"""Localização dos extratos na pasta de rede.

Estrutura (confirmada pela cliente em 13/08/2026):
    Z:\\A PAGAR\\AEROFLEX\\<ANO> AEROFLEX\\<MM - MÊS>\\<DD-MM>\\<BANCO> <CONTA> <DD-MM>.pdf
A pasta é a do dia em que o extrato foi GERADO; ela traz a movimentação do dia anterior.
"""
from __future__ import annotations

import re
from datetime import date, timedelta
from pathlib import Path
from typing import Iterable, Optional

from conciliacao.configuracao import Cadastro
from conciliacao.modelos import Conta
from conciliacao.texto import normalizar

MESES = ["JANEIRO", "FEVEREIRO", "MARÇO", "ABRIL", "MAIO", "JUNHO", "JULHO",
         "AGOSTO", "SETEMBRO", "OUTUBRO", "NOVEMBRO", "DEZEMBRO"]


def eh_dia_util(dia: date, feriados: Iterable[date] = ()) -> bool:
    return dia.weekday() < 5 and dia not in set(feriados)


def dia_util_anterior(dia: date, feriados: Iterable[date] = ()) -> date:
    dia -= timedelta(days=1)
    while not eh_dia_util(dia, feriados):
        dia -= timedelta(days=1)
    return dia


def dia_util_seguinte(dia: date, feriados: Iterable[date] = ()) -> date:
    dia += timedelta(days=1)
    while not eh_dia_util(dia, feriados):
        dia += timedelta(days=1)
    return dia


def _subpasta(pai: Path, esperado: str) -> Path:
    """Encontra a subpasta comparando sem acento/caixa (ex.: "03 - MARÇO" x "03 - MARCO")."""
    candidato = pai / esperado
    if candidato.is_dir() or not pai.is_dir():
        return candidato
    alvo = normalizar(esperado).replace(" ", "")
    for item in pai.iterdir():
        if item.is_dir() and normalizar(item.name).replace(" ", "") == alvo:
            return item
    return candidato


def pasta_do_dia(raiz: Path, dia: date) -> Path:
    pasta = _subpasta(raiz, f"{dia.year} AEROFLEX")
    pasta = _subpasta(pasta, f"{dia.month:02d} - {MESES[dia.month - 1]}")
    return _subpasta(pasta, f"{dia.day:02d}-{dia.month:02d}")


def nome_normalizado(arquivo: str) -> str:
    return normalizar(Path(arquivo).name)


def eh_atualizado(arquivo: str) -> bool:
    return "ATUALIZADO" in nome_normalizado(arquivo)


def banco_do_detalhe_de_tarifas(arquivo: str, cadastro: Cadastro) -> Optional[str]:
    """Código do banco se o arquivo for um extrato detalhado de tarifas, senão None."""
    nome = nome_normalizado(arquivo)
    for banco in cadastro.bancos.values():
        if banco.arquivo_tarifas and re.search(banco.arquivo_tarifas, nome):
            return banco.codigo
    return None


def conta_do_arquivo(arquivo: str, cadastro: Cadastro) -> Optional[Conta]:
    nome = nome_normalizado(arquivo)
    encontradas = [c for c in cadastro.contas if c.arquivo and re.search(c.arquivo, nome)]
    if len(encontradas) > 1:
        raise ValueError(f"'{arquivo}' casa com mais de uma conta: {[c.chave for c in encontradas]}")
    return encontradas[0] if encontradas else None


def listar_extratos(pasta_dia: Path, pasta_movimento: Optional[Path], cadastro: Cadastro) -> list[Path]:
    """PDFs a processar: os da pasta do dia + os ATUALIZADO da pasta da data do movimento (Safra retroativo).

    Um arquivo ATUALIZADO substitui o original da mesma conta.
    """
    arquivos = sorted(pasta_dia.glob("*.pdf")) if pasta_dia.is_dir() else []
    if pasta_movimento and pasta_movimento.is_dir() and pasta_movimento != pasta_dia:
        arquivos += [p for p in sorted(pasta_movimento.glob("*.pdf")) if eh_atualizado(p.name)]

    atualizados = {}
    for arquivo in arquivos:
        conta = conta_do_arquivo(arquivo.name, cadastro)
        if conta and eh_atualizado(arquivo.name):
            atualizados[conta.chave] = arquivo

    resultado = []
    for arquivo in arquivos:
        conta = conta_do_arquivo(arquivo.name, cadastro)
        if conta and conta.chave in atualizados and atualizados[conta.chave] != arquivo:
            continue
        resultado.append(arquivo)
    return resultado
