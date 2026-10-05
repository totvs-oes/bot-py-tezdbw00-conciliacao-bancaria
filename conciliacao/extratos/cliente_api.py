"""Cliente da API de extratos (repo svc-py-tezdbw00-leitura-extratos)."""
from __future__ import annotations

import logging
from pathlib import Path, PurePath
from typing import Optional

import httpx

log = logging.getLogger(__name__)

TIMEOUT = httpx.Timeout(connect=30, read=600, write=120, pool=30)  # o fallback com IA pode demorar


class ErroApiExtratos(Exception):
    pass


def transcrever(arquivos: list[Path], url: str, token: str) -> dict[str, list[dict]]:
    """Envia os PDFs um a um (um arquivo ruim não derruba os outros) e junta as respostas.

    Retorna o mesmo formato da API: {"banco_0237": [ExtratoConta, ...], ...}.
    Arquivo rejeitado (400/413) entra como extrato com metodo "nenhum" e aviso.
    Falha de conexão/autenticação/servidor levanta ErroApiExtratos (falha técnica).
    """
    if not token:
        raise ErroApiExtratos("API_EXTRATOS_TOKEN não configurado no .env")

    resultado: dict[str, list[dict]] = {}
    with httpx.Client(timeout=TIMEOUT, headers={"Authorization": f"Bearer {token}"}) as cliente:
        for arquivo in arquivos:
            log.info("Enviando %s para a API de extratos", arquivo.name)
            try:
                with open(arquivo, "rb") as f:
                    resposta = cliente.post(url, files={"arquivos": (arquivo.name, f, "application/pdf")})
            except httpx.HTTPError as erro:
                raise ErroApiExtratos(f"Sem conexão com a API de extratos ({erro})") from erro

            if resposta.status_code in (400, 413):
                resultado.setdefault("banco_desconhecido", []).append(_rejeitado(arquivo.name, resposta))
                continue
            if resposta.status_code >= 300:
                raise ErroApiExtratos(f"{arquivo.name}: HTTP {resposta.status_code} {resposta.text[:300]}")

            for chave, extratos in resposta.json().items():
                resultado.setdefault(chave, []).extend(extratos)
    return resultado


def _rejeitado(nome: str, resposta: httpx.Response) -> dict:
    return {
        "arquivo": nome, "metodo": "nenhum", "conta": None, "lancamentos": [],
        "aviso": f"Rejeitado pela API (HTTP {resposta.status_code}): {resposta.text[:300]}",
        "conferencia": {"saldo_anterior": None, "saldo_final": None, "ok": None},
    }


# ---------------------------------------------------------------------------
# SFTP: a API de extratos busca os PDFs no servidor do cliente (FONTE_EXTRATOS=sftp)
# ---------------------------------------------------------------------------

def url_base(url_extratos: str) -> str:
    """API_EXTRATOS_URL aponta para POST /extratos; as rotas de SFTP ficam na mesma API."""
    url = url_extratos.rstrip("/")
    return url[: -len("/extratos")] if url.endswith("/extratos") else url


def _cliente(url: str, token: str, transporte: Optional[httpx.BaseTransport]) -> httpx.Client:
    if not token:
        raise ErroApiExtratos("API_EXTRATOS_TOKEN não configurado no .env")
    return httpx.Client(base_url=url_base(url), timeout=TIMEOUT, transport=transporte,
                        headers={"Authorization": f"Bearer {token}"})


def _caminho(pasta: PurePath) -> str:
    return pasta.as_posix().replace("\\", "/")


class FonteSftp:
    """Fonte de extratos (ver pastas.Fonte) que lista as pastas do SFTP do cliente pela API de extratos.

    Caminhos são relativos à SFTP_PASTA_BASE configurada na API. Cada pasta é listada uma vez só (cache).
    """

    def __init__(self, url: str, token: str, transporte: Optional[httpx.BaseTransport] = None):
        self._http = _cliente(url, token, transporte)
        self._cache: dict[str, Optional[dict]] = {}

    def __enter__(self) -> "FonteSftp":
        return self

    def __exit__(self, *_) -> None:
        self.close()

    def close(self) -> None:
        self._http.close()

    def _listar(self, pasta: PurePath) -> Optional[dict]:
        caminho = _caminho(pasta)
        if caminho not in self._cache:
            try:
                resposta = self._http.get("/sftp/listar", params={"caminho": caminho})
            except httpx.HTTPError as erro:
                raise ErroApiExtratos(f"Sem conexão com a API de extratos ({erro})") from erro
            if resposta.status_code == 404:
                self._cache[caminho] = None
            elif resposta.status_code >= 300:
                raise ErroApiExtratos(f"Listagem SFTP de '{caminho}': HTTP {resposta.status_code} {resposta.text[:300]}")
            else:
                self._cache[caminho] = resposta.json()
        return self._cache[caminho]

    def subpastas(self, pasta: PurePath) -> Optional[list[str]]:
        listagem = self._listar(pasta)
        return None if listagem is None else listagem["pastas"]

    def pdfs(self, pasta: PurePath) -> list[str]:
        listagem = self._listar(pasta)
        return [] if listagem is None else listagem["arquivos"]


def transcrever_sftp(arquivos: list[PurePath], url: str, token: str,
                     transporte: Optional[httpx.BaseTransport] = None) -> dict[str, list[dict]]:
    """Pede à API que baixe do SFTP e leia cada PDF (um por chamada, como no envio local).

    Arquivo com problema volta da API como extrato com metodo "nenhum" e aviso.
    Falha do SFTP (502), API sem SFTP configurado (503) ou sem conexão levantam ErroApiExtratos.
    """
    resultado: dict[str, list[dict]] = {}
    with _cliente(url, token, transporte) as cliente:
        for arquivo in arquivos:
            caminho = _caminho(arquivo)
            log.info("Lendo %s do SFTP pela API de extratos", caminho)
            try:
                resposta = cliente.post("/extratos/sftp", json={"arquivos": [caminho]})
            except httpx.HTTPError as erro:
                raise ErroApiExtratos(f"Sem conexão com a API de extratos ({erro})") from erro
            if resposta.status_code >= 300:
                raise ErroApiExtratos(f"{caminho}: HTTP {resposta.status_code} {resposta.text[:300]}")
            for chave, extratos in resposta.json().items():
                resultado.setdefault(chave, []).extend(extratos)
    return resultado
