"""Planilha "Roteiro de Testes - MIT045": acrescenta roteiros e evidências sem mexer no que já existe.

Abas usadas (layout do modelo MIT045):
    Início      C15 data | C16 versão | C17 criado/modificado por | C18 descrição da mudança
    Cenários    B ID | C cenário | D resultado esperado | E módulo/frente | F data planejada        (dados a partir da linha 6)
    Roteiro     B código (fórmula) | C "ID - Cenário" | D ordem | E módulo (fórmula) | F processo | G subprocesso |
                H descrição | I situação esperada | J resp. TOTVS | K resp. cliente | L/M/N datas | O status |
                P aberto ocorrência? | Q/R (fórmulas) | S observações | T evidências                     (linha 6 em diante)
    Evidências  B código | C roteiro | D arquivo | E o que mostra | F data | G link no Drive         (linha 5 em diante)
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Optional

from roteiro_testes.evidencias import Evidencia
from roteiro_testes.xlsx import Aba, PastaDeTrabalho

EXITO = "Executado com êxito"
AJUSTE = "Executado com necessidade de ajuste"
ERRO = "Executado com erro"
NAO_INICIADO = "Não iniciado"

# Cenários do MIT045 do projeto (ID, nome, resultado esperado, módulo). Se a planilha não tiver o ID, ele é criado.
CENARIOS = {
    "001": ("Leitura de extratos", "A API lê os PDFs de todos os bancos e os relatórios de tarifas, confere o saldo ao "
            "centavo e só aceita chamadas com token.", "LEITURA DE EXTRATOS"),
    "002": ("Planejamento do dia", "O robô monta o plano do dia (transferências, tarifas, rendimentos e conciliações) e "
            "lista as pendências com motivo e ação.", "PLANEJAMENTO"),
    "003": ("Acesso ao Protheus", "O robô faz login sozinho, fecha os avisos e ajusta a data base para a data do movimento.",
            "PROTHEUS SIGAFIN"),
    "004": ("Lançamentos no Movimento Bancário", "Transferências, tarifas e rendimentos preenchidos corretamente e gravados "
            "com lançamento contábil.", "PROTHEUS SIGAFIN"),
    "005": ("Recusas do Protheus", "Quando o Protheus recusa um lançamento, o robô registra o motivo, descarta a tela e "
            "segue, sem gravar nada errado.", "PROTHEUS SIGAFIN"),
    "006": ("Conciliação bancária", "O robô compara o saldo do Protheus com o do extrato no Conciliador e só concilia a "
            "conta com saldo igual.", "PROTHEUS SIGAFIN"),
    "007": ("Reexecução e qualidade", "Reexecutar o dia não duplica lançamentos; os testes automatizados passam.",
            "OPERAÇÃO E INTEGRAÇÃO"),
    "008": ("Integração e notificação", "O sistema externo dispara e acompanha a execução pela API, com token; o relatório "
            "chega por e-mail.", "OPERAÇÃO E INTEGRAÇÃO"),
}
PRIMEIRA_LINHA = 6


@dataclass
class Roteiro:
    cenario: str                 # "001"
    processo: str
    subprocesso: str
    descricao: str
    esperado: str
    status: str
    observacoes: str
    evidencias: list[Evidencia] = field(default_factory=list)
    codigo: str = ""             # "0014" (cenário + ordem), atribuído pela Numeracao
    ordem: int = 0


class Numeracao:
    """Próxima ordem de roteiro por cenário, continuando a numeração que já está na planilha."""

    def __init__(self, ordens_existentes: Optional[dict[str, int]] = None):
        self._ultima = dict(ordens_existentes or {})

    @classmethod
    def da_planilha(cls, caminho: Optional[Path]) -> "Numeracao":
        if not caminho:
            return cls()
        with PastaDeTrabalho(caminho, None) as livro:   # só leitura
            return cls(_ordens_por_cenario(livro.aba("Roteiro")))

    def reservar(self, roteiro: Roteiro) -> Roteiro:
        roteiro.ordem = self._ultima.get(roteiro.cenario, 0) + 1
        self._ultima[roteiro.cenario] = roteiro.ordem
        roteiro.codigo = f"{roteiro.cenario}{roteiro.ordem}"
        return roteiro


def _ordens_por_cenario(aba: Aba) -> dict[str, int]:
    ordens: dict[str, int] = {}
    ultima = aba.ultima_linha("C", PRIMEIRA_LINHA - 1)
    for r in range(PRIMEIRA_LINHA, ultima + 1):
        cenario, ordem = (aba.texto(f"C{r}") or "")[:3], aba.texto(f"D{r}") or ""
        if cenario.isdigit() and re.fullmatch(r"\d+(\.0)?", ordem):
            ordens[cenario] = max(ordens.get(cenario, 0), int(float(ordem)))
    return ordens


# ---------------------------------------------------------------------------------------------------------------------
def gravar(origem: Path, destino: Path, roteiros: list[Roteiro], hoje: date, responsavel: str, descricao: str) -> Path:
    """Copia `origem` para `destino` acrescentando os roteiros e as evidências."""
    with PastaDeTrabalho(origem, destino) as livro:
        nomes = _garantir_cenarios(livro.aba("Cenários"), {r.cenario for r in roteiros}, hoje)
        _acrescentar_roteiros(livro.aba("Roteiro"), roteiros, nomes, hoje, responsavel)
        evidencias = livro.aba("Evidências") if livro.tem_aba("Evidências") else livro.criar_aba("Evidências", _ABA_EVIDENCIAS)
        _acrescentar_evidencias(evidencias, roteiros, hoje)
        _versao(livro.aba("Início"), hoje, responsavel, descricao)
    return destino


def _garantir_cenarios(aba: Aba, ids: set[str], hoje: date) -> dict[str, str]:
    """Nome de cada cenário como está na planilha; cria os que faltam (com o texto padrão de CENARIOS)."""
    nomes: dict[str, str] = {}
    ultima = PRIMEIRA_LINHA - 1
    for r in range(PRIMEIRA_LINHA, 400):
        id_, nome = aba.texto(f"B{r}"), aba.texto(f"C{r}")
        if id_ is None and nome is None and r > ultima + 30:
            break
        if id_ and nome:
            nomes[id_] = nome
            ultima = r
    for id_ in sorted(ids - nomes.keys()):
        nome, esperado, modulo = CENARIOS[id_]
        ultima += 1
        modelo = ultima - 1 if ultima > PRIMEIRA_LINHA else None
        aba.definir(f"B{ultima}", id_, modelo and f"B{modelo}")
        aba.definir(f"C{ultima}", nome, modelo and f"C{modelo}", quebra=True)
        aba.definir(f"D{ultima}", esperado, modelo and f"D{modelo}", quebra=True)
        aba.definir(f"E{ultima}", modulo, modelo and f"E{modelo}", quebra=True)
        aba.definir(f"F{ultima}", hoje, modelo and f"F{modelo}")
        nomes[id_] = nome
    return nomes


def _acrescentar_roteiros(aba: Aba, roteiros: list[Roteiro], nomes: dict[str, str], hoje: date,
                          responsavel: str) -> None:
    ultima = aba.ultima_linha("C", PRIMEIRA_LINHA - 1)
    modelo = max(ultima, PRIMEIRA_LINHA)
    for i, rot in enumerate(roteiros):
        r = ultima + 1 + i
        m = lambda col: f"{col}{modelo}"   # noqa: E731 - estilo da última linha preenchida
        aba.definir(f"C{r}", f"{rot.cenario} - {nomes[rot.cenario]}", m("C"))
        aba.definir(f"D{r}", str(rot.ordem), m("D"))
        aba.valor_calculado(f"B{r}", rot.codigo)
        aba.valor_calculado(f"E{r}", CENARIOS.get(rot.cenario, ("", "", ""))[2])
        for col, valor in (("F", rot.processo), ("G", rot.subprocesso), ("H", rot.descricao), ("I", rot.esperado),
                           ("S", rot.observacoes), ("T", "\n".join(e.arquivo for e in rot.evidencias))):
            aba.definir(f"{col}{r}", valor, m(col), quebra=True)
        aba.definir(f"J{r}", responsavel, m("J"))
        for col in ("L", "M", "N"):
            aba.definir(f"{col}{r}", hoje, m(col))
        aba.definir(f"O{r}", rot.status, m("O"))
        aba.definir(f"P{r}", "Não", m("P"))
        aba.altura(r, 110)
    aba.filtro_ate("T", ultima + len(roteiros))


def _acrescentar_evidencias(aba: Aba, roteiros: list[Roteiro], hoje: date) -> None:
    ultima = aba.ultima_linha("B", 4)
    modelo = ultima if ultima >= 5 else None
    r = ultima
    for rot in roteiros:
        for ev in rot.evidencias:
            r += 1
            m = lambda col: modelo and f"{col}{modelo}"   # noqa: E731
            for col, valor, quebra in (("B", rot.codigo, False), ("C", f"{rot.processo} - {rot.subprocesso}", True),
                                       ("D", ev.arquivo, True), ("E", ev.descricao, True), ("F", hoje, False),
                                       ("G", "", False)):
                aba.definir(f"{col}{r}", valor, m(col), quebra=quebra)
            aba.altura(r, 32)
    aba.filtro_ate("G", r)


def _versao(aba: Aba, hoje: date, responsavel: str, descricao: str) -> None:
    atual = aba.texto("C16") or "1.0"
    try:
        nova = f"{float(atual.replace(',', '.')) + 0.1:.1f}"
    except ValueError:
        nova = atual
    anterior = aba.texto("C18") or ""
    aba.definir("C15", hoje.strftime("%d/%m/%Y"))
    aba.definir("C16", nova)
    aba.definir("C17", responsavel)
    aba.definir("C18", f"{anterior} | {nova} ({hoje:%d/%m/%Y}): {descricao}".strip(" |"))


_ABA_EVIDENCIAS = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
    '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
    'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
    '<sheetViews><sheetView showGridLines="0" workbookViewId="0"><pane ySplit="4" topLeftCell="A5" activePane="bottomLeft" '
    'state="frozen"/></sheetView></sheetViews><sheetFormatPr defaultRowHeight="15.75"/>'
    '<cols><col min="1" max="1" width="1.63" customWidth="1"/><col min="2" max="2" width="14" customWidth="1"/>'
    '<col min="3" max="3" width="40" customWidth="1"/><col min="4" max="4" width="52" customWidth="1"/>'
    '<col min="5" max="5" width="60" customWidth="1"/><col min="6" max="6" width="16" customWidth="1"/>'
    '<col min="7" max="7" width="50" customWidth="1"/></cols><sheetData>'
    '<row r="2"><c r="B2" t="inlineStr"><is><t>Evidências dos Testes - MIT045</t></is></c></row>'
    '<row r="4">' + "".join(f'<c r="{c}4" t="inlineStr"><is><t>{t}</t></is></c>' for c, t in zip(
        "BCDEFG", ("Cod. Roteiro", "Roteiro", "Arquivo", "O que mostra", "Data da evidência", "Link no Drive"))) +
    '</row></sheetData><autoFilter ref="B4:G4"/></worksheet>')
