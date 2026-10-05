"""FONTE_EXTRATOS=sftp: o robô lista e lê os PDFs pela API de extratos (que acessa o SFTP do cliente)."""
import json
from datetime import date
from pathlib import PurePosixPath

import httpx
import pytest

from conciliacao.extratos import cliente_api
from conciliacao.extratos.cliente_api import ErroApiExtratos, FonteSftp, transcrever_sftp, url_base
from conciliacao.extratos.pastas import listar_extratos, pasta_do_dia

URL = "http://api:5000/extratos"
RAIZ = PurePosixPath(".")

# Pastas no SFTP do cliente (caminho relativo à SFTP_PASTA_BASE da API -> subpastas, PDFs)
SERVIDOR = {
    ".": (["2026 AEROFLEX"], []),
    "2026 AEROFLEX": (["08 - AGOSTO", "03 - MARCO"], []),
    "2026 AEROFLEX/08 - AGOSTO": (["11-08", "12-08"], []),
    "2026 AEROFLEX/08 - AGOSTO/12-08": ([], ["ITAU 6896-9 12-08.pdf", "SAFRA 580275-7 12-08.pdf"]),
    "2026 AEROFLEX/08 - AGOSTO/11-08": ([], ["ITAU 6896-9 11-08.pdf", "SAFRA 580275-7 11-08.pdf ATUALIZADO.pdf"]),
    "2026 AEROFLEX/03 - MARCO": (["05-03"], []),
    "2026 AEROFLEX/03 - MARCO/05-03": ([], []),
}


def api_simulada(chamadas: list):
    def responder(request: httpx.Request) -> httpx.Response:
        chamadas.append((request.method, request.url.path, request.url.params.get("caminho"),
                         request.content.decode() if request.content else None))
        assert request.headers["Authorization"] == "Bearer tk"
        if request.url.path == "/sftp/listar":
            caminho = request.url.params["caminho"]
            if caminho not in SERVIDOR:
                return httpx.Response(404, json={"detail": "Pasta não encontrada"})
            pastas, arquivos = SERVIDOR[caminho]
            return httpx.Response(200, json={"caminho": caminho, "pastas": pastas, "arquivos": arquivos})
        if request.url.path == "/extratos/sftp":
            caminho = json.loads(request.content)["arquivos"][0]
            if "FALHA" in caminho:
                return httpx.Response(502, json={"detail": "Sem conexão com o SFTP"})
            nome = caminho.rsplit("/", 1)[-1]
            return httpx.Response(200, json={"banco_0341": [{"arquivo": nome, "metodo": "layout Itaú"}]})
        return httpx.Response(404)
    return httpx.MockTransport(responder)


@pytest.fixture
def chamadas():
    return []


@pytest.fixture
def fonte(chamadas):
    with FonteSftp(URL, "tk", transporte=api_simulada(chamadas)) as f:
        yield f


def test_url_base():
    assert url_base("http://api:5000/extratos") == "http://api:5000"
    assert url_base("http://api:5000/extratos/") == "http://api:5000"
    assert url_base("http://api:5000") == "http://api:5000"


def test_pasta_do_dia_no_sftp(fonte):
    assert pasta_do_dia(RAIZ, date(2026, 8, 12), fonte) == PurePosixPath("2026 AEROFLEX/08 - AGOSTO/12-08")


def test_pasta_do_dia_no_sftp_tolera_acento(fonte):
    assert pasta_do_dia(RAIZ, date(2026, 3, 5), fonte) == PurePosixPath("2026 AEROFLEX/03 - MARCO/05-03")


def test_pasta_inexistente_no_sftp(fonte):
    pasta = pasta_do_dia(RAIZ, date(2026, 9, 10), fonte)
    assert pasta == PurePosixPath("2026 AEROFLEX/09 - SETEMBRO/10-09")
    assert fonte.pdfs(pasta) == []


def test_atualizado_substitui_original_no_sftp(cadastro, fonte):
    pasta_dia = pasta_do_dia(RAIZ, date(2026, 8, 12), fonte)
    pasta_mov = pasta_do_dia(RAIZ, date(2026, 8, 11), fonte)
    arquivos = listar_extratos(pasta_dia, pasta_mov, cadastro, fonte)
    assert [a.as_posix() for a in arquivos] == [
        "2026 AEROFLEX/08 - AGOSTO/12-08/ITAU 6896-9 12-08.pdf",
        "2026 AEROFLEX/08 - AGOSTO/11-08/SAFRA 580275-7 11-08.pdf ATUALIZADO.pdf",
    ]


def test_cada_pasta_e_listada_uma_vez(cadastro, fonte, chamadas):
    pasta_dia = pasta_do_dia(RAIZ, date(2026, 8, 12), fonte)
    listar_extratos(pasta_dia, None, cadastro, fonte)
    listar_extratos(pasta_dia, None, cadastro, fonte)
    listagens = [c[2] for c in chamadas if c[1] == "/sftp/listar"]
    assert len(listagens) == len(set(listagens))


def test_transcrever_sftp_envia_caminho_relativo(chamadas):
    arquivos = [PurePosixPath("2026 AEROFLEX/08 - AGOSTO/12-08/ITAU 6896-9 12-08.pdf")]
    resposta = transcrever_sftp(arquivos, URL, "tk", transporte=api_simulada(chamadas))
    assert resposta == {"banco_0341": [{"arquivo": "ITAU 6896-9 12-08.pdf", "metodo": "layout Itaú"}]}
    assert json.loads(chamadas[0][3]) == {"arquivos": ["2026 AEROFLEX/08 - AGOSTO/12-08/ITAU 6896-9 12-08.pdf"]}


def test_falha_do_sftp_e_erro_tecnico(chamadas):
    with pytest.raises(ErroApiExtratos, match="502"):
        transcrever_sftp([PurePosixPath("FALHA.pdf")], URL, "tk", transporte=api_simulada(chamadas))


def test_sem_token():
    with pytest.raises(ErroApiExtratos, match="API_EXTRATOS_TOKEN"):
        cliente_api.FonteSftp(URL, "")
