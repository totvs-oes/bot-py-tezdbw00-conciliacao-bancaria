"""Planejamento: extratos lidos -> Plano do que lançar e conciliar no Protheus.

Módulo puro (sem I/O): toda regra de negócio fica aqui e é coberta por testes.
"""
from __future__ import annotations

import hashlib
import re
from collections import Counter, defaultdict
from datetime import date, datetime
from decimal import Decimal
from typing import Iterable, Optional

from conciliacao.classificacao import classificar
from conciliacao.configuracao import Cadastro
from conciliacao.modelos import (Categoria, Classificacao, Conciliacao, Conta, ExtratoLido,
                                 LancamentoExtrato, Pendencia, Plano, Rendimento, Rotina, Tarifa,
                                 Transferencia, formatar_brl)
from conciliacao.extratos.pastas import dia_util_seguinte
from conciliacao.texto import normalizar

TAMANHO_HISTORICO = 40  # E5_HISTOR
RE_OCORRENCIA = re.compile(r"OCORRENCIA (\d{2}/\d{2}/\d{4})")


def planejar(extratos: list[ExtratoLido], cadastro: Cadastro, data_movimento: date,
             feriados: Iterable[date] = ()) -> Plano:
    plano = Plano(data_movimento=data_movimento, extratos=extratos)
    planejador = _Planejador(cadastro, plano, dia_util_seguinte(data_movimento, feriados))
    planejador.executar()
    _atribuir_chaves(plano)
    return plano


class _Planejador:
    def __init__(self, cadastro: Cadastro, plano: Plano, proximo_dia_util: date):
        self.cadastro = cadastro
        self.plano = plano
        self.dia = plano.data_movimento
        self.proximo_dia_util = proximo_dia_util
        self.contrapartidas: list[tuple[LancamentoExtrato, Classificacao]] = []
        self.consolidacoes: dict[tuple[str, str], list[LancamentoExtrato]] = defaultdict(list)
        self.agrupadas: list[LancamentoExtrato] = []
        self.transferencias_do_extrato: list[Transferencia] = []
        # Transferências lançadas a partir do DÉBITO (caução/vinculada): o crédito deve aparecer no destino
        self.saidas_de_caucao: list[Transferencia] = []

    # ------------------------------------------------------------------
    def executar(self) -> None:
        normais = [e for e in self.plano.extratos if not e.eh_detalhe_tarifas]
        for extrato in normais:
            if self._extrato_utilizavel(extrato):
                for lancamento in extrato.lancamentos:
                    if lancamento.data == self.dia:
                        self._tratar(lancamento)
                self._planejar_conciliacao(extrato)

        self.plano.transferencias = self._consolidacoes_santander() + self.transferencias_do_extrato
        self._parear_contrapartidas()
        self._detalhar_tarifas_agrupadas()

    def _pendencia(self, arquivo: str, motivo: str, acao: str, lancamento: Optional[LancamentoExtrato] = None,
                   conta: Optional[Conta] = None) -> None:
        self.plano.pendencias.append(Pendencia(
            arquivo=arquivo, motivo=motivo, acao=acao,
            data=lancamento.data if lancamento else None,
            historico=lancamento.historico if lancamento else None,
            valor=lancamento.valor if lancamento else None,
            conta=lancamento.conta if lancamento else conta,
        ))

    def _extrato_utilizavel(self, extrato: ExtratoLido) -> bool:
        if extrato.conta is None:
            self._pendencia(extrato.arquivo, "Arquivo não corresponde a nenhuma conta de config/contas.yaml.",
                            "Conferir o nome do arquivo ou cadastrar a conta em config/contas.yaml.")
            return False
        if extrato.metodo == "nenhum":
            self._pendencia(extrato.arquivo, f"A API não conseguiu ler o extrato: {extrato.aviso or 'sem detalhe'}.",
                            "Lançar manualmente ou reenviar um PDF com texto.", conta=extrato.conta)
            return False
        if extrato.conferencia_ok is False:
            self._pendencia(extrato.arquivo,
                            "Conferência do extrato falhou (saldo anterior + lançamentos ≠ saldo final). "
                            "Nenhum lançamento desta conta foi planejado.",
                            "Verificar a leitura do PDF na API e lançar manualmente se necessário.",
                            conta=extrato.conta)
            return False
        if extrato.aviso:
            self._pendencia(extrato.arquivo, f"Aviso da leitura: {extrato.aviso}", "Conferir o extrato.",
                            conta=extrato.conta)
        return True

    # ------------------------------------------------------------------
    def _tratar(self, lancamento: LancamentoExtrato) -> None:
        classificacao = classificar(lancamento, self.cadastro.regras)
        categoria = classificacao.categoria
        if categoria == Categoria.TRANSFERENCIA:
            self._transferencia(lancamento, classificacao)
        elif categoria == Categoria.CONTRAPARTIDA:
            self.contrapartidas.append((lancamento, classificacao))
        elif categoria == Categoria.CONSOLIDACAO:
            self.consolidacoes[(lancamento.conta.chave, classificacao.origem_conta)].append(lancamento)
        elif categoria == Categoria.TARIFA:
            self.plano.tarifas.append(self._tarifa(lancamento, lancamento.historico, f"{lancamento.arquivo}#{lancamento.indice}"))
        elif categoria == Categoria.TARIFA_AGRUPADA:
            self.agrupadas.append(lancamento)
        elif categoria == Categoria.RENDIMENTO:
            self.plano.rendimentos.append(Rendimento(
                rotina=Rotina.RENDIMENTO, data=self.dia, valor=lancamento.valor, conta=lancamento.conta,
                historico=self.cadastro.constantes["historico_rendimento"],
                origem_extrato=f"{lancamento.arquivo}#{lancamento.indice}",
            ))
        elif categoria == Categoria.FORA_DO_ESCOPO:
            self.plano.fora_do_escopo.append(lancamento)
        elif categoria == Categoria.ACAO_MANUAL:
            self._pendencia(lancamento.arquivo, classificacao.motivo or f"Regra {classificacao.regra}.",
                            classificacao.acao or "Tratar manualmente.", lancamento)
        else:
            self._pendencia(lancamento.arquivo, "Lançamento não reconhecido por nenhuma regra.",
                            "Lançar manualmente ou criar regra em config/regras.yaml.", lancamento)

    def _tarifa(self, lancamento: LancamentoExtrato, historico: str, origem: str) -> Tarifa:
        return Tarifa(rotina=Rotina.TARIFA, data=self.dia, valor=lancamento.valor, conta=lancamento.conta,
                      historico=normalizar(historico)[:TAMANHO_HISTORICO], origem_extrato=origem)

    def _conta_unica(self, banco: str, tipos: tuple[str, ...] | str) -> Optional[Conta]:
        tipos = (tipos,) if isinstance(tipos, str) else tipos
        candidatas = [c for tipo in tipos for c in self.cadastro.contas_do_banco(banco, tipo)]
        return candidatas[0] if len(candidatas) == 1 else None

    def _historico_transferencia(self, modelo: Optional[str], origem: Conta, destino: Conta) -> str:
        abreviacao = self.cadastro.constantes["abreviacao_tipo"]
        texto = (modelo or self.cadastro.constantes["historico_transferencia"]).format(
            so=self.cadastro.bancos[origem.banco].sigla, to=abreviacao[origem.tipo.value],
            sd=self.cadastro.bancos[destino.banco].sigla, td=abreviacao[destino.tipo.value],
        )
        return texto[:TAMANHO_HISTORICO]

    def _transferencia(self, lancamento: LancamentoExtrato, classificacao: Classificacao) -> None:
        conta = lancamento.conta
        if classificacao.destino_tipo:
            origem, destino = conta, self._conta_unica(conta.banco, classificacao.destino_tipo)
            falta = classificacao.destino_tipo
        else:
            origem, destino = self._conta_unica(conta.banco, classificacao.origem_tipo), conta
            falta = "/".join(classificacao.origem_tipo or ())
        if origem is None or destino is None:
            self._pendencia(lancamento.arquivo,
                            f"Transferência ({classificacao.regra}): não há exatamente uma conta '{falta}' "
                            f"do banco {conta.banco} em config/contas.yaml.",
                            "Ajustar o cadastro de contas ou lançar manualmente.", lancamento)
            return
        transferencia = Transferencia(
            rotina=Rotina.TRANSFERENCIA, data=self.dia, valor=lancamento.valor, origem=origem, destino=destino,
            historico=self._historico_transferencia(classificacao.historico_protheus, origem, destino),
            origem_extrato=f"{lancamento.arquivo}#{lancamento.indice}",
        )
        self.transferencias_do_extrato.append(transferencia)
        if classificacao.destino_tipo == "corrente":
            self.saidas_de_caucao.append(transferencia)

    # ------------------------------------------------------------------
    def _consolidacoes_santander(self) -> list[Transferencia]:
        """Santander: no Protheus tudo entra na caução central; ela é redistribuída para as cauções do extrato."""
        resultado = []
        modelos = {r.nome: r for r in self.cadastro.regras}
        for (chave_destino, chave_origem), lancamentos in self.consolidacoes.items():
            destino, origem = self.cadastro.conta(chave_destino), self.cadastro.conta(chave_origem)
            regra = modelos.get("consolidacao_santander")
            resultado.append(Transferencia(
                rotina=Rotina.TRANSFERENCIA, data=self.dia, valor=sum((l.valor for l in lancamentos), Decimal("0")),
                origem=origem, destino=destino,
                historico=self._historico_transferencia(regra.historico_protheus if regra else None, origem, destino),
                origem_extrato=f"{lancamentos[0].arquivo}#consolidacao({len(lancamentos)} créditos)",
            ))
        return resultado

    def _parear_contrapartidas(self) -> None:
        """Cada crédito de contrapartida precisa de uma transferência planejada com mesmo destino, data e valor."""
        livres = list(self.transferencias_do_extrato)
        for lancamento, classificacao in self.contrapartidas:
            par = next((t for t in livres
                        if t.destino == lancamento.conta and t.valor == lancamento.valor
                        and t.origem.tipo.value in (classificacao.origem_tipo or ())), None)
            if par:
                livres.remove(par)
            else:
                self._pendencia(lancamento.arquivo,
                                "Crédito de transferência sem o débito correspondente nos extratos de origem "
                                "(extrato da caução/vinculada ausente ou valor divergente).",
                                "Conferir o extrato da conta de origem; lançar a transferência manualmente se for o caso.",
                                lancamento)

        contas_com_extrato = {e.conta for e in self.plano.extratos if e.conta and not e.eh_detalhe_tarifas}
        for transferencia in livres:
            if transferencia in self.saidas_de_caucao and transferencia.destino in contas_com_extrato:
                transferencia.aviso = "Crédito correspondente não encontrado no extrato da conta destino."

    def _detalhar_tarifas_agrupadas(self) -> None:
        """Tarifa agrupada: lança as tarifas do extrato detalhado, se a soma bater com o agrupado."""
        por_ocorrencia: dict[tuple[str, date], list[LancamentoExtrato]] = defaultdict(list)
        for lancamento in self.agrupadas:
            match = RE_OCORRENCIA.search(normalizar(lancamento.historico))
            ocorrencia = datetime.strptime(match.group(1), "%d/%m/%Y").date() if match else lancamento.data
            por_ocorrencia[(lancamento.conta.banco, ocorrencia)].append(lancamento)

        detalhes = [e for e in self.plano.extratos if e.eh_detalhe_tarifas]
        for (banco, ocorrencia), agrupadas in por_ocorrencia.items():
            individuais = [l for e in detalhes if e.banco == banco for l in e.lancamentos
                           if l.data == ocorrencia and l.operacao == "debito"]
            total_agrupado = sum((l.valor for l in agrupadas), Decimal("0"))
            total_detalhe = sum((l.valor for l in individuais), Decimal("0"))

            if individuais and total_detalhe == total_agrupado:
                conta_debito = agrupadas[0].conta
                for item in individuais:
                    tarifa = self._tarifa(item, item.historico, f"{item.arquivo}#{item.indice}")
                    tarifa.conta = conta_debito
                    self.plano.tarifas.append(tarifa)
                continue

            motivo = ("Extrato de tarifas detalhado não encontrado para a ocorrência."
                      if not individuais else
                      f"Soma do detalhamento (R$ {formatar_brl(total_detalhe)}) diferente do agrupado "
                      f"(R$ {formatar_brl(total_agrupado)}).")
            for lancamento in agrupadas:
                self._pendencia(lancamento.arquivo, f"Tarifa agrupada de {ocorrencia:%d/%m/%Y}: {motivo}",
                                "Lançar as tarifas individualmente a partir do extrato de tarifas.", lancamento)

    # ------------------------------------------------------------------
    def _planejar_conciliacao(self, extrato: ExtratoLido) -> None:
        conta = extrato.conta
        if not conta.conciliar:
            return
        if extrato.conferencia_ok is not True or extrato.saldo_anterior is None:
            self._pendencia(extrato.arquivo, "Extrato sem saldo conferido: a conciliação automática não será feita.",
                            "Conciliar manualmente no Conciliador Backoffice.", conta=conta)
            return
        # Se o extrato começa no dia útil seguinte, o saldo anterior dele É o saldo do fim do dia do movimento.
        # Começando depois disso, pode ter havido movimento no meio que o arquivo não mostra.
        datas = [l.data for l in extrato.lancamentos]
        if datas and min(datas) > self.proximo_dia_util:
            self._pendencia(extrato.arquivo,
                            f"O extrato começa em {min(datas):%d/%m/%Y}, depois do dia útil seguinte ao movimento: "
                            "não é possível calcular o saldo do dia.",
                            "Conferir se o extrato correto está na pasta.", conta=conta)
            return

        saldo = extrato.saldo_anterior
        for lancamento in extrato.lancamentos:
            if lancamento.data <= self.dia:
                saldo += lancamento.valor if lancamento.operacao == "credito" else -lancamento.valor
        for data_ajuste, valor in extrato.ajustes:
            if data_ajuste <= self.dia:
                saldo += valor
        self.plano.conciliacoes.append(Conciliacao(conta=conta, data=self.dia, saldo_extrato=saldo))


# ----------------------------------------------------------------------
def _atribuir_chaves(plano: Plano) -> None:
    """Chave estável de idempotência: não depende do nome do arquivo (o ATUALIZADO substitui o original).

    Itens idênticos no mesmo dia (ex.: duas tarifas de R$ 4,85) são diferenciados pela ordem de ocorrência.
    """
    ocorrencias: Counter[str] = Counter()
    for item in plano.itens():
        base = f"{item.rotina.value}|{item.data.isoformat()}|{item.contas()}|{item.valor}|{item.historico}"
        ocorrencias[base] += 1
        item.chave = hashlib.sha1(f"{base}|{ocorrencias[base]}".encode()).hexdigest()[:16]


def documento(data: date, sequencia: int) -> str:
    """DDMMAA; a partir do segundo lançamento do mesmo tipo/banco/dia: DDMMAA1, DDMMAA2..."""
    base = data.strftime("%d%m%y")
    return base if sequencia == 0 else f"{base}{sequencia}"
