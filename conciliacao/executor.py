"""Executa o Plano no Protheus, com idempotência (registro) e a política de erro da especificação:
falha técnica -> espera 5 min e tenta de novo uma vez -> se falhar, interrompe e notifica a TI.
"""
from __future__ import annotations

import logging
import time
import traceback
from pathlib import Path
from typing import Callable, Optional

from playwright.sync_api import Error as ErroPlaywright

from conciliacao.configuracao import Ambiente, Cadastro
from conciliacao.modelos import ItemPlano, Plano, Rendimento, Rotina, Tarifa, Transferencia, formatar_brl
from conciliacao.protheus import conciliador, movimento_bancario
from conciliacao.protheus.sessao import SessaoProtheus
from conciliacao.protheus.tela import TIMEOUT_PADRAO, ErroDeTela, MensagemProtheus
from conciliacao.registro import (CONCLUIDO, ERRO, EXECUTANDO, INCERTO, PODE_EXECUTAR, SALVANDO, Registro)
from conciliacao.relatorio import ResultadoExecucao

log = logging.getLogger(__name__)

ESPERA_RETENTATIVA = 5 * 60  # segundos (spec v1.2: "tentar novamente após 5 minutos")
FALHAS_TECNICAS = (ErroDeTela, ErroPlaywright, TimeoutError, ConnectionError)

DIVERGENTE = "divergente"
ENSAIADO = "ensaiado"  # só existe dentro da simulação do registro (modo ensaio)
CONCILIADO = "conciliado"


def _funcao(item: ItemPlano) -> Callable:
    if isinstance(item, Transferencia):
        return movimento_bancario.incluir_transferencia
    if isinstance(item, Rendimento):
        return movimento_bancario.incluir_rendimento
    if isinstance(item, Tarifa):
        return movimento_bancario.incluir_tarifa
    raise TypeError(type(item))


class Executor:
    def __init__(self, plano: Plano, cadastro: Cadastro, ambiente: Ambiente, registro: Registro,
                 pasta_saida: Path, rotinas: set[Rotina], ensaio: bool = False, limite: Optional[int] = None):
        """ensaio: percorre as telas preenchendo e conferindo tudo, mas CANCELA em vez de gravar; o registro
        não é alterado. limite: no máximo N lançamentos (útil para homologar aos poucos)."""
        self.plano = plano
        self.cadastro = cadastro
        self.ambiente = ambiente
        self.registro = registro
        self.pasta_prints = pasta_saida / ("prints_ensaio" if ensaio else "prints")
        self.rotinas = rotinas
        self.ensaio = ensaio
        self.limite = limite
        self.resultado = ResultadoExecucao()

    def executar(self) -> ResultadoExecucao:
        if self.ensaio:
            with self.registro.simulacao():
                return self._executar(tentativas=1)
        return self._executar(tentativas=2)

    def _executar(self, tentativas: int) -> ResultadoExecucao:
        interrompidos = self.registro.recuperar_interrompidos()
        if interrompidos:
            log.warning("%d item(ns) de execução anterior interrompida", interrompidos)
        self.registro.registrar_plano(self.plano)

        for tentativa in range(1, tentativas + 1):
            try:
                with SessaoProtheus(self.ambiente, self.plano.data_movimento, self.cadastro.constantes["filial"],
                                    self.pasta_prints, cdp_url=self.ambiente.protheus_cdp_url) as sessao:
                    self._executar_itens(sessao)
                    self._executar_conciliacoes(sessao)
                self.resultado.erro_geral = None
                break
            except FALHAS_TECNICAS as erro:
                detalhe = f"{type(erro).__name__}: {erro}"
                log.error("Falha técnica (tentativa %d): %s\n%s", tentativa, detalhe, traceback.format_exc())
                self.resultado.erro_geral = f"Falha técnica após {tentativa} tentativa(s) — {detalhe}"
                if tentativa < tentativas:
                    log.info("Nova tentativa em %d minutos", ESPERA_RETENTATIVA // 60)
                    time.sleep(ESPERA_RETENTATIVA)

        self._coletar_status()
        return self.resultado

    # ------------------------------------------------------------------
    def _executar_itens(self, sessao: SessaoProtheus) -> None:
        feitos = 0
        for item in self.plano.itens():
            if item.rotina not in self.rotinas:
                continue
            status = self.registro.status(item.chave)
            if status not in PODE_EXECUTAR:
                continue  # concluído ou incerto: nunca refazer
            if self.limite is not None and feitos >= self.limite:
                break
            feitos += 1

            log.info("%s%s %s R$ %s doc %s", "[ENSAIO] " if self.ensaio else "", item.rotina.value,
                     item.contas(), formatar_brl(item.valor), item.documento)
            try:
                self._executar_item(sessao, item)
            finally:
                sessao.tela.page.set_default_timeout(TIMEOUT_PADRAO)

    def _executar_item(self, sessao: SessaoProtheus, item: ItemPlano) -> None:
        for tentativa in (1, 2):
            self.registro.marcar(item.chave, EXECUTANDO)
            try:
                _funcao(item)(sessao, item, self.cadastro.constantes,
                              antes_de_salvar=lambda: self.registro.marcar(item.chave, SALVANDO),
                              ensaio=self.ensaio)
                self.registro.marcar(item.chave, ENSAIADO if self.ensaio else CONCLUIDO)
                return
            except MensagemProtheus as mensagem:
                # Regra de negócio recusada pelo Protheus (ex.: conta não cadastrada): segue para o próximo item.
                # Formulário ainda aberto com o nosso lançamento = recusado antes de gravar -> erro, não incerto.
                # Antes do gravar (EXECUTANDO) o formulário é nosso mesmo sem histórico: o Help pode vir num campo
                # anterior (ex.: 100DOCEXIS no Número Doc.). Depois do gravar, só se o histórico for o nosso.
                antes_de_gravar = self.registro.status(item.chave) == EXECUTANDO
                recusado = movimento_bancario.descartar_formulario(
                    sessao.tela, None if antes_de_gravar else item.historico)
                self._falhou(item, f"Protheus recusou: {mensagem}", recusado)
                return
            except FALHAS_TECNICAS as erro:
                sessao.tela.print(f"erro_{item.chave}")
                # Falhou ANTES de gravar (preenchimento/conferência): descarta o formulário e refaz o item na hora
                if tentativa == 1 and self.registro.status(item.chave) == EXECUTANDO and self._descartar(sessao):
                    log.warning("Falha antes de gravar (%s: %s); formulário descartado, refazendo o item",
                                type(erro).__name__, erro)
                    continue
                self._falhou(item, f"{type(erro).__name__}: {erro}")
                raise

    @staticmethod
    def _descartar(sessao: SessaoProtheus) -> bool:
        try:
            movimento_bancario.descartar_formulario(sessao.tela)
            return True
        except FALHAS_TECNICAS:
            log.exception("Não consegui descartar o formulário")
            return False

    def _falhou(self, item: ItemPlano, detalhe: str, recusado: bool = False) -> None:
        depois_de_salvar = self.registro.status(item.chave) == SALVANDO and not recusado
        self.registro.marcar(item.chave, INCERTO if depois_de_salvar else ERRO, detalhe)
        log.error("%s %s: %s", item.rotina.value, item.chave, detalhe)

    def _contas_com_problema(self) -> set[str]:
        # No ensaio nada é gravado: lançamento ensaiado conta como concluído só para LER o saldo no Conciliador
        # (o ensaio nunca aplica a conciliação)
        concluidos = (CONCLUIDO, ENSAIADO) if self.ensaio else (CONCLUIDO,)
        problemas = set()
        for item in self.plano.itens():
            if self.registro.status(item.chave) not in concluidos:
                problemas.update(item.contas().split(">"))
        return problemas

    def _executar_conciliacoes(self, sessao: SessaoProtheus) -> None:
        if Rotina.CONCILIACAO not in self.rotinas:
            return
        com_problema = self._contas_com_problema()
        for conciliacao in self.plano.conciliacoes:
            chave = conciliacao.conta.chave
            if self.registro.status_conciliacao(conciliacao) == CONCILIADO:
                self.resultado.conciliacoes[chave] = (CONCILIADO, "já conciliada em execução anterior")
                continue
            if chave in com_problema:
                self.resultado.conciliacoes[chave] = ("não executada", "há lançamentos da conta não concluídos")
                continue
            try:
                conciliou, saldo_protheus = conciliador.conciliar(sessao, conciliacao, ensaio=self.ensaio)
            except MensagemProtheus as mensagem:
                self.registro.marcar_conciliacao(conciliacao, ERRO, detalhe=str(mensagem))
                self.resultado.conciliacoes[chave] = (ERRO, str(mensagem))
                continue
            if conciliou:
                self.registro.marcar_conciliacao(conciliacao, CONCILIADO, saldo_protheus)
                self.resultado.conciliacoes[chave] = (CONCILIADO, None)
            elif self.ensaio and saldo_protheus == conciliacao.saldo_extrato:
                self.resultado.conciliacoes[chave] = (
                    "pronta para conciliar", f"Saldo Protheus = extrato = R$ {formatar_brl(saldo_protheus)} (ensaio)")
            else:
                gap = saldo_protheus - conciliacao.saldo_extrato
                detalhe = (f"Saldo Protheus R$ {formatar_brl(saldo_protheus)} × extrato "
                           f"R$ {formatar_brl(conciliacao.saldo_extrato)} (diferença R$ {formatar_brl(gap)}). "
                           "Investigar no Contas a Receber.")
                self.registro.marcar_conciliacao(conciliacao, DIVERGENTE, saldo_protheus, detalhe)
                self.resultado.conciliacoes[chave] = (DIVERGENTE, detalhe)

    def _coletar_status(self) -> None:
        for linha in self.registro.itens_do_dia(self.plano.data_movimento):
            self.resultado.status_itens[linha["chave"]] = linha["status"]
            if linha["detalhe"]:
                self.resultado.detalhes_itens[linha["chave"]] = linha["detalhe"]
