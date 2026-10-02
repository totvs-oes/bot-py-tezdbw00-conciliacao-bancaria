"""Rotina 5: Novo Conciliador Backoffice (CTBA940, PO-UI), configuração 0024 - Conciliação Bancária Manual.

Calibrado em 02/10/2026 na homologação. A tela roda num iframe próprio (URL com "ctba940"):
  menu lateral "Conciliador" -> combo "Selecione uma configuração de conciliação" -> 0024
  -> "Ver Filtros" (painel lateral: Data Dispon. de/até, Banco/Agencia/Conta Banco igual a) -> Aplicar
  -> botões Exportar Dados / Saldos bancários / Sair da Conciliação (+ Aplicar Conciliação quando há match)
  -> abas "Dados da Conciliação" / "Dados não Encontrados" (grid FK5 com checkbox, botão "Ações").
"Saldos bancários" abre um modal com "Saldo atual (Bancário)" e "Saldo atual (Conciliado)".
"Sair da Conciliação" pede confirmação ("Deseja mesmo sair desta conciliação?" -> Ok) e volta ao combo.
"""
from __future__ import annotations

import logging
import re
import time
from decimal import Decimal
from typing import Optional

from playwright.sync_api import Frame

from conciliacao.modelos import Conciliacao, dinheiro
from conciliacao.protheus import seletores as s
from conciliacao.protheus.sessao import SessaoProtheus
from conciliacao.protheus.tela import ErroDeTela, Tela

log = logging.getLogger(__name__)

RE_VALOR = re.compile(r"-?\d{1,3}(?:\.\d{3})*,\d{2}")


def _valor_brl(texto: str) -> Optional[Decimal]:
    match = RE_VALOR.search(texto)
    return dinheiro(match.group().replace(".", "").replace(",", ".")) if match else None


def _frame(tela: Tela, timeout: int = 60) -> Frame:
    """O Conciliador (PO-UI) roda num iframe cuja URL contém "ctba940"."""
    limite = time.monotonic() + timeout
    while time.monotonic() < limite:
        for frame in tela.page.frames:
            if s.CONC_FRAME_URL in frame.url:
                return frame
        tela.page.wait_for_timeout(1000)
    raise ErroDeTela("Iframe do Conciliador (ctba940) não apareceu")



def _esperar(tela: Tela, locator, descricao: str, timeout: int = 60):
    # O PO-UI mantém cópias escondidas de modais/botões: procurar o VISÍVEL entre todos os encontrados
    limite = time.monotonic() + timeout
    while time.monotonic() < limite:
        if _carregando(tela):
            tela.page.wait_for_timeout(500)
            continue
        try:
            for i in range(locator.count()):
                if locator.nth(i).is_visible():
                    return locator.nth(i)
        except Exception:
            pass
        tela.page.wait_for_timeout(500)
    caminho = tela.diagnostico(f"conciliador_sem_{descricao}")
    raise ErroDeTela(f"Conciliador: '{descricao}' não apareceu. Diagnóstico: {caminho}")


def _carregando(tela: Tela) -> bool:
    """O PO-UI cobre a tela com um overlay "aria-busy" enquanto carrega; cliques nesse período falham."""
    for frame in tela.page.frames:
        if s.CONC_FRAME_URL not in frame.url:
            continue
        try:
            overlay = frame.locator(s.CONC_CARREGANDO)
            if any(overlay.nth(i).is_visible() for i in range(overlay.count())):
                return True
        except Exception:
            return True  # frame recarregando
    return False


def _aguardar_ocioso(tela: Tela, timeout: int = 180) -> None:
    limite = time.monotonic() + timeout
    while _carregando(tela):
        if time.monotonic() > limite:
            raise ErroDeTela(f"Conciliador continua carregando depois de {timeout}s")
        tela.page.wait_for_timeout(500)


def _modal(f: Frame, texto: str):
    """Conteúdo VISÍVEL do modal PO-UI que contém o texto (o host <po-modal> tem tamanho zero e há
    cópias escondidas; o que aparece é o div.po-modal-content)."""
    textos = f.get_by_text(texto, exact=False)
    for i in range(textos.count()):
        if textos.nth(i).is_visible():
            return textos.nth(i).locator("xpath=ancestor::div[contains(@class,'po-modal-content')][1]")
    return None


def _esperar_modal(tela: Tela, f: Frame, texto: str, timeout: int = 30):
    limite = time.monotonic() + timeout
    while time.monotonic() < limite:
        _aguardar_ocioso(tela)
        if (modal := _modal(f, texto)) is not None:
            return modal
        tela.page.wait_for_timeout(500)
    caminho = tela.diagnostico(f"conciliador_sem_modal_{texto[:20]}")
    raise ErroDeTela(f"Conciliador: modal '{texto}' não apareceu. Diagnóstico: {caminho}")


def _fechar_modais(tela: Tela, f: Frame) -> None:
    """Fecha modais que tenham ficado abertos (execução anterior interrompida)."""
    if (modal := _modal(f, s.CONC_SAIR_PERGUNTA)) is not None:
        modal.get_by_role("button", name=s.CONC_OK).first.click()  # sair da conciliação não aplicada
        tela.page.wait_for_timeout(2000)
    if (modal := _modal(f, s.CONC_SALDO_ATUAL)) is not None:
        modal.get_by_role("button", name=s.CONC_FECHAR, exact=True).first.click()
        tela.page.wait_for_timeout(1000)


def _preparar(tela: Tela, f: Frame, conciliacao: Conciliacao) -> None:
    """Sai de uma conciliação aberta (se houver), escolhe a 0024, filtra conta/data e aplica."""
    tela.page.wait_for_timeout(1000)
    _aguardar_ocioso(tela)
    _fechar_modais(tela, f)
    _aguardar_ocioso(tela)
    sair = f.get_by_role("button", name=s.CONC_SAIR)
    if any(sair.nth(i).is_visible() for i in range(sair.count())):
        _esperar(tela, sair, "sair_da_conciliacao").click()
        _esperar_modal(tela, f, s.CONC_SAIR_PERGUNTA).get_by_role("button", name=s.CONC_OK).first.click()
        tela.page.wait_for_timeout(2000)

    combo = f.get_by_placeholder(s.CONC_CONFIGURACAO)
    if not (combo.count() and combo.first.is_visible()):
        f.locator("po-menu-item", has_text=s.CONC_MENU_CONCILIADOR).first.click()
    combo = _esperar(tela, combo, "combo_configuracao")
    if s.CONC_CONFIGURACAO_0024 not in combo.input_value():
        combo.click()
        _esperar(tela, f.get_by_text(s.CONC_CONFIGURACAO_0024, exact=True), "opcao_0024").click()

    _esperar(tela, f.get_by_role("button", name=s.CONC_VER_FILTROS), "ver_filtros").click()
    data = conciliacao.data.strftime("%d/%m/%Y")
    conta = conciliacao.conta
    for rotulo, valor in ((s.CONC_FILTRO_DATA_DE, data), (s.CONC_FILTRO_DATA_ATE, data),
                          (s.CONC_FILTRO_BANCO, conta.banco), (s.CONC_FILTRO_AGENCIA, conta.agencia),
                          (s.CONC_FILTRO_CONTA, conta.numero)):
        campo = _esperar(tela, f.get_by_label(rotulo, exact=True), f"filtro_{rotulo}")
        campo.click()
        campo.fill("")
        campo.type(valor, delay=30)
        campo.press("Tab")
        tela.page.wait_for_timeout(300)
        if campo.input_value().strip() != valor:
            raise ErroDeTela(f"Conciliador: filtro '{rotulo}' ficou {campo.input_value()!r}, esperado {valor!r}")
    _esperar(tela, f.get_by_role("button", name=s.CONC_APLICAR_FILTRO), "aplicar_filtro").click()
    _esperar(tela, f.get_by_role("button", name=s.CONC_SALDOS), "saldos_bancarios")
    tela.page.wait_for_timeout(2000)
    tela.print(f"conciliador_{conta.chave}_filtrado")


def ler_saldo(tela: Tela, f: Frame, conciliacao: Conciliacao) -> Decimal:
    _esperar(tela, f.get_by_role("button", name=s.CONC_SALDOS), "botao_saldos").click()
    modal = _esperar_modal(tela, f, s.CONC_SALDO_ATUAL)
    texto = " ".join(modal.inner_text().split())
    conta_tela = re.search(r"Conta:\s*([\d\s-]+?)\s+Quantidade", texto)
    esperado = f"{conciliacao.conta.banco} - {conciliacao.conta.agencia} - {conciliacao.conta.numero}"
    if not conta_tela or conta_tela.group(1).strip() != esperado:
        raise ErroDeTela(f"Saldos bancários de outra conta: tela {conta_tela and conta_tela.group(1)!r}, esperado {esperado!r}")
    trecho = texto.split(s.CONC_SALDO_ATUAL, 1)[1]
    saldo = _valor_brl(trecho)
    if saldo is None:
        raise ErroDeTela(f"Não consegui ler o '{s.CONC_SALDO_ATUAL}' em: {texto[:300]}")
    tela.print(f"conciliador_{conciliacao.conta.chave}_saldos")
    modal.get_by_role("button", name=s.CONC_FECHAR, exact=True).first.click()
    return saldo


def conciliar(sessao: SessaoProtheus, conciliacao: Conciliacao, ensaio: bool = False) -> tuple[bool, Decimal]:
    """Lê o Saldo atual (Bancário) da conta no Conciliador e só concilia se for EXATAMENTE igual ao do extrato.

    Retorna (conciliou, saldo_protheus). ensaio=True: só lê e compara, nunca concilia.
    """
    tela = sessao.abrir_rotina(s.ROTINA_CONCILIADOR)
    f = _frame(tela)
    _preparar(tela, f, conciliacao)
    saldo_protheus = ler_saldo(tela, f, conciliacao)
    log.info("Conta %s: saldo Protheus %s x extrato %s", conciliacao.conta, saldo_protheus, conciliacao.saldo_extrato)
    if saldo_protheus != conciliacao.saldo_extrato or ensaio:
        return False, saldo_protheus
    _aplicar_conciliacao(tela, f, conciliacao)
    return True, saldo_protheus


def _toast(tela: Tela, f: Frame, texto: str, timeout: int = 120) -> None:
    limite = time.monotonic() + timeout
    while time.monotonic() < limite:
        mensagens = f.locator(s.CONC_TOAST).evaluate_all(
            "es => es.filter(e => e.checkVisibility()).map(e => e.innerText.replace(/\\s+/g, ' '))")
        if any(texto in m for m in mensagens):
            return
        if erros := [m for m in mensagens if "erro" in m.lower() or "falha" in m.lower()]:
            raise ErroDeTela(f"Conciliador: {erros[0][:300]}")
        tela.page.wait_for_timeout(500)
    caminho = tela.diagnostico(f"conciliador_sem_toast_{texto[:15]}")
    raise ErroDeTela(f"Conciliador: mensagem '{texto}' não apareceu. Diagnóstico: {caminho}")


def _aplicar_conciliacao(tela: Tela, f: Frame, conciliacao: Conciliacao) -> None:
    """Calibrado em 02/10/2026 (caução Santander 29000376, homologação):

        aba "Dados não Encontrados" -> marcar todos (checkbox do cabeçalho) -> Ações > Conciliar
        -> toast "Conciliar - Execução realizada com sucesso!" (cria o match, ainda reversível)
        -> "Aplicar Conciliação" -> "Deseja mesmo aplicar todos os dados da Conciliação?" Ok
        -> toast "Conciliação realizada com sucesso" (efetiva: status N -> S)
        -> confere "Saldo atual (Conciliado)" = saldo do extrato -> Sair da Conciliação.
    O filtro (conta + data) garante que só os movimentos desta conta/dia estão na tela.
    """
    chave = conciliacao.conta.chave
    _esperar(tela, f.get_by_text(s.CONC_ABA_NAO_ENCONTRADOS, exact=True), "aba_nao_encontrados").click()
    tela.page.wait_for_timeout(1500)
    _aguardar_ocioso(tela)

    # Há outras tabelas escondidas na página: considerar só as linhas VISÍVEIS com caixa de seleção
    linhas = f.locator("tbody tr").filter(has=f.locator(s.CONC_CHECKBOX_LINHA))
    visiveis = [linhas.nth(i) for i in range(linhas.count()) if linhas.nth(i).is_visible()]
    if visiveis:
        for linha in visiveis:  # conferência: tudo que vai ser marcado é desta conta e deste dia
            texto = linha.inner_text()
            if conciliacao.conta.numero not in texto or conciliacao.data.strftime("%d/%m/%Y") not in texto:
                raise ErroDeTela(f"Linha de outra conta/data em 'Dados não Encontrados' ({chave}): {texto[:150]}")
        for linha in visiveis:
            caixa = linha.locator(f"{s.CONC_CHECKBOX_LINHA} [role=checkbox]").first
            if caixa.get_attribute("aria-checked") != "true":
                caixa.click()
                tela.page.wait_for_timeout(300)
        marcadas = [l.locator(f"{s.CONC_CHECKBOX_LINHA} [role=checkbox]").first.get_attribute("aria-checked")
                    for l in visiveis]
        if marcadas.count("true") != len(visiveis):
            raise ErroDeTela(f"Esperava {len(visiveis)} linhas marcadas, marcadas: {marcadas}")
        tela.print(f"conciliador_{chave}_selecionados")
        _esperar(tela, f.locator("po-dropdown").filter(has_text=s.CONC_ACOES), "acoes").click()
        _esperar(tela, f.get_by_text(s.CONC_CONCILIAR, exact=True), "acao_conciliar").click()
        _toast(tela, f, s.CONC_MATCH_SUCESSO)
        _aguardar_ocioso(tela)

    # "Aplicar Conciliação" só aparece na aba "Dados da Conciliação" (onde ficam os matches)
    _esperar(tela, f.get_by_text(s.CONC_ABA_CONCILIACAO, exact=True), "aba_conciliacao").click()
    tela.page.wait_for_timeout(1500)
    _aguardar_ocioso(tela)
    _esperar(tela, f.get_by_role("button", name=s.CONC_APLICAR), "aplicar_conciliacao").click()
    _esperar_modal(tela, f, s.CONC_APLICAR_PERGUNTA).get_by_role("button", name=s.CONC_OK).first.click()
    _toast(tela, f, s.CONC_SUCESSO)
    _aguardar_ocioso(tela)
    tela.print(f"conciliador_{chave}_aplicado")

    conciliado = _ler_saldo_conciliado(tela, f, conciliacao)
    if conciliado != conciliacao.saldo_extrato:
        raise ErroDeTela(f"Conciliação aplicada mas 'Saldo atual (Conciliado)' = {conciliado}, "
                         f"extrato = {conciliacao.saldo_extrato}")
    _esperar(tela, f.get_by_role("button", name=s.CONC_SAIR), "sair").click()
    _esperar_modal(tela, f, s.CONC_SAIR_PERGUNTA).get_by_role("button", name=s.CONC_OK).first.click()
    _aguardar_ocioso(tela)


def _ler_saldo_conciliado(tela: Tela, f: Frame, conciliacao: Conciliacao) -> Decimal:
    _esperar(tela, f.get_by_role("button", name=s.CONC_SALDOS), "botao_saldos").click()
    modal = _esperar_modal(tela, f, s.CONC_SALDO_ATUAL)
    texto = " ".join(modal.inner_text().split())
    valor = _valor_brl(texto.split(s.CONC_SALDO_CONCILIADO, 1)[-1])
    modal.get_by_role("button", name=s.CONC_FECHAR, exact=True).first.click()
    if valor is None:
        raise ErroDeTela(f"Não consegui ler '{s.CONC_SALDO_CONCILIADO}' em: {texto[:300]}")
    return valor
