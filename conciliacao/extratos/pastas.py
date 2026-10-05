"""Localização dos extratos na pasta de rede.

Estrutura (confirmada pela cliente em 13/08/2026):
    Z:\\A PAGAR\\AEROFLEX\\<ANO> AEROFLEX\\<MM - MÊS>\\<DD-MM>\\<BANCO> <CONTA> <DD-MM>.pdf
A pasta é a do dia em que o extrato foi GERADO; ela traz a movimentação do dia anterior.

A mesma estrutura vale para as duas fontes (FONTE_EXTRATOS no .env):
    local  pasta local/de rede (PASTA_EXTRATOS)
    sftp   servidor do cliente, acessado pela API de extratos (caminhos relativos à SFTP_PASTA_BASE dela)
"""
from __future__ import annotations

import re
from datetime import date, timedelta
from pathlib import Path, PurePath
from typing import Iterable, Optional, Protocol

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


class Fonte(Protocol):
    """De onde vêm os PDFs: pasta local/rede (FonteLocal) ou SFTP do cliente via API de extratos (FonteSftp)."""

    def subpastas(self, pasta: PurePath) -> Optional[list[str]]:
        """Nomes das subpastas; None se a pasta não existe."""

    def pdfs(self, pasta: PurePath) -> list[str]:
        """Nomes dos PDFs da pasta; vazio se ela não existe."""


class FonteLocal:
    def subpastas(self, pasta: PurePath) -> Optional[list[str]]:
        pasta = Path(pasta)
        return sorted(p.name for p in pasta.iterdir() if p.is_dir()) if pasta.is_dir() else None

    def pdfs(self, pasta: PurePath) -> list[str]:
        pasta = Path(pasta)
        if not pasta.is_dir():
            return []
        return sorted(p.name for p in pasta.iterdir() if p.is_file() and p.suffix.lower() == ".pdf")


LOCAL = FonteLocal()


def _subpasta(pai: PurePath, esperado: str, fonte: Fonte) -> PurePath:
    """Encontra a subpasta comparando sem acento/caixa (ex.: "03 - MARÇO" x "03 - MARCO")."""
    candidato = pai / esperado
    existentes = fonte.subpastas(pai)
    if existentes is None or esperado in existentes:
        return candidato
    alvo = normalizar(esperado).replace(" ", "")
    for nome in existentes:
        if normalizar(nome).replace(" ", "") == alvo:
            return pai / nome
    return candidato


def pasta_do_dia(raiz: PurePath, dia: date, fonte: Fonte = LOCAL) -> PurePath:
    pasta = _subpasta(raiz, f"{dia.year} AEROFLEX", fonte)
    pasta = _subpasta(pasta, f"{dia.month:02d} - {MESES[dia.month - 1]}", fonte)
    return _subpasta(pasta, f"{dia.day:02d}-{dia.month:02d}", fonte)


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


def listar_extratos(pasta_dia: PurePath, pasta_movimento: Optional[PurePath], cadastro: Cadastro,
                    fonte: Fonte = LOCAL) -> list[PurePath]:
    """PDFs a processar: os da pasta do dia + os ATUALIZADO da pasta da data do movimento (Safra retroativo).

    Um arquivo ATUALIZADO substitui o original da mesma conta.
    """
    arquivos = [pasta_dia / nome for nome in fonte.pdfs(pasta_dia)]
    if pasta_movimento and pasta_movimento != pasta_dia:
        arquivos += [pasta_movimento / nome for nome in fonte.pdfs(pasta_movimento) if eh_atualizado(nome)]

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
