import dataclasses
import threading
import time
from datetime import date, datetime

import pytest
from fastapi.testclient import TestClient

from conciliacao.api import EM_ANDAMENTO, Execucao, Execucoes, PedidoExecucao, criar_app
from conciliacao.configuracao import carregar_ambiente
from conciliacao.servico import Desfecho, Modo, Pedido, ResultadoDia, executar_dia
from tests.conftest import DIA, FIXTURES

TOKEN = "token-de-teste"
CABECALHO = {"Authorization": f"Bearer {TOKEN}"}


@pytest.fixture
def ambiente(tmp_path, monkeypatch):
    monkeypatch.setenv("RPA_API_TOKEN", TOKEN)
    return dataclasses.replace(carregar_ambiente(), pasta_saida=tmp_path)


class ExecutorFalso:
    """Simula o robô: registra os pedidos e só termina quando o teste liberar."""

    def __init__(self, desfecho=Desfecho.OK, erro: Exception | None = None):
        self.pedidos: list[Pedido] = []
        self.liberar = threading.Event()
        self.desfecho, self.erro = desfecho, erro

    def __call__(self, pedido, ambiente, cadastro) -> ResultadoDia:
        self.pedidos.append(pedido)
        self.liberar.wait(5)
        if self.erro:
            raise self.erro
        pasta = ambiente.pasta_saida / pedido.data_movimento.isoformat()
        pasta.mkdir(parents=True, exist_ok=True)
        (relatorio := pasta / "relatorio.md").write_text("# Relatório de teste", encoding="utf-8")
        return ResultadoDia(data_movimento=pedido.data_movimento, modo=pedido.modo, desfecho=self.desfecho,
                            relatorio=relatorio, itens=3, pendencias=1, status_itens={"concluido": 3},
                            conciliacoes={"itau_cc": {"status": "conciliado", "detalhe": None}},
                            erro="Falha técnica" if self.desfecho == Desfecho.FALHA else None)


def _cliente(ambiente, cadastro, executor):
    return TestClient(criar_app(ambiente, cadastro, executar=executor))


def _esperar_fim(cliente, id_):
    for _ in range(100):
        corpo = cliente.get(f"/execucoes/{id_}", headers=CABECALHO).json()
        if corpo["status"] != EM_ANDAMENTO:
            return corpo
        time.sleep(0.05)
    raise AssertionError("execução não terminou")


def test_sem_token_ou_token_errado_recusa(ambiente, cadastro):
    cliente = _cliente(ambiente, cadastro, ExecutorFalso())
    assert cliente.post("/execucoes", json={}).status_code == 401
    assert cliente.post("/execucoes", json={}, headers={"Authorization": "Bearer errado"}).status_code == 401
    assert cliente.get("/saude").status_code == 200  # monitoramento não exige token


def test_token_nao_configurado(ambiente, cadastro, monkeypatch):
    monkeypatch.delenv("RPA_API_TOKEN")
    assert _cliente(ambiente, cadastro, ExecutorFalso()).post("/execucoes", json={}).status_code == 500


def test_dispara_em_segundo_plano_e_consulta_resultado(ambiente, cadastro):
    executor = ExecutorFalso()
    cliente = _cliente(ambiente, cadastro, executor)

    resposta = cliente.post("/execucoes", headers=CABECALHO, json={
        "data_movimento": "2026-09-09", "modo": "ensaio", "rotinas": ["tarifa", "conciliacao"], "limite": 2})
    assert resposta.status_code == 202
    corpo = resposta.json()
    assert corpo["status"] == "em_andamento" and corpo["data_movimento"] == "2026-09-09"

    executor.liberar.set()
    final = _esperar_fim(cliente, corpo["id"])
    assert final["status"] == "concluida"
    assert final["resultado"]["desfecho"] == "ok" and final["resultado"]["status_itens"] == {"concluido": 3}
    pedido = executor.pedidos[0]
    assert (pedido.data_movimento, pedido.modo, pedido.limite) == (DIA, Modo.ENSAIO, 2)
    assert {r.value for r in pedido.rotinas} == {"tarifa", "conciliacao"}

    relatorio = cliente.get(f"/execucoes/{corpo['id']}/relatorio", headers=CABECALHO)
    assert relatorio.status_code == 200 and relatorio.text == "# Relatório de teste"
    assert [e["id"] for e in cliente.get("/execucoes", headers=CABECALHO).json()] == [corpo["id"]]


def test_segunda_execucao_simultanea_recebe_409(ambiente, cadastro):
    executor = ExecutorFalso()
    cliente = _cliente(ambiente, cadastro, executor)
    primeira = cliente.post("/execucoes", headers=CABECALHO, json={"data_movimento": "2026-09-09"}).json()

    segunda = cliente.post("/execucoes", headers=CABECALHO, json={"data_movimento": "2026-09-10"})
    assert segunda.status_code == 409 and primeira["id"] in segunda.json()["detail"]

    executor.liberar.set()
    _esperar_fim(cliente, primeira["id"])
    # Terminada a primeira, libera a próxima
    assert cliente.post("/execucoes", headers=CABECALHO, json={"data_movimento": "2026-09-10"}).status_code == 202
    executor.liberar.set()


def test_falha_tecnica_e_erro_inesperado_viram_falhou(ambiente, cadastro):
    executor = ExecutorFalso(desfecho=Desfecho.FALHA)
    executor.liberar.set()
    cliente = _cliente(ambiente, cadastro, executor)
    final = _esperar_fim(cliente, cliente.post("/execucoes", headers=CABECALHO, json={}).json()["id"])
    assert final["status"] == "falhou" and final["resultado"]["erro"] == "Falha técnica"

    executor = ExecutorFalso(erro=FileNotFoundError("Nenhum PDF encontrado em Z:\\..."))
    executor.liberar.set()
    cliente = _cliente(ambiente, cadastro, executor)
    final = _esperar_fim(cliente, cliente.post("/execucoes", headers=CABECALHO, json={}).json()["id"])
    assert final["status"] == "falhou" and final["erro"].startswith("FileNotFoundError: Nenhum PDF")


def test_pedido_invalido_recebe_422(ambiente, cadastro):
    cliente = _cliente(ambiente, cadastro, ExecutorFalso())
    for corpo in ({"rotinas": ["inexistente"]}, {"modo": "gravar_tudo"}, {"limite": 0}, {"data_movimento": "09/09"}):
        assert cliente.post("/execucoes", headers=CABECALHO, json=corpo).status_code == 422, corpo


def test_execucao_desconhecida_404(ambiente, cadastro):
    cliente = _cliente(ambiente, cadastro, ExecutorFalso())
    assert cliente.get("/execucoes/abc123", headers=CABECALHO).status_code == 404
    assert cliente.get("/execucoes/..%2F..%2Fsegredo", headers=CABECALHO).status_code == 404


def test_execucao_em_andamento_quando_a_api_caiu_vira_interrompida(ambiente):
    pasta = ambiente.pasta_saida / "execucoes"
    Execucoes(pasta).salvar(Execucao(id="caiu1", status=EM_ANDAMENTO, pedido=PedidoExecucao(),
                                     data_movimento=DIA, inicio=datetime.now()))
    recuperada = Execucoes(pasta).obter("caiu1")
    assert recuperada.status == "interrompida" and "reiniciada" in recuperada.erro


def test_servico_modo_planejar_com_extratos_salvos(ambiente, cadastro):
    """O mesmo caminho do CLI 'planejar' e do POST com modo=planejar, sem abrir o Protheus."""
    pedido = Pedido(data_movimento=DIA, modo=Modo.PLANEJAR, extratos_json=FIXTURES / "extratos_exemplo.json")
    resultado = executar_dia(pedido, ambiente, cadastro)
    assert resultado.desfecho == Desfecho.COM_PENDENCIAS and resultado.itens == 41
    assert resultado.relatorio == ambiente.pasta_saida / "2026-09-09" / "relatorio.md"
    assert resultado.relatorio.exists() and (resultado.relatorio.parent / "execucao.log").exists()
