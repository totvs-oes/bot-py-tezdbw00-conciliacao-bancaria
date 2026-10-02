from conciliacao.planejamento import planejar
from conciliacao.registro import CONCLUIDO, ERRO, EXECUTANDO, INCERTO, PLANEJADO, SALVANDO, Registro
from tests.conftest import DIA


def _plano(extratos, cadastro):
    return planejar(extratos, cadastro, DIA)


def test_documentos_sequenciais_por_rotina_e_banco(extratos, cadastro):
    plano = _plano(extratos, cadastro)
    registro = Registro(":memory:")
    registro.registrar_plano(plano)

    bradesco = [t.documento for t in plano.tarifas if t.conta.banco == "237"]
    assert bradesco == ["090926", "0909261", "0909262", "0909263", "0909264"]
    # Sequência é por rotina: a primeira transferência do Bradesco também é 090926
    assert next(t.documento for t in plano.transferencias if t.origem.banco == "237") == "090926"


def test_registrar_de_novo_mantem_documento_e_status(extratos, cadastro):
    registro = Registro(":memory:")
    primeiro = _plano(extratos, cadastro)
    registro.registrar_plano(primeiro)
    item = primeiro.tarifas[0]
    registro.marcar(item.chave, CONCLUIDO)

    segundo = _plano(extratos, cadastro)
    registro.registrar_plano(segundo)
    assert [i.documento for i in segundo.itens()] == [i.documento for i in primeiro.itens()]
    assert registro.status(item.chave) == CONCLUIDO


def test_simulacao_nao_grava(extratos, cadastro, tmp_path):
    registro = Registro(tmp_path / "controle.sqlite")
    with registro.simulacao():
        plano = _plano(extratos, cadastro)
        registro.registrar_plano(plano)
        assert plano.tarifas[0].documento == "090926"
    assert registro.status(plano.tarifas[0].chave) is None


def test_execucao_interrompida(extratos, cadastro):
    registro = Registro(":memory:")
    plano = _plano(extratos, cadastro)
    registro.registrar_plano(plano)
    a, b, c = plano.tarifas[:3]
    registro.marcar(a.chave, EXECUTANDO)
    registro.marcar(b.chave, SALVANDO)

    assert registro.recuperar_interrompidos() == 2
    assert registro.status(a.chave) == ERRO        # caiu antes de salvar: pode tentar de novo
    assert registro.status(b.chave) == INCERTO     # caiu depois de clicar em Salvar: conferência humana
    assert registro.status(c.chave) == PLANEJADO
