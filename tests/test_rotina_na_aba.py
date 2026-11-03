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


def test_aba_ativa_de_rotina_sem_versao_no_titulo():
    """Produção, 29/10/2026 (diagnóstico rotina_errada): a aba veio como "Movimentos Bancários", sem "[02.9.0006]".
    Antes o robô só aceitava título com "[" e concluía que nenhuma rotina estava ativa."""
    from conciliacao.protheus.sessao import rotina_da_aba_ativa
    assert rotina_da_aba_ativa([{"caption": "Movimentos Bancários", "workspace": True}]) == "Movimentos Bancários"
    assert rotina_da_aba_ativa([{"caption": "Movimentos Bancários [02.9.0006]", "workspace": False}]) == \
        "Movimentos Bancários"                                             # como antes (título com versão)
    # Pasta interna de um formulário (não é aba de workspace) não conta como rotina
    assert rotina_da_aba_ativa([{"caption": "Dados não Encontrados", "workspace": False},
                                {"caption": "Novo Conciliador Backoffice", "workspace": True}]) == \
        "Novo Conciliador Backoffice"
    assert rotina_da_aba_ativa([{"caption": "Dados não Encontrados", "workspace": False}]) == ""
    assert rotina_da_aba_ativa([]) == ""


def test_aba_ja_aberta_e_encontrada_com_ou_sem_versao():
    """Sem achar a aba aberta, o robô abria a rotina de novo pelo menu: duas abas "Movimentos Bancários" (29/10/2026)."""
    from conciliacao.protheus.sessao import padrao_aba_da_rotina
    padrao = padrao_aba_da_rotina(s.ABAS_DA_ROTINA[s.ROTINA_MOVIMENTO_BANCARIO])
    for texto in ("Movimentos Bancários", " Movimentos Bancários ", "Movimentos Bancários [02.9.0006]",
                  "Movimento Bancario - 01/010101 [02.9.0006]"):
        assert padrao.search(texto), texto
    for texto in ("Movimentos Bancários a Receber", "Saldos Bancários", "Novo Conciliador Backoffice"):
        assert not padrao.search(texto), texto
