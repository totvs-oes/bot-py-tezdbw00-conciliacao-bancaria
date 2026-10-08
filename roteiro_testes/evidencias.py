"""Evidências: imagem (PNG) de texto de console/relatório e seleção dos prints que o robô tira no Protheus."""
from __future__ import annotations

import html
import re
import shutil
import socket
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional

MAX_LINHAS = 70


@dataclass
class Evidencia:
    arquivo: str          # nome do arquivo na pasta de evidências
    descricao: str        # o que mostra (vai para a aba Evidências)


def _html(titulo: str, legenda: str, texto: str) -> str:
    linhas = texto.rstrip("\n").splitlines()
    if len(linhas) > MAX_LINHAS:
        omitidas = len(linhas) - MAX_LINHAS
        linhas = linhas[:15] + [f"... ({omitidas} linhas omitidas; texto completo no .txt) ..."] + linhas[-(MAX_LINHAS - 16):]
    quando = datetime.now().strftime("%d/%m/%Y %H:%M:%S")
    return f"""<!doctype html><html><head><meta charset="utf-8"><style>
body {{ margin:0; background:#f3f5f4; font-family: Segoe UI, Arial, sans-serif; }}
.cab {{ background:#0b5d4b; color:#fff; padding:12px 18px; }}
.cab b {{ font-size:16px; }} .cab small {{ display:block; opacity:.85; margin-top:3px; font-size:12px; }}
pre {{ margin:0; background:#101820; color:#d7ece7; padding:14px 18px; font: 12.5px/1.45 Consolas, monospace;
       white-space: pre-wrap; word-break: break-word; }}
.leg {{ color:#8fe3c9; }}
</style></head><body>
<div class="cab"><b>{html.escape(titulo)}</b>
<small>{quando} · {html.escape(socket.gethostname())} · RPA Conciliação Bancária AEROFLEX</small></div>
<pre><span class="leg">{html.escape(legenda)}</span>
{html.escape(chr(10).join(linhas))}</pre></body></html>"""


class Renderizador:
    """Gera PNGs com o Chrome headless (o mesmo navegador do robô). Abre uma vez e reaproveita."""

    def __init__(self, navegador: str = "chrome"):
        self._navegador_canal = navegador
        self._playwright = self._navegador = None

    def __enter__(self) -> "Renderizador":
        from playwright.sync_api import sync_playwright
        self._playwright = sync_playwright().start()
        self._navegador = self._playwright.chromium.launch(channel=self._navegador_canal, headless=True)
        return self

    def __exit__(self, *_) -> None:
        if self._navegador:
            self._navegador.close()
        if self._playwright:
            self._playwright.stop()

    def texto(self, destino: Path, titulo: str, legenda: str, texto: str) -> Path:
        """PNG estilo terminal + o mesmo conteúdo em .txt ao lado (texto completo, sem corte)."""
        destino.with_suffix(".txt").write_text(f"{legenda}\n{texto}", encoding="utf-8")
        pagina = self._navegador.new_page(viewport={"width": 1280, "height": 400})
        try:
            pagina.set_content(_html(titulo, legenda, texto))
            pagina.screenshot(path=str(destino), full_page=True)
        finally:
            pagina.close()
        return destino


# ---------------------------------------------------------------------------------------------------------------------
# Prints do robô (saida/<data>/prints ou prints_ensaio)
# ---------------------------------------------------------------------------------------------------------------------
LOGIN = [("01_tela_login.png", "Tela de login do WebApp"), ("02_tela_pos_login.png", "Tela de Grupo/Filial/Ambiente"),
         ("aviso_protheus_1.png", "Aviso pós-login fechado pelo robô"), ("03_login_ok.png", "Tela inicial após o login"),
         ("dialogo_moedas.png", "Diálogo de cotações (Moedas) cancelado pelo robô")]
RE_LANCAMENTO = re.compile(r"^(transf|tarifa|rend)_(\d{3})_(\d+)_(preenchido|gravado)\.png$")
TIPOS = {"transf": "Transferência", "tarifa": "Tarifa", "rend": "Rendimento"}
BANCOS = {"001": "BB", "033": "Santander", "104": "Caixa", "237": "Bradesco", "246": "ABC", "341": "Itaú",
          "422": "Safra", "707": "Daycoval"}


@dataclass
class Selecao:
    login: list[tuple[Path, str]]
    lancamentos: list[tuple[Path, str]]
    falhas: list[tuple[Path, str]]
    conciliacao: list[tuple[Path, str]]


def selecionar_prints(pasta: Path, desde: datetime, maximo_falhas: int = 3, maximo_saldos: int = 2) -> Selecao:
    """Escolhe os prints desta execução (modificados depois de `desde`): login, 1 lançamento por tipo e banco
    (preenchido + gravado), lançamento contábil, recusas/falhas e a conciliação."""
    if not pasta.is_dir():
        return Selecao([], [], [], [])
    recentes = sorted(p for p in pasta.glob("*.png") if datetime.fromtimestamp(p.stat().st_mtime) >= desde)
    por_nome = {p.name: p for p in recentes}

    login = [(por_nome[n], d) for n, d in LOGIN if n in por_nome]

    primeiro_doc: dict[tuple[str, str], str] = {}   # (tipo, banco) -> 1º documento desta execução
    sequencia = lambda doc: (len(doc), doc)         # noqa: E731 - 150926 < 1509261 < 15092610
    for p in recentes:
        if m := RE_LANCAMENTO.match(p.name):
            chave = (m.group(1), m.group(2))
            if chave not in primeiro_doc or sequencia(m.group(3)) < sequencia(primeiro_doc[chave]):
                primeiro_doc[chave] = m.group(3)
    lancamentos = []
    for (tipo, banco), doc in primeiro_doc.items():
        for fase in ("preenchido", "gravado"):
            if (p := por_nome.get(f"{tipo}_{banco}_{doc}_{fase}.png")):
                lancamentos.append((p, f"{TIPOS[tipo]} {BANCOS.get(banco, banco)} (doc {doc}) {fase}"))
    if "lancamento_contabil.png" in por_nome:
        lancamentos.append((por_nome["lancamento_contabil.png"], "Lançamento contábil gerado na gravação"))

    falhas = []
    if "mensagem_protheus.png" in por_nome:
        falhas.append((por_nome["mensagem_protheus.png"], "Mensagem/recusa do Protheus tratada pelo robô"))
    for p in recentes:
        if len(falhas) >= maximo_falhas:
            break
        if p.name.startswith(("nao_encontrado_", "erro_", "foco_perdido", "campos_divergentes")):
            falhas.append((p, f"Diagnóstico de falha: {p.stem.replace('_', ' ')}"))

    conciliacao = [(p, f"Conciliador: {p.stem.removeprefix('conciliador_').replace('_', ' ')}")
                   for p in recentes if p.name.startswith("conciliador_") and p.stem.endswith(("selecionados", "aplicado"))]
    saldos = [p for p in recentes if p.name.startswith("conciliador_") and p.stem.endswith("saldos")]
    conciliacao += [(p, f"Conciliador: {p.stem.removeprefix('conciliador_').replace('_', ' ')}") for p in saldos[:maximo_saldos]]
    return Selecao(login, lancamentos, falhas, conciliacao)


def copiar(origens: list[tuple[Path, str]], pasta: Path, codigo: str, inicio: int = 1) -> list[Evidencia]:
    """Copia os prints para a pasta de evidências com o código do roteiro na frente do nome."""
    copiadas = []
    for i, (origem, descricao) in enumerate(origens, start=inicio):
        nome = f"{codigo}_{i:02d}_{re.sub(r'^\d+_', '', origem.name)}"   # "01_tela_login.png" -> "0035_01_tela_login.png"
        shutil.copy2(origem, pasta / nome)
        copiadas.append(Evidencia(nome, descricao))
    return copiadas


def recorte(texto: str, inicio: str, fim: Optional[str] = None) -> str:
    """Trecho do relatório.md entre dois títulos (ex.: "## Pendências" até "## Fora")."""
    if inicio not in texto:
        return ""
    trecho = texto.split(inicio, 1)[1]
    if fim and fim in trecho:
        trecho = trecho.split(fim, 1)[0]
    return inicio + trecho
