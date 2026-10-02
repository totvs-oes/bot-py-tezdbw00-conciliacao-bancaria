"""Converte a resposta da API de extratos nos modelos do RPA (associa cada PDF a uma conta do Protheus)."""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from conciliacao.configuracao import Cadastro
from conciliacao.extratos.pastas import banco_do_detalhe_de_tarifas, conta_do_arquivo
from conciliacao.modelos import ExtratoLido, LancamentoExtrato, dinheiro


def _data(texto: Optional[str]):
    return datetime.strptime(texto, "%d/%m/%Y").date() if texto else None


def ler_resposta_api(resposta: dict[str, list[dict]], cadastro: Cadastro) -> list[ExtratoLido]:
    extratos = []
    for chave_banco, lista in resposta.items():
        for bruto in lista:
            extratos.append(_ler_extrato(bruto, chave_banco, cadastro))
    return sorted(extratos, key=lambda e: e.arquivo)


def _ler_extrato(bruto: dict, chave_banco: str, cadastro: Cadastro) -> ExtratoLido:
    arquivo = bruto["arquivo"]
    banco_detalhe = banco_do_detalhe_de_tarifas(arquivo, cadastro)
    conta = None
    if banco_detalhe:
        # Extrato detalhado de tarifas: as tarifas são debitadas na conta corrente do banco
        correntes = cadastro.contas_do_banco(banco_detalhe, "corrente")
        conta = correntes[0] if correntes else None
    else:
        conta = conta_do_arquivo(arquivo, cadastro)

    conferencia = bruto.get("conferencia") or {}
    saldo_anterior = conferencia.get("saldo_anterior")
    extrato = ExtratoLido(
        arquivo=arquivo,
        conta=conta,
        eh_detalhe_tarifas=banco_detalhe is not None,
        banco=banco_detalhe or (conta.banco if conta else _banco_da_chave(chave_banco)),
        metodo=bruto.get("metodo", ""),
        aviso=bruto.get("aviso"),
        conferencia_ok=conferencia.get("ok"),
        saldo_anterior=dinheiro(saldo_anterior) if saldo_anterior is not None else None,
    )
    if conta is None:
        return extrato

    sem_data = 0
    for indice, item in enumerate(bruto.get("lancamentos", [])):
        data = _data(item.get("data"))
        if data is None:
            sem_data += 1
            continue
        extrato.lancamentos.append(LancamentoExtrato(
            conta=conta, arquivo=arquivo, indice=indice, data=data,
            historico=item["historico"], operacao=item["operacao"], valor=dinheiro(item["valor"]),
        ))
    for ajuste in conferencia.get("movimentacoes_nao_listadas") or []:
        data = _data(ajuste.get("data"))
        if data is None:
            sem_data += 1
            continue
        extrato.ajustes.append((data, dinheiro(ajuste["valor"])))

    if sem_data:
        extrato.aviso = ((extrato.aviso or "") + f" {sem_data} lançamento(s) sem data foram ignorados.").strip()
    return extrato


def _banco_da_chave(chave: str) -> Optional[str]:
    # "banco_0237" -> "237"
    codigo = chave.removeprefix("banco_")
    return codigo[-3:] if codigo.isdigit() else None
