"""Login contra uma cópia simulada do WebApp (estrutura tirada do diagnóstico real de 02/10/2026).

Reproduz o que quebrou o robô duas vezes: o iframe do login PO-UI é criado DEPOIS da carga, dentro do
shadow DOM de um wa-webview, com ids "po-login[<uuid>]", e é destruído após o Enter.
Precisa do Chrome instalado; é pulado se não houver.
"""
import functools
import threading
from datetime import date
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from conciliacao.configuracao import carregar_ambiente
from conciliacao.protheus.sessao import SessaoProtheus
from conciliacao.protheus.tela import ErroDeTela

WEBAPP = Path(__file__).parent / "fixtures" / "webapp_simulado"


class _Silencioso(SimpleHTTPRequestHandler):
    def log_message(self, *args):
        pass


@pytest.fixture
def servidor():
    http = ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(_Silencioso, directory=str(WEBAPP)))
    threading.Thread(target=http.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{http.server_port}"
    http.shutdown()


@pytest.fixture
def ambiente(servidor):
    amb = carregar_ambiente()
    amb.protheus_url = f"{servidor}/webapp.html"
    amb.protheus_usuario, amb.protheus_senha = "sp01\\robo", "segredo"
    amb.protheus_visivel = False
    amb.protheus_navegador = "chrome"
    return amb


def _sessao(ambiente, tmp_path):
    try:
        return SessaoProtheus(ambiente, date(2026, 9, 9), "010101", tmp_path / "prints").__enter__()
    except Exception as erro:
        if "Executable doesn't exist" in str(erro) or "is not found" in str(erro):
            pytest.skip("Chrome não instalado")
        raise


def test_login_completo(ambiente, tmp_path):
    sessao = _sessao(ambiente, tmp_path)
    try:
        # O botão da tela 2 grava no título o que recebeu
        assert sessao.tela.page.title() == "ENTROU 09/09/2026 sp01\\robo senha:7"
    finally:
        sessao.__exit__()


def test_login_falha_com_diagnostico(ambiente, tmp_path, servidor, monkeypatch):
    monkeypatch.setattr("conciliacao.protheus.sessao.TIMEOUT_CARGA_INICIAL", 3_000)
    ambiente.protheus_url = f"{servidor}/nao-existe.html"
    with pytest.raises(ErroDeTela, match="campo_usuario"):
        with SessaoProtheus(ambiente, date(2026, 9, 9), "010101", tmp_path / "prints"):
            pass
    assert (tmp_path / "prints" / "nao_encontrado_campo_usuario.json").exists()
