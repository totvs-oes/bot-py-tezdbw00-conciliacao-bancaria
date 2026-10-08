from datetime import date
from decimal import Decimal

import pytest

from conciliacao.classificacao import classificar
from conciliacao.modelos import LancamentoExtrato


def _lancamento(cadastro, conta, historico, operacao):
    return LancamentoExtrato(conta=cadastro.conta(conta), arquivo="x.pdf", indice=0, data=date(2026, 9, 9),
                             historico=historico, operacao=operacao, valor=Decimal("1.00"))


@pytest.mark.parametrize("conta, historico, operacao, categoria, regra", [
    # Liberação de caução/vinculada (lado dono = débito na caução)
    ("bradesco_caucao", "TRANSF.EXCEDENTEGARANTIA 06349 00505420 4975", "debito", "transferencia", "liberacao_caucao"),
    ("itau_caucao", "TEG EX GAR 4685-0008311-7", "debito", "transferencia", "liberacao_caucao"),
    ("safra_vinculada", "LIBERACAO DE VINCULADA 5802757", "debito", "transferencia", "liberacao_caucao"),
    ("santander_caucao_376", "SALDO VINCULADO LIBERADO 000000", "debito", "transferencia", "liberacao_caucao"),
    ("abc_caucao", "TRANSFERENCIA ENTRE C/C", "debito", "transferencia", "liberacao_caucao"),
    # Mesmo movimento visto pela conta corrente
    ("bradesco_cc", "TRANSF.EXCEDENTEGARANTIA 06349 00031798 4975", "credito", "contrapartida", "contrapartida_liberacao"),
    ("itau_cc", "TEG EX GAR 4685-0008311-7", "credito", "contrapartida", "contrapartida_liberacao"),
    ("safra_cc", "LIBERACAO DE CONTA VINCULADA 3266131", "credito", "contrapartida", "contrapartida_liberacao"),
    ("santander_cc", "TRANSF VALORES MESMA TITULARI DADE 000000", "credito", "contrapartida", "contrapartida_liberacao"),
    ("abc_cc", "TRANSFERENCIA ENTRE C/C", "credito", "contrapartida", "contrapartida_liberacao"),
    # Aplicações
    ("bradesco_cc", "RESGATE MERCADO ABERTO 3595454", "credito", "transferencia", "resgate_aplicacao"),
    ("santander_cc", "RESGATE CONTAMAX AUTOMATICO 000000", "credito", "transferencia", "resgate_aplicacao"),
    ("abc_cc", "RESGATE APLIC. FINANCEIRA", "credito", "transferencia", "resgate_aplicacao"),
    ("itau_cc", "RES APLIC AUT MAIS", "credito", "transferencia", "resgate_aplicacao"),
    ("santander_cc", "APLICACAO CONTAMAX 000000", "debito", "transferencia", "aplicacao"),
    ("safra_cc", "APLICACAO CDB AUTOMATICO 5802749", "debito", "transferencia", "aplicacao"),
    ("abc_cc", "APLICACAO FINANCEIRA", "debito", "transferencia", "aplicacao"),
    ("itau_cc", "APL APLIC AUT MAIS AP", "debito", "transferencia", "aplicacao"),
    # Caixa vinculada -> C/C (10/09/2026)
    ("caixa_vinculada", "TRANSF RECURSO AGENCIA 07872967000102 AEROFLEX INDUSTRIA DE A. LTDA 101111 11:11:45",
     "debito", "transferencia", "liberacao_caucao"),
    ("caixa_cc", "CRED TEV 101111", "credito", "contrapartida", "contrapartida_liberacao"),
    ("caixa_vinculada", "COB COMPE 90926 02:15:43", "credito", "fora_do_escopo", "fora_do_escopo"),
    # Caixa: tarifas com código grudado
    ("caixa_cc", "COB ALT055 100926", "debito", "tarifa", "tarifa"),
    ("caixa_cc", "COBPROT066 100926", "debito", "tarifa", "tarifa"),
    ("caixa_cc", "COB BX 063 100926", "debito", "tarifa", "tarifa"),
    # Pagamentos (fora do escopo) e câmbio/amortização (ação manual)
    ("bb_cc", "Pagamento de Boleto COMERCIAL MARTINS LTDA", "debito", "fora_do_escopo", "fora_do_escopo"),
    ("santander_cc", "DEBITO PAGAMENTO DE SALARIO P AGSAL: 6 PAGTOS 010910", "debito", "fora_do_escopo", "fora_do_escopo"),
    ("caixa_cc", "ENVIO TED 000324", "debito", "fora_do_escopo", "fora_do_escopo"),
    ("bradesco_cc", "DEBITO AUTOMATICO CGMP-SEM PARAR/SP*- 309724", "debito", "fora_do_escopo", "fora_do_escopo"),
    ("bb_cc", "Liquid Contr Câmbio Exp", "credito", "acao_manual", "cambio_bb"),
    ("bb_cc", "Débito Serv.Cambio", "debito", "acao_manual", "cambio_bb"),
    ("bb_cc", "Tar Liquid Orpag Exterior", "debito", "tarifa", "tarifa"),
    ("bb_cc", "Cap Giro Dig Amortização", "debito", "acao_manual", "amortizacao_contrato"),
    # Santander: créditos de cobrança na caução
    ("santander_caucao_432", "COBRANCA GARANTIA 000000", "credito", "consolidacao", "consolidacao_santander"),
    # Rendimento
    ("itau_cc", "REND PAGO APLIC AUT MAIS", "credito", "rendimento", "rendimento"),
    ("bb_cc", "Rendimento aplicação", "credito", "rendimento", "rendimento"),
    # Tarifas
    ("abc_cc", "TARIFA MANUTENCAO TIT.VENCIDO", "debito", "tarifa", "tarifa"),
    ("abc_cc", "PAGTO. DESPESAS DE CARTORIO", "debito", "tarifa", "tarifa"),
    ("itau_cc", "TAR/CUSTAS COBRANCA", "debito", "tarifa_agrupada", "tarifa_agrupada_itau"),  # cliente 05/10
    ("itau_cc", "TAR NEGAT EXC 468506896", "debito", "tarifa", "tarifa"),
    ("caixa_cc", "COB LOTERI 090926", "debito", "tarifa", "tarifa"),
    ("caixa_cc", "COB COMPE 090926", "debito", "tarifa", "tarifa"),
    ("santander_cc", "DEB. CUSTAS CARTORARIAS COBRA NCA 000000", "debito", "tarifa", "tarifa"),
    ("bb_cc", "Débito Serviço Cobrança", "debito", "tarifa", "tarifa"),
    ("bb_cc", "Débito Serviço Cobrança Tar. agrupadas - ocorrencia 08/09/2026", "debito", "tarifa_agrupada", "tarifa_agrupada_bb"),
    # Fora do escopo
    ("itau_cc", "PAGAMENTOS A FORNECEDORES", "debito", "fora_do_escopo", "fora_do_escopo"),
    ("itau_cc", "SISPAG TRIBUTOS GNRE/MG", "debito", "fora_do_escopo", "fora_do_escopo"),
    ("bb_cc", "Cobrança", "credito", "fora_do_escopo", "fora_do_escopo"),
    ("itau_caucao", "BOLETO RECEBIDO 09/09L", "credito", "fora_do_escopo", "fora_do_escopo"),
    # Respostas da cliente (05/10/2026)
    ("safra_cc", "PACOTE PJ SIMPLES 5802757", "debito", "tarifa", "tarifa"),
    ("bradesco_cc", "TARIFA BANCARIA TRANSF PGTO PIX 30926", "debito", "tarifa", "tarifa"),
    ("itau_cc", "KG 3361823", "debito", "acao_manual", "kg_contas_a_pagar"),
    ("itau_cc", "DEV PAG BOL JADIMO TRANSPORTES RODOVIARI", "credito", "fora_do_escopo", "fora_do_escopo"),
    # Daycoval: caução -> CC ("TRANSF.MESMA TITULARIDADE" nos dois extratos)
    ("daycoval_caucao", "TRANSF.MESMA TITULARIDADE", "debito", "transferencia", "liberacao_caucao"),
    ("daycoval_cc", "TRANSF.MESMA TITULARIDADE", "credito", "contrapartida", "contrapartida_liberacao"),
    ("daycoval_cc", "TARIFA DE MANUTENCAO DE C/C", "debito", "tarifa", "tarifa"),
    # Não reconhecido: vira pendência
    ("daycoval_cc", "AMORT. DE CONTRATO 99EJDSR", "debito", "acao_manual", "amortizacao_contrato"),
])
def test_classificacao(cadastro, conta, historico, operacao, categoria, regra):
    resultado = classificar(_lancamento(cadastro, conta, historico, operacao), cadastro.regras)
    assert (resultado.categoria.value, resultado.regra) == (categoria, regra)


def test_transferencia_sem_fronteira_nao_vira_transferencia(cadastro):
    # "TRANSACAO" não pode casar com a regra de "TRANSF"
    resultado = classificar(_lancamento(cadastro, "itau_caucao", "TRANSACAO CARTAO", "debito"), cadastro.regras)
    assert resultado.categoria.value == "nao_reconhecido"
