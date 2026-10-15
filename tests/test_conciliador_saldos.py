"""Conferência depois de aplicar a conciliação (modal "Saldos bancários" do Novo Conciliador)."""
from decimal import Decimal

from conciliacao.protheus.conciliador import conferir_conciliado, saldos_do_modal

# Texto do modal em produção (Daycoval, 13/10/2026): histórico não conciliado nesta rotina (conciliado ≠ bancário)
MODAL_PRODUCAO = ("Saldos bancários Conta: 707 - 0001 - 1500926 Quantidade Reais (R$) Saldo anterior (Bancário) 969,31 "
                  "Saldo anterior (Conciliado) -118.226,87 Total de entradas 0 0,00 Total de saídas 0 0,00 "
                  "Saldo atual (Bancário) 969,31 Saldo atual (Conciliado) -118.226,87 Fechar")


def _modal(anterior_banc, anterior_conc, atual_banc, atual_conc):
    return (f"Saldo anterior (Bancário) {anterior_banc} Saldo anterior (Conciliado) {anterior_conc} "
            f"Total de entradas 1 1,00 Total de saídas 0 0,00 Saldo atual (Bancário) {atual_banc} "
            f"Saldo atual (Conciliado) {atual_conc}")


def test_le_os_quatro_saldos_do_modal_de_producao():
    saldos = saldos_do_modal(MODAL_PRODUCAO)
    assert saldos == {"Saldo anterior (Bancário)": Decimal("969.31"),
                      "Saldo anterior (Conciliado)": Decimal("-118226.87"),
                      "Saldo atual (Bancário)": Decimal("969.31"),
                      "Saldo atual (Conciliado)": Decimal("-118226.87")}


def test_historico_nao_conciliado_nao_e_erro_quando_o_dia_inteiro_foi_conciliado():
    """Produção, 15/10/2026: extrato 510,24; o conciliado ficou 113.097,14 porque o conciliado anterior já carregava
    112.586,90 de diferença. O dia foi todo conciliado: a conferência tem que passar."""
    saldos = saldos_do_modal(_modal("1.000,00", "113.586,90", "510,24", "113.097,14"))
    assert conferir_conciliado(saldos, Decimal("510.24")) is None


def test_movimento_do_dia_conciliado_pela_metade_e_erro():
    saldos = saldos_do_modal(_modal("1.000,00", "113.586,90", "510,24", "113.400,00"))
    assert "nem todos os movimentos do dia" in conferir_conciliado(saldos, Decimal("510.24"))


def test_bancario_diferente_do_extrato_e_modal_ilegivel_sao_erro():
    saldos = saldos_do_modal(_modal("1.000,00", "1.000,00", "600,00", "600,00"))
    assert "extrato" in conferir_conciliado(saldos, Decimal("510.24"))
    assert "não consegui ler" in conferir_conciliado(saldos_do_modal("Saldo atual (Bancário) 510,24"), Decimal("510.24"))


CABECALHO = ["", "Filial Orig. ⇅", "Data Dispon. ⇅", "Data Mov. ⇅", "Moeda ⇅", "Valor Mov. ⇅", "Natureza ⇅",
             "Banco ⇅", "Agencia ⇅", "Conta Banco ⇅", "Num Cheque ⇅", "Documento ⇅", "Rec/Pag"]
# As 11 linhas de "Dados não Encontrados" da ABC C/C em 14/10/2026 (produção, 15/10/2026)
LINHAS_ABC = [("357.977,90", "R"), ("0,89", "P"), ("10.502,72", "R"), ("15.345,60", "R"), ("3,46", "P"),
              ("5,34", "P"), ("39.677,76", "R"), ("37.145,92", "R"), ("17.664,00", "R"), ("508.729,39", "P"),
              ("2,67", "P")]


def test_soma_dos_movimentos_da_grade_e_o_caso_que_nao_podia_ser_conciliado():
    from conciliacao.protheus.conciliador import soma_dos_movimentos
    linhas = [["", "010101", "14/10/2026", "14/10/2026", "M1", valor, "", "246", "0001", "006609345", "", "", tipo]
              for valor, tipo in LINHAS_ABC]
    soma = soma_dos_movimentos(CABECALHO, linhas)
    assert soma == Decimal("-30427.85")            # = "Total Movimentos" do rodapé da grade
    variacao_do_dia = Decimal("510.24") - Decimal("434.49")
    assert soma != variacao_do_dia                  # +75,75: com a conferência nova, a conciliação NÃO é aplicada
