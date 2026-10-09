"""API HTTP para um sistema externo disparar a conciliação.

    POST /execucoes                  dispara a execução de um dia (202 + id; roda em segundo plano)
    GET  /execucoes/{id}             andamento e resultado
    GET  /execucoes/{id}/relatorio   relatório do dia (markdown)
    GET  /execucoes                  últimas execuções
    GET  /saude                      sem autenticação (monitoramento)

Uma execução leva de 10 a 30 min (telas do Protheus): o POST responde na hora e o sistema externo consulta o GET.
Só UMA execução por vez — é uma única sessão no Protheus; um segundo POST durante a execução recebe 409.
Autenticação: header "Authorization: Bearer <RPA_API_TOKEN>" (mesmo padrão da API de extratos).
Subir: python -m conciliacao api [--port 5001]
"""
from __future__ import annotations

import logging
import threading
import uuid
from datetime import date, datetime
from hmac import compare_digest
from pathlib import Path
from typing import Callable, Literal, Optional

from fastapi import Depends, FastAPI, HTTPException, status
from fastapi.responses import PlainTextResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field

from conciliacao import credenciais as cofre
from conciliacao.configuracao import Ambiente, Cadastro, carregar_ambiente, carregar_cadastro
from conciliacao.modelos import Rotina
from conciliacao.servico import Desfecho, Modo, Pedido, ResultadoDia, data_do_movimento, executar_dia

log = logging.getLogger("conciliacao.api")

EM_ANDAMENTO, CONCLUIDA, FALHOU, INTERROMPIDA = "em_andamento", "concluida", "falhou", "interrompida"


# ---------------------------------------------------------------------------
# Contrato
# ---------------------------------------------------------------------------
class PedidoExecucao(BaseModel):
    data_movimento: Optional[date] = Field(
        default=None, description="Data dos lançamentos (AAAA-MM-DD). Padrão: dia útil anterior a hoje.")
    modo: Modo = Field(default=Modo.EXECUTAR, description=(
        "executar = grava no Protheus e concilia; ensaio = preenche e confere as telas mas cancela; "
        "planejar = só plano e relatório, sem abrir o Protheus."))
    rotinas: Optional[list[Rotina]] = Field(default=None, description="Padrão: todas.")
    limite: Optional[int] = Field(default=None, ge=1, description="No máximo N lançamentos nesta execução.")


class ResultadoExecucaoApi(BaseModel):
    desfecho: Desfecho = Field(description="ok | com_pendencias (ação humana: ver relatório) | falha (TI notificada)")
    itens_planejados: int
    pendencias: int
    status_itens: dict[str, int] = Field(description="Quantidade de lançamentos por status (concluido, erro, incerto...).")
    conciliacoes: dict[str, dict] = Field(description="Por conta: {status, detalhe}.")
    erro: Optional[str] = None


class Execucao(BaseModel):
    id: str
    status: Literal["em_andamento", "concluida", "falhou", "interrompida"]
    pedido: PedidoExecucao
    data_movimento: date
    inicio: datetime
    fim: Optional[datetime] = None
    resultado: Optional[ResultadoExecucaoApi] = None
    erro: Optional[str] = Field(default=None, description="Erro que impediu a execução (ex.: pasta sem extratos).")


# ---------------------------------------------------------------------------
# Execuções (memória + um JSON por execução em saida/execucoes, para sobreviver a reinício)
# ---------------------------------------------------------------------------
class Execucoes:
    def __init__(self, pasta: Path):
        self.pasta = pasta
        self.pasta.mkdir(parents=True, exist_ok=True)
        self._trava_protheus = threading.Lock()   # uma execução por vez
        self._trava_dados = threading.Lock()
        self._atual: Optional[str] = None
        self._recuperar_interrompidas()

    def _arquivo(self, id_: str) -> Path:
        return self.pasta / f"{id_}.json"

    def salvar(self, execucao: Execucao) -> None:
        with self._trava_dados:
            self._arquivo(execucao.id).write_text(execucao.model_dump_json(indent=1), encoding="utf-8")

    def obter(self, id_: str) -> Optional[Execucao]:
        if not id_.replace("-", "").isalnum():  # id vira nome de arquivo
            return None
        arquivo = self._arquivo(id_)
        return Execucao.model_validate_json(arquivo.read_text(encoding="utf-8")) if arquivo.exists() else None

    def ultimas(self, quantidade: int) -> list[Execucao]:
        arquivos = sorted(self.pasta.glob("*.json"), key=lambda a: a.stat().st_mtime, reverse=True)[:quantidade]
        return [Execucao.model_validate_json(a.read_text(encoding="utf-8")) for a in arquivos]

    def _recuperar_interrompidas(self) -> None:
        """A API caiu no meio de uma execução: ela não continua sozinha. O registro do robô (controle.sqlite)
        garante que uma nova execução não duplica o que já foi gravado."""
        for arquivo in self.pasta.glob("*.json"):
            execucao = Execucao.model_validate_json(arquivo.read_text(encoding="utf-8"))
            if execucao.status == EM_ANDAMENTO:
                execucao.status, execucao.fim = INTERROMPIDA, datetime.now()
                execucao.erro = "A API foi reiniciada durante a execução. Dispare de novo para continuar."
                self.salvar(execucao)

    def iniciar(self, execucao: Execucao, trabalho: Callable[[], None]) -> bool:
        """Começa em segundo plano. False se já há uma execução em andamento."""
        if not self._trava_protheus.acquire(blocking=False):
            return False
        self._atual = execucao.id
        self.salvar(execucao)

        def rodar():
            try:
                trabalho()
            finally:
                self._atual = None
                self._trava_protheus.release()

        # Thread comum (não a do servidor): o Playwright síncrono não roda dentro do loop asyncio
        threading.Thread(target=rodar, name=f"execucao-{execucao.id}", daemon=True).start()
        return True

    @property
    def atual(self) -> Optional[str]:
        return self._atual


# ---------------------------------------------------------------------------
# Aplicação
# ---------------------------------------------------------------------------
def _resultado_api(resultado: ResultadoDia) -> ResultadoExecucaoApi:
    return ResultadoExecucaoApi(
        desfecho=resultado.desfecho, itens_planejados=resultado.itens, pendencias=resultado.pendencias,
        status_itens=resultado.status_itens, conciliacoes=resultado.conciliacoes, erro=resultado.erro)


def criar_app(ambiente: Optional[Ambiente] = None, cadastro: Optional[Cadastro] = None,
              executar: Callable[[Pedido, Ambiente, Cadastro], ResultadoDia] = executar_dia) -> FastAPI:
    ambiente = ambiente or carregar_ambiente()   # também carrega o .env (RPA_API_TOKEN)
    cadastro = cadastro or carregar_cadastro()
    execucoes = Execucoes(ambiente.pasta_saida / "execucoes")
    seguranca = HTTPBearer(auto_error=False)

    def verificar_token(credenciais: Optional[HTTPAuthorizationCredentials] = Depends(seguranca)) -> None:
        esperado = cofre.obter("RPA_API_TOKEN")
        if not esperado:
            raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, "RPA_API_TOKEN não configurado no .env.")
        if not credenciais or not compare_digest(credenciais.credentials, esperado):
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Token inválido ou não fornecido.",
                                headers={"WWW-Authenticate": "Bearer"})

    app = FastAPI(title="RPA Conciliação Bancária AEROFLEX", version="1.0",
                  description=__doc__)

    @app.get("/saude")
    def saude() -> dict:
        return {"status": "ok", "execucao_em_andamento": execucoes.atual}

    @app.post("/execucoes", status_code=status.HTTP_202_ACCEPTED, response_model=Execucao,
              dependencies=[Depends(verificar_token)],
              responses={409: {"description": "Já existe uma execução em andamento."}})
    def disparar(corpo: PedidoExecucao) -> Execucao:
        pedido = Pedido(data_movimento=corpo.data_movimento, modo=corpo.modo,
                        rotinas=set(corpo.rotinas) if corpo.rotinas else None, limite=corpo.limite)
        # Data padrão resolvida AGORA: a execução roda a data informada na resposta, mesmo se virar o dia
        pedido.data_movimento = data_do_movimento(pedido, ambiente)
        execucao = Execucao(id=uuid.uuid4().hex[:12], status=EM_ANDAMENTO, pedido=corpo,
                            data_movimento=pedido.data_movimento, inicio=datetime.now())

        def trabalho() -> None:
            log.info("Execução %s: %s", execucao.id, corpo.model_dump_json())
            try:
                resultado = executar(pedido, ambiente, cadastro)
                execucao.resultado = _resultado_api(resultado)
                execucao.status = FALHOU if resultado.desfecho == Desfecho.FALHA else CONCLUIDA
            except Exception as erro:  # noqa: BLE001 — qualquer erro precisa chegar ao sistema externo
                log.exception("Execução %s falhou", execucao.id)
                execucao.status, execucao.erro = FALHOU, f"{type(erro).__name__}: {erro}"
            execucao.fim = datetime.now()
            execucoes.salvar(execucao)

        if not execucoes.iniciar(execucao, trabalho):
            raise HTTPException(status.HTTP_409_CONFLICT,
                                f"Já existe uma execução em andamento ({execucoes.atual}). Aguarde terminar.")
        return execucao

    @app.get("/execucoes", response_model=list[Execucao], dependencies=[Depends(verificar_token)])
    def listar(quantidade: int = 20) -> list[Execucao]:
        return execucoes.ultimas(max(1, min(quantidade, 100)))

    @app.get("/execucoes/{id_}", response_model=Execucao, dependencies=[Depends(verificar_token)])
    def consultar(id_: str) -> Execucao:
        if not (execucao := execucoes.obter(id_)):
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Execução não encontrada.")
        return execucao

    @app.get("/execucoes/{id_}/relatorio", response_class=PlainTextResponse,
             dependencies=[Depends(verificar_token)])
    def relatorio(id_: str) -> str:
        if not (execucao := execucoes.obter(id_)):
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Execução não encontrada.")
        caminho = ambiente.pasta_saida / execucao.data_movimento.isoformat() / "relatorio.md"
        if execucao.status == EM_ANDAMENTO or not caminho.exists():
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Relatório ainda não gerado.")
        return caminho.read_text(encoding="utf-8")

    return app


def __getattr__(nome: str):
    # "conciliacao.api:app" para o uvicorn: só monta a aplicação (lê .env e YAMLs) quando for usada
    if nome == "app":
        return criar_app()
    raise AttributeError(nome)
