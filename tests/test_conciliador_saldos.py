"""Conciliação no Novo Conciliador: leitura do modal "Saldos bancários", soma da grade e conferência pós-aplicação.

Regra (cliente, 15/10/2026): saldo do Protheus = extrato -> concilia. Se os lançamentos listados não somarem a
variação do saldo do dia, concilia mesmo assim e registra uma observação no relatório.
"""
from datetime import date
from decimal import Decimal

from conciliacao.protheus.conciliador import (conferir_conciliado, observacao_lista_do_dia, saldos_do_modal,
                                              soma_dos_movimentos)

# Texto do modal em produção (Daycoval, 13/10/2026): histórico não conciliado nesta rotina (conciliado ≠ bancário)
MODAL_PRODUCAO = ("Saldos bancários Conta: 707 - 0001 - 1500926 Quantidade Reais (R$) Saldo anterior (Bancário) 969,31 "
                  "Saldo anterior (Conciliado) -118.226,87 Total de entradas 0 0,00 Total de saídas 0 0,00 "
                  "Saldo atual (Bancário) 969,31 Saldo atual (Conciliado) -118.226,87 Fechar")


def _modal(anterior_banc, anterior_conc, atual_banc, atual_conc):
    return saldos_do_modal(f"Saldo anterior (Bancário) {anterior_banc} Saldo anterior (Conciliado) {anterior_conc} "
                           f"Total de entradas 1 1,00 Total de saídas 0 0,00 Saldo atual (Bancário) {atual_banc} "
                           f"Saldo atual (Conciliado) {atual_conc}")


def test_le_os_quatro_saldos_do_modal_de_producao():
    assert saldos_do_modal(MODAL_PRODUCAO) == {"Saldo anterior (Bancário)": Decimal("969.31"),
                                               "Saldo anterior (Conciliado)": Decimal("-118226.87"),
                                               "Saldo atual (Bancário)": Decimal("969.31"),
                                               "Saldo atual (Conciliado)": Decimal("-118226.87")}


CABECALHO = ["", "Filial Orig. ⇅", "Data Dispon. ⇅", "Data Mov. ⇅", "Moeda ⇅", "Valor Mov. ⇅", "Natureza ⇅",
             "Banco ⇅", "Agencia ⇅", "Conta Banco ⇅", "Num Cheque ⇅", "Documento ⇅", "Rec/Pag"]
# As 11 linhas de "Dados não Encontrados" da ABC C/C em 14/10/2026 (produção, 15/10/2026)
LINHAS_ABC = [("357.977,90", "R"), ("0,89", "P"), ("10.502,72", "R"), ("15.345,60", "R"), ("3,46", "P"),
              ("5,34", "P"), ("39.677,76", "R"), ("37.145,92", "R"), ("17.664,00", "R"), ("508.729,39", "P"),
              ("2,67", "P")]


def test_soma_dos_movimentos_da_grade():
    linhas = [["", "010101", "14/10/2026", "14/10/2026", "M1", valor, "", "246", "0001", "006609345", "", "", tipo]
              for valor, tipo in LINHAS_ABC]
    assert soma_dos_movimentos(CABECALHO, linhas) == Decimal("-30427.85")   # = "Total Movimentos" do rodapé


def test_conferencia_pos_aplicacao_com_os_numeros_reais_da_abc():
    """ABC C/C, 14/10: conciliado 143.524,99 antes; 11 linhas somando -30.427,85; depois 113.097,14 (o valor que
    antes era tratado como erro por ser diferente do extrato de 510,24). O Protheus registrou exatamente as linhas."""
    antes = _modal("434,49", "143.524,99", "510,24", "143.524,99")
    depois = _modal("434,49", "143.524,99", "510,24", "113.097,14")
    assert conferir_conciliado(antes, depois, Decimal("-30427.85")) is None


def test_conferencia_pos_aplicacao_acusa_linhas_nao_registradas_e_modal_ilegivel():
    antes = _modal("434,49", "143.524,99", "510,24", "143.524,99")
    parcial = _modal("434,49", "143.524,99", "510,24", "143.000,00")
    assert "linhas marcadas somam" in conferir_conciliado(antes, parcial, Decimal("-30427.85"))
    assert "não consegui ler" in conferir_conciliado(antes, saldos_do_modal("Saldo atual (Bancário) 510,24"),
                                                     Decimal("1"))


def test_observacao_no_relatorio_quando_a_lista_nao_fecha_com_o_saldo_do_dia():
    texto = observacao_lista_do_dia(11, date(2026, 10, 14), Decimal("-30427.85"), Decimal("434.49"), Decimal("510.24"))
    assert "Conciliados 11 lançamentos" in texto and "R$ -30.427,85" in texto and "R$ 75,75" in texto
    assert "R$ 30.503,60" in texto
    # lista que fecha com a variação do dia: sem observação
    assert observacao_lista_do_dia(3, date(2026, 10, 14), Decimal("75.75"), Decimal("434.49"), Decimal("510.24")) is None
