"""Confirmação da gravação no Protheus (movimento_bancario._gravar), com uma tela falsa."""
import pytest

from conciliacao.protheus import movimento_bancario
from conciliacao.protheus.tela import ErroDeTela


class _Botao:
    def __init__(self, tela):
        self.tela = tela

    def click(self):
        self.tela.cliques += 1


class _Vazio:
    def count(self):
        return 0


class _Pagina:
    def wait_for_timeout(self, _ms):
        pass

    def locator(self, _seletor):
        return _Vazio()   # a tela contábil nunca aparece


class TelaFalsa:
    def __init__(self, valor_historico):
        self.valor_historico = valor_historico
        self.cliques = 0
        self.diagnosticos = []
        self.page = _Pagina()

    def botao(self, _nome, _descricao):
        return _Botao(self)

    def verificar_mensagem(self):
        pass

    def valor_do_campo(self, _rotulo):
        return self.valor_historico

    def diagnostico(self, nome):
        self.diagnosticos.append(nome)
        return f"{nome}.json"


@pytest.fixture(autouse=True)
def sem_espera(monkeypatch):
    monkeypatch.setattr(movimento_bancario, "ESPERA_POS_GRAVAR", 0)
    monkeypatch.setattr(movimento_bancario, "ESPERA_REABERTURA", 0)


def test_sem_tela_contabil_e_sem_formulario_em_branco_nao_conta_como_gravado():
    """06/11/2026: Histórico ilegível (None) o tempo todo; o robô dava "concluido" e os R$ 900.000,00 não gravaram."""
    tela = TelaFalsa(valor_historico=None)
    with pytest.raises(ErroDeTela, match="Gravação não confirmada"):
        movimento_bancario._gravar(tela, "Ok", "Histórico", "TRANSF BB C/C P/ ITAU C/C", "Cancelar")
    assert tela.cliques == 1                                      # nunca clica de novo se pode ter gravado
    assert tela.diagnosticos == ["gravacao_nao_confirmada"]


def test_formulario_reaberto_em_branco_confirma_a_gravacao(monkeypatch):
    fechados = []
    monkeypatch.setattr(movimento_bancario, "_fechar_em_branco", lambda _t, rotulo, cancelar: fechados.append(cancelar))
    tela = TelaFalsa(valor_historico="")
    movimento_bancario._gravar(tela, "Ok", "Histórico", "TRANSF BB C/C P/ ITAU C/C", "Cancelar")
    assert fechados == ["Cancelar"] and tela.diagnosticos == []
