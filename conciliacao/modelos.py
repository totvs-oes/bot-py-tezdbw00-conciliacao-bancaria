"""Modelos de domínio. Valores monetários sempre em Decimal com 2 casas."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from enum import Enum
from typing import Literal, Optional

CENTAVO = Decimal("0.01")


def dinheiro(valor) -> Decimal:
    """Converte float/str/int para Decimal com 2 casas (str(float) evita o erro binário do float)."""
    return Decimal(str(valor)).quantize(CENTAVO, rounding=ROUND_HALF_UP)


def formatar_brl(valor: Decimal) -> str:
    texto = f"{valor:,.2f}"
    return texto.replace(",", "X").replace(".", ",").replace("X", ".")


class TipoConta(str, Enum):
    CORRENTE = "corrente"
    CAUCAO = "caucao"
    VINCULADA = "vinculada"
    APLICACAO = "aplicacao"
    CAMBIO = "cambio"
    AUXILIAR = "auxiliar"


class Categoria(str, Enum):
    TRANSFERENCIA = "transferencia"
    CONTRAPARTIDA = "contrapartida"
    CONSOLIDACAO = "consolidacao"
    TARIFA = "tarifa"
    TARIFA_AGRUPADA = "tarifa_agrupada"
    RENDIMENTO = "rendimento"
    FORA_DO_ESCOPO = "fora_do_escopo"
    NAO_RECONHECIDO = "nao_reconhecido"


class Rotina(str, Enum):
    TRANSFERENCIA = "transferencia"   # Rotina 2
    TARIFA = "tarifa"                 # Rotina 3
    RENDIMENTO = "rendimento"         # Rotina 4
    CONCILIACAO = "conciliacao"       # Rotina 5


@dataclass(frozen=True)
class Banco:
    codigo: str
    nome: str
    sigla: str
    arquivo_tarifas: Optional[str] = None


@dataclass(frozen=True)
class Conta:
    chave: str
    banco: str
    agencia: str
    numero: str
    tipo: TipoConta
    arquivo: Optional[str] = None
    conciliar: bool = True

    def __str__(self) -> str:
        return f"{self.banco}/{self.agencia}/{self.numero}"


@dataclass(frozen=True)
class LancamentoExtrato:
    conta: Conta
    arquivo: str
    indice: int                      # posição no extrato: diferencia lançamentos idênticos
    data: date
    historico: str                   # como veio da API
    operacao: Literal["credito", "debito"]
    valor: Decimal


@dataclass
class ExtratoLido:
    """Um PDF já transcrito pela API e associado a uma conta do Protheus."""
    arquivo: str
    conta: Optional[Conta]
    eh_detalhe_tarifas: bool
    banco: Optional[str]
    metodo: str
    aviso: Optional[str]
    conferencia_ok: Optional[bool]
    saldo_anterior: Optional[Decimal]
    lancamentos: list[LancamentoExtrato] = field(default_factory=list)
    # Movimentações deduzidas dos saldos pela API (ex.: aplicação automática): (data, valor com sinal)
    ajustes: list[tuple[date, Decimal]] = field(default_factory=list)


@dataclass(frozen=True)
class Classificacao:
    categoria: Categoria
    regra: Optional[str]
    destino_tipo: Optional[str] = None
    origem_tipo: Optional[tuple[str, ...]] = None
    origem_conta: Optional[str] = None
    historico_protheus: Optional[str] = None


# ---------------------------------------------------------------------------
# Itens do plano (o que o robô vai fazer no Protheus)
# ---------------------------------------------------------------------------

@dataclass(kw_only=True)
class ItemPlano:
    rotina: Rotina
    data: date
    valor: Decimal
    historico: str                   # histórico gravado no Protheus
    origem_extrato: str              # "arquivo#indice" para rastreabilidade
    chave: str = ""                  # identidade estável (idempotência), definida pelo planejamento
    documento: Optional[str] = None  # atribuído pelo registro (DDMMAA + sequencial)
    aviso: Optional[str] = None

    def banco_documento(self) -> str:
        """Banco que define a sequência do número do documento."""
        raise NotImplementedError

    def contas(self) -> str:
        raise NotImplementedError


@dataclass(kw_only=True)
class Transferencia(ItemPlano):
    origem: Conta
    destino: Conta

    def banco_documento(self) -> str:
        return self.origem.banco

    def contas(self) -> str:
        return f"{self.origem.chave}>{self.destino.chave}"


@dataclass(kw_only=True)
class Tarifa(ItemPlano):
    conta: Conta

    def banco_documento(self) -> str:
        return self.conta.banco

    def contas(self) -> str:
        return self.conta.chave


@dataclass(kw_only=True)
class Rendimento(Tarifa):
    pass


@dataclass
class Conciliacao:
    conta: Conta
    data: date
    saldo_extrato: Decimal


@dataclass
class Pendencia:
    arquivo: str
    motivo: str
    acao: str
    data: Optional[date] = None
    historico: Optional[str] = None
    valor: Optional[Decimal] = None
    conta: Optional[Conta] = None


@dataclass
class Plano:
    data_movimento: date
    transferencias: list[Transferencia] = field(default_factory=list)
    tarifas: list[Tarifa] = field(default_factory=list)
    rendimentos: list[Rendimento] = field(default_factory=list)
    conciliacoes: list[Conciliacao] = field(default_factory=list)
    pendencias: list[Pendencia] = field(default_factory=list)
    fora_do_escopo: list[LancamentoExtrato] = field(default_factory=list)
    extratos: list[ExtratoLido] = field(default_factory=list)

    def itens(self) -> list[ItemPlano]:
        """Na ordem de execução do processo: transferências, tarifas, rendimentos."""
        return [*self.transferencias, *self.tarifas, *self.rendimentos]
