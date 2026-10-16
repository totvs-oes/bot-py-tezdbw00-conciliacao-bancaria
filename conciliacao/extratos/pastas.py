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


RE_DIA_MES = re.compile(r"(?<!\d)(\d{2})-(\d{2})(?!\d)")


def dia_mes_no_nome(arquivo: str) -> Optional[tuple[int, int]]:
    """(dia, mês) da data no nome: "SAFRA 580275-7  14-10.pdf ATUALIZADO.pdf" -> (14, 10). Sem data -> None."""
    datas = RE_DIA_MES.findall(nome_normalizado(arquivo))
    return (int(datas[-1][0]), int(datas[-1][1])) if datas else None


def _dia_mes_da_pasta(pasta: Optional[PurePath]) -> Optional[tuple[int, int]]:
    m = RE_DIA_MES.fullmatch(pasta.name) if pasta is not None else None
    return (int(m.group(1)), int(m.group(2))) if m else None


def atualizados_do_dia(pastas: Iterable[PurePath], dia: date, fonte: Fonte = LOCAL) -> list[PurePath]:
    """Arquivos ATUALIZADO cujo nome tem a data `dia` (Safra: liberado no dia seguinte e salvo pela cliente na pasta
    dos extratos daquele movimento, ex.: "SAFRA 580275-7  14-10.pdf ATUALIZADO" na pasta 15-10, em 16/10/2026)."""
    vistos, resultado = set(), []
    for pasta in pastas:
        for nome in fonte.pdfs(pasta):
            if eh_atualizado(nome) and dia_mes_no_nome(nome) == (dia.day, dia.month) and nome not in vistos:
                vistos.add(nome)
                resultado.append(pasta / nome)
    return resultado


def listar_extratos(pasta_dia: PurePath, pasta_movimento: Optional[PurePath], cadastro: Cadastro,
                    fonte: Fonte = LOCAL, data_movimento: Optional[date] = None) -> list[PurePath]:
    """PDFs a processar: os extratos normais da pasta do dia + os ATUALIZADO do movimento (Safra retroativo).

    O ATUALIZADO vale para o movimento da DATA DO NOME, esteja na pasta do dia ou na da data do movimento:
    - 11-09: "SAFRA 580275-7 11-09.pdf ATUALIZADO.pdf" na pasta 11-09 só tinha o dia 11/09 (é do movimento de 11/09,
      não do de 10/09, que lê a pasta 11-09; tirava do dia 10 o resgate e a tarifa);
    - 16/10/2026: "SAFRA 580275-7  14-10.pdf ATUALIZADO" na pasta 15-10 (a dos extratos do movimento de 14/10) só tem
      o dia 14/10 (era lido como se fosse do movimento de 15/10).
    ATUALIZADO sem data no nome: só o da pasta da data do movimento (regra antiga). Um ATUALIZADO substitui o original
    da mesma conta. Pasta do dia sem nenhum extrato normal = ainda não chegou: lista vazia (o ATUALIZADO sozinho não
    é o dia).
    """
    if pasta_movimento is None or pasta_movimento == pasta_dia:
        arquivos = [pasta_dia / nome for nome in fonte.pdfs(pasta_dia)]
    else:
        dia_mes = (data_movimento.day, data_movimento.month) if data_movimento else _dia_mes_da_pasta(pasta_movimento)
        arquivos = [pasta_dia / nome for nome in fonte.pdfs(pasta_dia) if not eh_atualizado(nome)]
        if not arquivos:
            return []
        for pasta in (pasta_dia, pasta_movimento):
            for nome in fonte.pdfs(pasta):
                if not eh_atualizado(nome):
                    continue
                data_nome = dia_mes_no_nome(nome)
                if (data_nome == dia_mes if data_nome else pasta == pasta_movimento) \
                        and nome not in {a.name for a in arquivos}:
                    arquivos.append(pasta / nome)

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
