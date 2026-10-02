from datetime import date
from pathlib import Path

import pytest

from conciliacao.extratos.pastas import (banco_do_detalhe_de_tarifas, conta_do_arquivo, dia_util_anterior,
                                         listar_extratos, pasta_do_dia)


def test_pasta_do_dia():
    raiz = Path(r"Z:\A PAGAR\AEROFLEX")
    assert pasta_do_dia(raiz, date(2026, 8, 13)) == raiz / "2026 AEROFLEX" / "08 - AGOSTO" / "13-08"


def test_pasta_do_dia_tolera_acento(tmp_path):
    (tmp_path / "2026 AEROFLEX" / "03 - MARCO" / "05-03").mkdir(parents=True)
    assert pasta_do_dia(tmp_path, date(2026, 3, 5)) == tmp_path / "2026 AEROFLEX" / "03 - MARCO" / "05-03"


def test_dia_util_anterior():
    assert dia_util_anterior(date(2026, 9, 14)) == date(2026, 9, 11)            # segunda -> sexta
    assert dia_util_anterior(date(2026, 9, 8), [date(2026, 9, 7)]) == date(2026, 9, 4)  # feriado 7/9


@pytest.mark.parametrize("arquivo, conta", [
    ("ABC 6609345-9 10-09.pdf", "abc_cc"),
    ("ABC 6609348-3 10-09.pdf", "abc_caucao"),
    ("BANCO CAIXA 10-09.pdf", "caixa_cc"),
    ("BANCO DO BRASIL 410004 10-09.pdf", "bb_cc"),
    ("BRADESCO 3179-8 10-09.pdf", "bradesco_caucao"),
    ("BRADESCO 50542-0 10-09.pdf", "bradesco_cc"),
    ("ITAU 6896-9 10-09.pdf", "itau_cc"),
    ("ITAU 8311-7 10-09.pdf", "itau_caucao"),
    ("SAFRA 326613-1  10-09.pdf", "safra_vinculada"),
    ("SAFRA 580275-7  10-09.pdf", "safra_cc"),
    ("SAFRA 580275-7 11-08.pdf ATUALIZADO.pdf", "safra_cc"),
    ("SANTANDER 130018420 10-09.pdf", "santander_cc"),
    ("SANTANDER 290003762 10-09.pdf", "santander_caucao_376"),
    ("SANTANDER 290004323 10-09.pdf", "santander_caucao_432"),
])
def test_conta_do_arquivo(cadastro, arquivo, conta):
    assert conta_do_arquivo(arquivo, cadastro).chave == conta


def test_arquivo_desconhecido(cadastro):
    assert conta_do_arquivo("extrato-da-sua-conta-01M3W87.pdf", cadastro) is None


@pytest.mark.parametrize("arquivo, banco", [
    ("TARIFAS ABC 09-09.pdf", "246"),
    ("TARIFAS BB 09-09 II.pdf", "001"),
    ("EXTRATO DE TARIFAS ITAU 13-08.pdf", "341"),
    ("ITAU 6896-9 10-09.pdf", None),
])
def test_detalhe_de_tarifas(cadastro, arquivo, banco):
    assert banco_do_detalhe_de_tarifas(arquivo, cadastro) == banco


def test_atualizado_substitui_original(cadastro, tmp_path):
    pasta_dia, pasta_mov = tmp_path / "12-08", tmp_path / "11-08"
    pasta_dia.mkdir()
    pasta_mov.mkdir()
    for nome in ("SAFRA 580275-7 12-08.pdf", "ITAU 6896-9 12-08.pdf"):
        (pasta_dia / nome).write_bytes(b"%PDF-")
    (pasta_mov / "SAFRA 580275-7 11-08.pdf ATUALIZADO.pdf").write_bytes(b"%PDF-")
    (pasta_mov / "ITAU 6896-9 11-08.pdf").write_bytes(b"%PDF-")  # sem ATUALIZADO: ignorado

    nomes = [p.name for p in listar_extratos(pasta_dia, pasta_mov, cadastro)]
    assert nomes == ["ITAU 6896-9 12-08.pdf", "SAFRA 580275-7 11-08.pdf ATUALIZADO.pdf"]
