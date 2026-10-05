import json
from datetime import date
from decimal import Decimal

from conciliacao.extratos.leitura import ler_resposta_api
from conciliacao.planejamento import documento, planejar
from tests.conftest import DIA, FIXTURES


def _transferencias(plano, origem, destino):
    return [t for t in plano.transferencias if t.origem.chave == origem and t.destino.chave == destino]


def _pendencias(plano, trecho):
    return [p for p in plano.pendencias if trecho in p.motivo]


def test_liberacao_de_caucao_lancada_uma_vez_e_pareada(extratos, cadastro):
    plano = planejar(extratos, cadastro, DIA)

    bradesco = _transferencias(plano, "bradesco_caucao", "bradesco_cc")
    assert sorted(t.valor for t in bradesco) == [Decimal("51315.72"), Decimal("126933.86")]
    assert all(t.aviso is None for t in bradesco)  # crédito achado no extrato da CC
    assert all(t.historico == "TRANSF BRAD CAU P/ BRAD C/C" for t in bradesco)

    itau = _transferencias(plano, "itau_caucao", "itau_cc")
    assert len(itau) == 9 and all(t.aviso is None for t in itau)

    # Nenhuma contrapartida sobrou como pendência
    assert not _pendencias(plano, "sem o débito correspondente")


def test_consolidacao_santander(extratos, cadastro):
    plano = planejar(extratos, cadastro, DIA)

    para_376 = _transferencias(plano, "santander_caucao_central", "santander_caucao_376")
    para_432 = _transferencias(plano, "santander_caucao_central", "santander_caucao_432")
    assert [t.valor for t in para_376] == [Decimal("70416.20")]
    assert [t.valor for t in para_432] == [Decimal("107036.94")]
    assert para_376[0].historico == "TRANSF SANT CAU P/ SANT CAU"

    # A caução central não tem extrato próprio; os valores batem com o SALDO VINCULADO LIBERADO do dia
    assert [t.valor for t in _transferencias(plano, "santander_caucao_376", "santander_cc")] == [Decimal("70416.20")]
    assert [t.valor for t in _transferencias(plano, "santander_caucao_432", "santander_cc")] == [Decimal("107036.94")]

    # Consolidação vem antes da liberação para a CC
    chaves = [t.origem.chave for t in plano.transferencias]
    assert chaves.index("santander_caucao_central") < chaves.index("santander_caucao_376")


def test_resgate_usa_conta_aplicacao_do_mesmo_banco(extratos, cadastro):
    plano = planejar(extratos, cadastro, DIA)
    resgate = _transferencias(plano, "bradesco_aplic", "bradesco_cc")
    assert [t.valor for t in resgate] == [Decimal("8047715.82")]
    assert resgate[0].historico == "TRANSF BRAD RESGATE APLIC P/ BRAD CC"
    assert resgate[0].aviso is None


def test_tarifas_e_rendimentos(extratos, cadastro):
    plano = planejar(extratos, cadastro, DIA)
    tarifas_bradesco = [t.valor for t in plano.tarifas if t.conta.chave == "bradesco_cc"]
    assert tarifas_bradesco == [Decimal(v) for v in ("90.00", "4.85", "9.70", "2.06", "84.20")]
    assert all(len(t.historico) <= 40 for t in plano.tarifas)
    assert [r.valor for r in plano.rendimentos] == [Decimal("0.04"), Decimal("3.55")]
    assert all(r.conta.chave == "itau_cc" and r.historico == "REND APLIC" for r in plano.rendimentos)


def test_somente_lancamentos_da_data_do_movimento(extratos, cadastro):
    plano = planejar(extratos, cadastro, DIA)
    assert all(i.data == DIA for i in plano.itens())
    # 10/09 tem tarifas no ABC que não podem entrar no plano de 09/09
    assert Decimal("109.47") not in [t.valor for t in plano.tarifas]


def test_nao_reconhecido_vira_pendencia_e_nao_lancamento(extratos, cadastro):
    plano = planejar(extratos, cadastro, DIA)
    nao_reconhecidos = _pendencias(plano, "não reconhecido")
    assert {p.historico for p in nao_reconhecidos} == {"DOC/TED INTERNET TED INTERNET 1704220"}


def test_kg_vira_pendencia_de_baixa_manual_e_dev_pag_bol_e_ignorado(extratos, cadastro):
    """Respostas da cliente em 05/10/2026."""
    plano = planejar(extratos, cadastro, DIA)
    kg = [p for p in plano.pendencias if p.historico == "KG 3361823"]
    assert len(kg) == 1 and "Contas a Pagar" in kg[0].motivo and "baixa manual" in kg[0].acao
    assert Decimal("546127.35") not in [i.valor for i in plano.itens()]
    assert not [p for p in plano.pendencias if (p.historico or "").startswith("DEV PAG BOL")]
    assert any(l.historico.startswith("DEV PAG BOL") for l in plano.fora_do_escopo)


def _daycoval():
    """Saída real da API (layout Daycoval) para os extratos de exemplo enviados pela cliente em 05/10/2026."""
    return json.loads((FIXTURES / "extratos_daycoval.json").read_text(encoding="utf-8"))


def test_daycoval_liberacao_de_caucao(cadastro):
    plano = planejar(ler_resposta_api(_daycoval(), cadastro), cadastro, date(2026, 9, 3))
    transf = _transferencias(plano, "daycoval_caucao", "daycoval_cc")
    assert [t.valor for t in transf] == [Decimal("100691.20")]
    assert transf[0].aviso is None and transf[0].historico == "TRANSF DAYC CAU P/ DAYC C/C"
    assert len(plano.itens()) == 1 and not plano.pendencias
    saldos = {c.conta.chave: c.saldo_extrato for c in plano.conciliacoes}
    assert saldos == {"daycoval_cc": Decimal("320049.70"), "daycoval_caucao": Decimal("0.00")}


def test_daycoval_amortizacao_vira_pendencia(cadastro):
    plano = planejar(ler_resposta_api(_daycoval(), cadastro), cadastro, date(2026, 9, 1))
    assert [t.valor for t in plano.tarifas] == [Decimal("43.79")]
    assert [p.historico[:18] for p in _pendencias(plano, "não reconhecido")] == ["AMORT. DE CONTRATO"]


def test_tarifa_agrupada_sem_detalhe_vira_pendencia(extratos, cadastro):
    plano = planejar(extratos, cadastro, DIA)
    pendencias = _pendencias(plano, "Tarifa agrupada")
    assert [p.valor for p in pendencias] == [Decimal("14.34")]
    assert Decimal("14.34") not in [t.valor for t in plano.tarifas]


def _com_detalhe_bb(resposta_api, valores):
    resposta_api["banco_0001"].append({
        "arquivo": "TARIFAS BB 09-09 I.pdf", "metodo": "ia", "aviso": None, "conta": None,
        "lancamentos": [{"data": "09/09/2026", "historico": f"Tarifa {i}", "operacao": "debito", "valor": v}
                        for i, v in enumerate(valores)],
        "conferencia": {"saldo_anterior": None, "saldo_final": None, "ok": None},
    })
    return resposta_api


def test_tarifa_agrupada_com_detalhe_que_bate(resposta_api, cadastro):
    extratos = ler_resposta_api(_com_detalhe_bb(resposta_api, [10.00, 4.34]), cadastro)
    plano = planejar(extratos, cadastro, DIA)
    individuais = [t for t in plano.tarifas if t.origem_extrato.startswith("TARIFAS BB")]
    assert [t.valor for t in individuais] == [Decimal("10.00"), Decimal("4.34")]
    assert all(t.conta.chave == "bb_cc" for t in individuais)
    assert not _pendencias(plano, "Tarifa agrupada")


def test_tarifa_agrupada_com_detalhe_que_nao_bate(resposta_api, cadastro):
    extratos = ler_resposta_api(_com_detalhe_bb(resposta_api, [10.00, 4.33]), cadastro)
    plano = planejar(extratos, cadastro, DIA)
    assert not [t for t in plano.tarifas if t.origem_extrato.startswith("TARIFAS BB")]
    assert "diferente do agrupado" in _pendencias(plano, "Tarifa agrupada")[0].motivo


def test_extrato_com_conferencia_falha_nao_gera_lancamentos(resposta_api, cadastro):
    resposta_api["banco_0237"][1]["conferencia"]["ok"] = False   # BRADESCO 50542-0
    plano = planejar(ler_resposta_api(resposta_api, cadastro), cadastro, DIA)
    assert not [t for t in plano.tarifas if t.conta.chave == "bradesco_cc"]
    assert _pendencias(plano, "Conferência do extrato falhou")


def test_saldo_para_conciliacao(extratos, cadastro):
    plano = planejar(extratos, cadastro, DIA)
    saldos = {c.conta.chave: c.saldo_extrato for c in plano.conciliacoes}
    assert saldos["itau_caucao"] == Decimal("2943719.77")  # zerada no dia 10 pelo TEG de 2.943.719,77
    assert saldos["bradesco_caucao"] == Decimal("0.00")
    # Caixa: o extrato começa no dia útil seguinte; o saldo anterior é o saldo do dia
    assert "caixa_cc" in saldos
    # Safra não tem saldo no extrato: sem conciliação automática
    assert "safra_cc" not in saldos and _pendencias(plano, "sem saldo conferido")


def test_chaves_unicas_e_estaveis(extratos, cadastro):
    primeiro = planejar(extratos, cadastro, DIA)
    segundo = planejar(extratos, cadastro, DIA)
    chaves = [i.chave for i in primeiro.itens()]
    assert len(chaves) == len(set(chaves))
    assert chaves == [i.chave for i in segundo.itens()]


def test_documento():
    assert documento(DIA, 0) == "090926"
    assert documento(DIA, 3) == "0909263"
