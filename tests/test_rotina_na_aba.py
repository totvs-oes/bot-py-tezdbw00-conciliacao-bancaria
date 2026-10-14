"""Nome da rotina pelo título da aba e nomes aceitos por ambiente (homologação x produção)."""
from conciliacao.protheus import seletores as s
from conciliacao.protheus.sessao import nome_da_rotina_na_aba


def test_titulo_da_aba_na_homologacao_e_na_producao():
    assert nome_da_rotina_na_aba("Movimento Bancario - 01/010101 [02.9.0006]") == "Movimento Bancario"
    assert nome_da_rotina_na_aba("Movimentos Bancários [02.9.0006]") == "Movimentos Bancários"   # produção, sem filial
    assert nome_da_rotina_na_aba("Novo Conciliador Backoffice - 01/010101 [12.1.2410]") == "Novo Conciliador Backoffice"


def test_movimento_bancario_aceita_o_nome_dos_dois_ambientes():
    aceitos = s.ABAS_DA_ROTINA[s.ROTINA_MOVIMENTO_BANCARIO]
    assert {"Movimento Bancario", "Movimentos Bancários"} <= set(aceitos)
    assert s.MENU_CAMINHO[s.ROTINA_MOVIMENTO_BANCARIO][-1] == aceitos   # item do menu: qualquer um dos dois
    assert set(s.MENU_CAMINHO) == set(s.ABAS_DA_ROTINA)                 # toda rotina do menu tem nome de aba
