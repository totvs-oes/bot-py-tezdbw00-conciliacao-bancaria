"""Execução de um dia de movimento, usada pelo CLI e pela API.

    extratos (pasta Z: ou SFTP do cliente -> API de extratos, ou JSON já salvo) -> plano -> Protheus -> relatório -> e-mail
"""
from __future__ import annotations

import json
import logging
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import date
from enum import Enum
from pathlib import Path, PurePosixPath
from typing import Iterator, Optional

from conciliacao import notificacao, relatorio
from conciliacao.configuracao import Ambiente, Cadastro
from conciliacao.extratos import cliente_api
from conciliacao.extratos.leitura import ler_resposta_api
from conciliacao.extratos.pastas import LOCAL, dia_util_anterior, dia_util_seguinte, listar_extratos, pasta_do_dia
from conciliacao.modelos import Plano, Rotina
from conciliacao.planejamento import planejar
from conciliacao.registro import Registro
from conciliacao.relatorio import ResultadoExecucao

log = logging.getLogger("conciliacao")


class Modo(str, Enum):
    PLANEJAR = "planejar"   # só plano e relatório, sem tocar no Protheus
    ENSAIO = "ensaio"       # percorre as telas preenchendo e conferindo, mas CANCELA em vez de gravar
    EXECUTAR = "executar"   # grava no Protheus e concilia


class Desfecho(str, Enum):
    OK = "ok"                            # tudo lançado e conciliado
    COM_PENDENCIAS = "com_pendencias"    # rodou, mas há pendência ou conta não conciliada (ação humana)
    FALHA = "falha"                      # falha técnica: interrompido depois da nova tentativa


@dataclass
class Pedido:
    data_movimento: Optional[date] = None        # padrão: dia útil anterior a hoje
    modo: Modo = Modo.EXECUTAR
    rotinas: Optional[set[Rotina]] = None        # padrão: todas
    limite: Optional[int] = None                 # no máximo N lançamentos
    data_pasta: Optional[date] = None            # padrão: dia útil seguinte ao movimento
    pasta: Optional[Path] = None                 # pasta exata com os PDFs (ignora a estrutura de Z:)
    extratos_json: Optional[Path] = None         # resposta da API de extratos já salva


@dataclass
class ResultadoDia:
    data_movimento: date
    modo: Modo
    desfecho: Desfecho
    relatorio: Path
    itens: int = 0
    pendencias: int = 0
    status_itens: dict[str, int] = field(default_factory=dict)          # status -> quantidade
    conciliacoes: dict[str, dict] = field(default_factory=dict)         # conta -> {status, detalhe}
    erro: Optional[str] = None

    @property
    def codigo_saida(self) -> int:
        return {Desfecho.OK: 0, Desfecho.COM_PENDENCIAS: 1, Desfecho.FALHA: 2}[self.desfecho]


@contextmanager
def log_em_arquivo(pasta: Path) -> Iterator[None]:
    """Grava o log da execução em <pasta>/execucao.log (somado às saídas que já existirem)."""
    pasta.mkdir(parents=True, exist_ok=True)
    arquivo = logging.FileHandler(pasta / "execucao.log", encoding="utf-8")
    arquivo.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    raiz = logging.getLogger("conciliacao")
    raiz.addHandler(arquivo)
    raiz.setLevel(logging.INFO)
    try:
        yield
    finally:
        raiz.removeHandler(arquivo)
        arquivo.close()


def data_do_movimento(pedido: Pedido, ambiente: Ambiente) -> date:
    return pedido.data_movimento or dia_util_anterior(date.today(), ambiente.feriados)


def _obter_resposta_api(pedido: Pedido, ambiente: Ambiente, cadastro: Cadastro, data_movimento: date,
                        pasta_saida: Path) -> dict:
    if pedido.extratos_json:
        return json.loads(Path(pedido.extratos_json).read_text(encoding="utf-8"))

    # SFTP: mesma estrutura de pastas, mas os caminhos são relativos à SFTP_PASTA_BASE da API de extratos
    sftp = ambiente.fonte_extratos == "sftp"
    fonte = cliente_api.FonteSftp(ambiente.api_url, ambiente.api_token) if sftp else LOCAL
    raiz = PurePosixPath(".") if sftp else ambiente.pasta_extratos
    try:
        if pedido.pasta:
            pasta_dia = PurePosixPath(Path(pedido.pasta).as_posix()) if sftp else Path(pedido.pasta)
            pasta_movimento = None
        else:
            data_pasta = pedido.data_pasta or dia_util_seguinte(data_movimento, ambiente.feriados)
            pasta_dia = pasta_do_dia(raiz, data_pasta, fonte)
            pasta_movimento = pasta_do_dia(raiz, data_movimento, fonte)
        arquivos = listar_extratos(pasta_dia, pasta_movimento, cadastro, fonte)
    finally:
        if sftp:
            fonte.close()
    onde = f"{pasta_dia} (SFTP do cliente)" if sftp else str(pasta_dia)
    if not arquivos:
        raise FileNotFoundError(f"Nenhum PDF encontrado em {onde}")
    log.info("%d extrato(s) em %s", len(arquivos), onde)

    if sftp:
        resposta = cliente_api.transcrever_sftp(arquivos, ambiente.api_url, ambiente.api_token)
    else:
        resposta = cliente_api.transcrever(arquivos, ambiente.api_url, ambiente.api_token)
    # Guarda a resposta: permite reprocessar/depurar sem chamar a API de novo (--extratos-json)
    (pasta_saida / "extratos_api.json").write_text(json.dumps(resposta, ensure_ascii=False, indent=1), encoding="utf-8")
    return resposta


def montar_plano(pedido: Pedido, ambiente: Ambiente, cadastro: Cadastro) -> tuple[Plano, Path]:
    data_movimento = data_do_movimento(pedido, ambiente)
    pasta_saida = ambiente.pasta_saida / data_movimento.isoformat()
    pasta_saida.mkdir(parents=True, exist_ok=True)
    log.info("Movimento de %s", data_movimento.strftime("%d/%m/%Y"))
    resposta = _obter_resposta_api(pedido, ambiente, cadastro, data_movimento, pasta_saida)
    extratos = ler_resposta_api(resposta, cadastro)
    return planejar(extratos, cadastro, data_movimento, ambiente.feriados), pasta_saida


def executar_dia(pedido: Pedido, ambiente: Ambiente, cadastro: Cadastro) -> ResultadoDia:
    """Roda o dia inteiro conforme o modo. Falha técnica do Protheus não levanta exceção: vira Desfecho.FALHA
    (o Executor já esperou e tentou de novo). Erros antes do Protheus (pasta vazia, API fora) levantam."""
    with log_em_arquivo(ambiente.pasta_saida / data_do_movimento(pedido, ambiente).isoformat()):
        plano, pasta_saida = montar_plano(pedido, ambiente, cadastro)
        registro = Registro(ambiente.pasta_saida / "controle.sqlite")

        if pedido.modo == Modo.PLANEJAR:
            with registro.simulacao():  # documentos provisórios, sem gravar nada
                registro.registrar_plano(plano)
                caminho = relatorio.salvar(plano, pasta_saida, simulado=True)
            log.info("Plano: %d itens, %d conciliações, %d pendências -> %s",
                     len(plano.itens()), len(plano.conciliacoes), len(plano.pendencias), caminho)
            desfecho = Desfecho.COM_PENDENCIAS if plano.pendencias else Desfecho.OK
            return _resultado(plano, pedido.modo, desfecho, caminho, ResultadoExecucao())

        from conciliacao.executor import Executor  # só carrega Playwright quando vai usar

        resultado = Executor(plano, cadastro, ambiente, registro, pasta_saida,
                             pedido.rotinas or set(Rotina), ensaio=pedido.modo == Modo.ENSAIO,
                             limite=pedido.limite).executar()
        caminho = relatorio.salvar(plano, pasta_saida, resultado, simulado=False)

        dia = plano.data_movimento.strftime("%d/%m/%Y")
        nao_conciliadas = [c for c, (status, _) in resultado.conciliacoes.items() if status != "conciliado"]
        if resultado.erro_geral:
            notificacao.enviar(ambiente, ambiente.email_ti + ambiente.email_operacao,
                               f"[Conciliação] FALHA na execução de {dia}", resultado.erro_geral, caminho)
            desfecho = Desfecho.FALHA
        else:
            assunto = (f"[Conciliação] {dia}: {len(plano.pendencias)} pendência(s), "
                       f"{len(nao_conciliadas)} conta(s) não conciliada(s)")
            notificacao.enviar(ambiente, ambiente.email_operacao, assunto, caminho.read_text(encoding="utf-8"), caminho)
            desfecho = Desfecho.COM_PENDENCIAS if plano.pendencias or nao_conciliadas else Desfecho.OK
        return _resultado(plano, pedido.modo, desfecho, caminho, resultado)


def _resultado(plano: Plano, modo: Modo, desfecho: Desfecho, caminho: Path,
               resultado: ResultadoExecucao) -> ResultadoDia:
    contagem: dict[str, int] = {}
    for status in resultado.status_itens.values():
        contagem[status] = contagem.get(status, 0) + 1
    return ResultadoDia(
        data_movimento=plano.data_movimento, modo=modo, desfecho=desfecho, relatorio=caminho,
        itens=len(plano.itens()), pendencias=len(plano.pendencias), status_itens=contagem,
        conciliacoes={conta: {"status": status, "detalhe": detalhe}
                      for conta, (status, detalhe) in resultado.conciliacoes.items()},
        erro=resultado.erro_geral,
    )
