"""Configuração: variáveis de ambiente (.env) e arquivos YAML de contas e regras."""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Optional

import yaml
from dotenv import load_dotenv

from conciliacao.modelos import Banco, Conta, TipoConta

RAIZ = Path(__file__).resolve().parent.parent
PASTA_CONFIG = RAIZ / "config"


@dataclass(frozen=True)
class Regra:
    nome: str
    categoria: str
    historico: re.Pattern
    operacao: Optional[str] = None
    tipos_conta: Optional[tuple[str, ...]] = None
    bancos: Optional[tuple[str, ...]] = None
    destino_tipo: Optional[str] = None
    origem_tipo: Optional[tuple[str, ...]] = None
    origem_conta: Optional[str] = None
    historico_protheus: Optional[str] = None
    motivo: Optional[str] = None   # (acao_manual) texto da pendência
    acao: Optional[str] = None


@dataclass
class Cadastro:
    """Contas, bancos, regras e constantes do Protheus (vindos de config/*.yaml)."""
    bancos: dict[str, Banco]
    contas: list[Conta]
    regras: list[Regra]
    constantes: dict

    def conta(self, chave: str) -> Conta:
        for conta in self.contas:
            if conta.chave == chave:
                return conta
        raise KeyError(f"Conta '{chave}' não existe em config/contas.yaml")

    def contas_do_banco(self, banco: str, tipo: str) -> list[Conta]:
        return [c for c in self.contas if c.banco == banco and c.tipo.value == tipo]


@dataclass
class Ambiente:
    """Variáveis de ambiente (.env)."""
    pasta_extratos: Path
    api_url: str
    api_token: str
    protheus_url: str
    protheus_ambiente: str
    protheus_usuario: str
    protheus_senha: str
    protheus_visivel: bool
    protheus_ignorar_certificado: bool
    protheus_navegador: str
    protheus_cdp_url: str
    feriados: frozenset[date]
    pasta_saida: Path
    smtp_host: str = ""
    smtp_porta: int = 587
    smtp_usuario: str = ""
    smtp_senha: str = field(default="", repr=False)
    email_remetente: str = ""
    email_operacao: list[str] = field(default_factory=list)
    email_ti: list[str] = field(default_factory=list)
    # "local": PDFs em PASTA_EXTRATOS | "sftp": a API de extratos busca no servidor do cliente
    fonte_extratos: str = "local"


def _tupla(valor) -> Optional[tuple[str, ...]]:
    if valor is None:
        return None
    if isinstance(valor, (list, tuple)):
        return tuple(str(v) for v in valor)
    return (str(valor),)


def _compilar(historico) -> re.Pattern:
    padrao = "|".join(f"(?:{p})" for p in historico) if isinstance(historico, list) else historico
    return re.compile(padrao)


def carregar_cadastro(pasta: Path = PASTA_CONFIG) -> Cadastro:
    with open(pasta / "contas.yaml", encoding="utf-8") as f:
        dados_contas = yaml.safe_load(f)
    with open(pasta / "regras.yaml", encoding="utf-8") as f:
        dados_regras = yaml.safe_load(f)

    bancos = {codigo: Banco(codigo=codigo, **dados) for codigo, dados in dados_contas["bancos"].items()}
    contas = [Conta(**{**c, "tipo": TipoConta(c["tipo"])}) for c in dados_contas["contas"]]

    chaves = [c.chave for c in contas]
    if len(chaves) != len(set(chaves)):
        raise ValueError("config/contas.yaml tem chaves de conta repetidas")

    regras = [
        Regra(
            nome=r["nome"],
            categoria=r["categoria"],
            historico=_compilar(r["historico"]),
            operacao=r.get("operacao"),
            tipos_conta=_tupla(r.get("tipos_conta")),
            bancos=_tupla(r.get("bancos")),
            destino_tipo=r.get("destino_tipo"),
            origem_tipo=_tupla(r.get("origem_tipo")),
            origem_conta=r.get("origem_conta"),
            historico_protheus=r.get("historico_protheus"),
            motivo=r.get("motivo"),
            acao=r.get("acao"),
        )
        for r in dados_regras["regras"]
    ]
    return Cadastro(bancos=bancos, contas=contas, regras=regras, constantes=dados_regras["constantes"])


def _url(valor: str) -> str:
    """O Chrome esconde o "https://" na barra de endereço; quem copia de lá costuma colar sem ele."""
    valor = valor.strip()
    if valor and not re.match(r"^https?://", valor, re.I):
        valor = "https://" + valor
    return valor


def _lista(valor: str) -> list[str]:
    return [v.strip() for v in valor.split(",") if v.strip()]


FONTES_EXTRATOS = ("local", "sftp")


def carregar_ambiente() -> Ambiente:
    load_dotenv(RAIZ / ".env")
    env = os.environ.get
    fonte_extratos = env("FONTE_EXTRATOS", "local").strip().lower()
    if fonte_extratos not in FONTES_EXTRATOS:
        raise ValueError(f"FONTE_EXTRATOS inválida: '{fonte_extratos}'. Use: {', '.join(FONTES_EXTRATOS)}")
    return Ambiente(
        fonte_extratos=fonte_extratos,
        pasta_extratos=Path(env("PASTA_EXTRATOS", r"Z:\A PAGAR\AEROFLEX")),
        api_url=env("API_EXTRATOS_URL", "http://localhost:5000/extratos"),
        api_token=env("API_EXTRATOS_TOKEN", ""),
        protheus_url=_url(env("PROTHEUS_URL", "")),
        protheus_ambiente=env("PROTHEUS_AMBIENTE", ""),
        protheus_usuario=env("PROTHEUS_USUARIO", ""),
        protheus_senha=env("PROTHEUS_SENHA", ""),
        protheus_visivel=env("PROTHEUS_VISIVEL", "true").lower() == "true",
        protheus_ignorar_certificado=env("PROTHEUS_IGNORAR_CERTIFICADO", "false").lower() == "true",
        protheus_navegador=env("PROTHEUS_NAVEGADOR", "chrome"),
        protheus_cdp_url=env("PROTHEUS_CDP_URL", ""),
        feriados=frozenset(date.fromisoformat(d) for d in _lista(env("FERIADOS", ""))),
        pasta_saida=RAIZ / env("PASTA_SAIDA", "saida"),
        smtp_host=env("SMTP_HOST", ""),
        smtp_porta=int(env("SMTP_PORTA", "587")),
        smtp_usuario=env("SMTP_USUARIO", ""),
        smtp_senha=env("SMTP_SENHA", ""),
        email_remetente=env("EMAIL_REMETENTE", ""),
        email_operacao=_lista(env("EMAIL_DESTINO_OPERACAO", "")),
        email_ti=_lista(env("EMAIL_DESTINO_TI", "")),
    )
