"""Classificação de um lançamento de extrato pelas regras de config/regras.yaml (primeira que casar)."""
from __future__ import annotations

from conciliacao.configuracao import Regra
from conciliacao.modelos import Categoria, Classificacao, LancamentoExtrato
from conciliacao.texto import normalizar


def _casa(regra: Regra, lancamento: LancamentoExtrato, historico: str) -> bool:
    if regra.operacao and regra.operacao != lancamento.operacao:
        return False
    if regra.tipos_conta and lancamento.conta.tipo.value not in regra.tipos_conta:
        return False
    if regra.bancos and lancamento.conta.banco not in regra.bancos:
        return False
    return bool(regra.historico.search(historico))


def classificar(lancamento: LancamentoExtrato, regras: list[Regra]) -> Classificacao:
    historico = normalizar(lancamento.historico)
    for regra in regras:
        if _casa(regra, lancamento, historico):
            return Classificacao(
                categoria=Categoria(regra.categoria),
                regra=regra.nome,
                destino_tipo=regra.destino_tipo,
                origem_tipo=regra.origem_tipo,
                origem_conta=regra.origem_conta,
                historico_protheus=regra.historico_protheus,
            )
    return Classificacao(categoria=Categoria.NAO_RECONHECIDO, regra=None)
