"""Cliente da API de extratos (repo aeroflex-conciliacao-bancaria-api)."""
from __future__ import annotations

import logging
from pathlib import Path

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
