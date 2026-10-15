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
from conciliacao.protheus.tela import ErroDeTela, MensagemProtheus, Tela

log = logging.getLogger(__name__)

RE_VALOR = re.compile(r"-?\d{1,3}(?:\.\d{3})*,\d{2}")


def _valor_brl(texto: str) -> Optional[Decimal]:
    match = RE_VALOR.search(texto)
    return dinheiro(match.group().replace(".", "").replace(",", ".")) if match else None


def saldos_do_modal(texto: str) -> dict[str, Optional[Decimal]]:
    """Valores do modal "Saldos bancários" (texto já com espaços normalizados), por rótulo."""
    rotulos = (s.CONC_SALDO_ANTERIOR_BANCARIO, s.CONC_SALDO_ANTERIOR_CONCILIADO, s.CONC_SALDO_ATUAL,
               s.CONC_SALDO_CONCILIADO)
    return {r: _valor_brl(texto.split(r, 1)[1]) if r in texto else None for r in rotulos}


def conferir_conciliado(saldos: dict[str, Optional[Decimal]], saldo_extrato: Decimal) -> Optional[str]:
    """Depois de aplicar: o que foi conciliado é exatamente o movimento do dia? None = sim; senão, o motivo.

    Em produção (15/10/2026) o "Saldo anterior (Conciliado)" é diferente do Bancário: há movimentos de dias anteriores
    nunca conciliados NESTA rotina. Então o "Saldo atual (Conciliado)" não é o saldo do extrato; o certo é
    conciliado atual = conciliado anterior + movimento do dia (bancário atual - bancário anterior)."""
    faltando = [r for r, v in saldos.items() if v is None]
    if faltando:
        return f"não consegui ler {faltando} no modal de saldos"
    bancario, conciliado = saldos[s.CONC_SALDO_ATUAL], saldos[s.CONC_SALDO_CONCILIADO]
    if bancario != saldo_extrato:
        return f"'{s.CONC_SALDO_ATUAL}' = {bancario}, extrato = {saldo_extrato}"
    movimento_do_dia = bancario - saldos[s.CONC_SALDO_ANTERIOR_BANCARIO]
    esperado = saldos[s.CONC_SALDO_ANTERIOR_CONCILIADO] + movimento_do_dia
    if conciliado != esperado:
        return (f"'{s.CONC_SALDO_CONCILIADO}' = {conciliado}, esperado {esperado} (conciliado anterior "
                f"{saldos[s.CONC_SALDO_ANTERIOR_CONCILIADO]} + movimento do dia {movimento_do_dia}): "
                "nem todos os movimentos do dia foram conciliados")
    return None


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

    banco = f.get_by_label(s.CONC_FILTRO_BANCO, exact=True)
    if not any(banco.nth(i).is_visible() for i in range(banco.count())):  # painel já aberto: clicar fecharia
        _esperar(tela, f.get_by_role("button", name=s.CONC_VER_FILTROS), "ver_filtros").click()
    _aguardar_ocioso(tela)
    data = conciliacao.data.strftime("%d/%m/%Y")
    conta = conciliacao.conta
    filtros = ((s.CONC_FILTRO_DATA_DE, data), (s.CONC_FILTRO_DATA_ATE, data),
               (s.CONC_FILTRO_BANCO, conta.banco), (s.CONC_FILTRO_AGENCIA, conta.agencia),
               (s.CONC_FILTRO_CONTA, conta.numero))
    _preencher_filtros(tela, f, filtros)
    _esperar(tela, f.get_by_role("button", name=s.CONC_APLICAR_FILTRO), "aplicar_filtro").click()
    _esperar(tela, f.get_by_role("button", name=s.CONC_SALDOS), "saldos_bancarios")
    tela.page.wait_for_timeout(2000)
    tela.print(f"conciliador_{conta.chave}_filtrado")


def _preencher_filtros(tela: Tela, f: Frame, filtros, tentativas: int = 3) -> None:
    """Preenche os filtros e CONFERE TODOS de novo logo antes do Aplicar. Ao abrir, o painel recarrega
    sozinho os filtros da última vez, com atraso: em 05/10/2026 o Banco mostrou "237" logo após digitar e
    depois voltou para "001" (conta anterior) — o saldo lido seria de outra conta."""
    for tentativa in range(1, tentativas + 1):
        for rotulo, valor in filtros:
            campo = _esperar(tela, f.get_by_label(rotulo, exact=True), f"filtro_{rotulo}")
            if campo.input_value().strip() == valor:
                continue
            campo.click()
            campo.fill("")
            campo.type(valor, delay=30)
            campo.press("Tab")
            tela.page.wait_for_timeout(300)
        tela.page.wait_for_timeout(1500)  # tempo para o recarregamento atrasado aparecer, se vier
        _aguardar_ocioso(tela)
        errados = [(rotulo, valor, lido) for rotulo, valor in filtros
                   if (lido := _esperar(tela, f.get_by_label(rotulo, exact=True), f"filtro_{rotulo}")
                       .input_value().strip()) != valor]
        if not errados:
            return
        log.info("Filtros do Conciliador mudaram depois de preenchidos (tentativa %d): %s", tentativa, errados)
    caminho = tela.diagnostico("conciliador_filtros_divergentes")
    raise ErroDeTela(f"Conciliador: filtros não ficaram como esperado: {errados}. Diagnóstico: {caminho}")


def ler_saldos(tela: Tela, f: Frame, conciliacao: Conciliacao) -> dict[str, Optional[Decimal]]:
    """Os quatro saldos do modal "Saldos bancários", conferindo que é a conta pedida."""
    _esperar(tela, f.get_by_role("button", name=s.CONC_SALDOS), "botao_saldos").click()
    modal = _esperar_modal(tela, f, s.CONC_SALDO_ATUAL)
    texto = " ".join(modal.inner_text().split())
    conta_tela = re.search(r"Conta:\s*([\d\s-]+?)\s+Quantidade", texto)
    esperado = f"{conciliacao.conta.banco} - {conciliacao.conta.agencia} - {conciliacao.conta.numero}"
    if not conta_tela or conta_tela.group(1).strip() != esperado:
        raise ErroDeTela(f"Saldos bancários de outra conta: tela {conta_tela and conta_tela.group(1)!r}, esperado {esperado!r}")
    saldos = saldos_do_modal(texto)
    if saldos[s.CONC_SALDO_ATUAL] is None:
        raise ErroDeTela(f"Não consegui ler o '{s.CONC_SALDO_ATUAL}' em: {texto[:300]}")
    tela.print(f"conciliador_{conciliacao.conta.chave}_saldos")
    modal.get_by_role("button", name=s.CONC_FECHAR, exact=True).first.click()
    return saldos


def conciliar(sessao: SessaoProtheus, conciliacao: Conciliacao, ensaio: bool = False) -> tuple[bool, Decimal]:
    """Lê o Saldo atual (Bancário) da conta no Conciliador e só concilia se for EXATAMENTE igual ao do extrato.

    Antes de marcar qualquer linha, confere que os movimentos a conciliar somam exatamente a variação do saldo
    bancário do dia (senão MensagemProtheus: vai para o relatório e nada é marcado; vale também no ensaio).
    Retorna (conciliou, saldo_protheus). ensaio=True: só lê e compara, nunca concilia.
    """
    tela = sessao.abrir_rotina(s.ROTINA_CONCILIADOR)
    f = _frame(tela)
    _preparar(tela, f, conciliacao)
    saldos = ler_saldos(tela, f, conciliacao)
    saldo_protheus = saldos[s.CONC_SALDO_ATUAL]
    log.info("Conta %s: saldo Protheus %s x extrato %s", conciliacao.conta, saldo_protheus, conciliacao.saldo_extrato)
    if saldo_protheus != conciliacao.saldo_extrato:
        return False, saldo_protheus
    visiveis = _movimentos_a_conciliar(tela, f, conciliacao, saldos)
    if ensaio:
        return False, saldo_protheus
    _aplicar_conciliacao(tela, f, conciliacao, visiveis)
    return True, saldo_protheus


def soma_dos_movimentos(cabecalho: list[str], linhas: list[list[str]]) -> Decimal:
    """Soma com sinal (R = entrada, P = saída) da coluna "Valor Mov." das linhas da grade do Conciliador."""
    nomes = [" ".join(c.split()) for c in cabecalho]
    i_valor = next(i for i, c in enumerate(nomes) if c.startswith(s.CONC_COLUNA_VALOR))
    i_tipo = next(i for i, c in enumerate(nomes) if c.startswith(s.CONC_COLUNA_REC_PAG))
    total = Decimal("0")
    for celulas in linhas:
        valor, tipo = _valor_brl(celulas[i_valor]), celulas[i_tipo].strip()
        if valor is None or tipo not in ("R", "P"):
            raise ValueError(f"Linha do Conciliador sem valor/Rec-Pag legível: {celulas}")
        total += valor if tipo == "R" else -valor
    return total


def _movimentos_a_conciliar(tela: Tela, f: Frame, conciliacao: Conciliacao, saldos: dict) -> list:
    """Aba "Dados não Encontrados": confere que cada linha é da conta e do dia e que a soma delas é a variação do
    saldo bancário do dia. Produção, 15/10/2026 (ABC C/C, movimento de 14/10): saldo bancário 434,49 -> 510,24
    (+75,75), mas os 11 movimentos listados somavam -30.427,85 e foram conciliados assim mesmo."""
    chave = conciliacao.conta.chave
    _esperar(tela, f.get_by_text(s.CONC_ABA_NAO_ENCONTRADOS, exact=True), "aba_nao_encontrados").click()
    tela.page.wait_for_timeout(1500)
    _aguardar_ocioso(tela)
    # Há outras tabelas escondidas na página: considerar só as linhas VISÍVEIS com caixa de seleção
    linhas = f.locator("tbody tr").filter(has=f.locator(s.CONC_CHECKBOX_LINHA))
    visiveis = [linhas.nth(i) for i in range(linhas.count()) if linhas.nth(i).is_visible()]
    if not visiveis:
        return visiveis
    for linha in visiveis:  # conferência: tudo que vai ser marcado é desta conta e deste dia
        texto = linha.inner_text()
        if conciliacao.conta.numero not in texto or conciliacao.data.strftime("%d/%m/%Y") not in texto:
            raise ErroDeTela(f"Linha de outra conta/data em 'Dados não Encontrados' ({chave}): {texto[:150]}")
    cabecalho = visiveis[0].locator("xpath=ancestor::table[1]").locator("thead th").all_inner_texts()
    soma = soma_dos_movimentos(cabecalho, [l.locator("td").all_inner_texts() for l in visiveis])
    anterior = saldos[s.CONC_SALDO_ANTERIOR_BANCARIO]
    if anterior is None:
        raise ErroDeTela(f"Não consegui ler o '{s.CONC_SALDO_ANTERIOR_BANCARIO}' ({chave})")
    variacao = saldos[s.CONC_SALDO_ATUAL] - anterior
    if soma != variacao:
        tela.print(f"conciliador_{chave}_movimentos_divergentes")
        raise MensagemProtheus(
            f"Os {len(visiveis)} movimentos de {conciliacao.data:%d/%m/%Y} a conciliar somam {soma}, mas o saldo "
            f"bancário do dia variou {variacao} ({anterior} -> {saldos[s.CONC_SALDO_ATUAL]}): há movimento do dia "
            "fora da lista (outra data de disponibilidade ou já conciliado). Conciliação NÃO aplicada.")
    return visiveis


def _toast(tela: Tela, f: Frame, texto: str, timeout: int = 120) -> None:
    limite = time.monotonic() + timeout
    while time.monotonic() < limite:
        mensagens = f.locator(s.CONC_TOAST).evaluate_all(
            "es => es.filter(e => e.checkVisibility()).map(e => e.innerText.replace(/\\s+/g, ' '))")
        if any(texto in m for m in mensagens):
            return
        # Recusa do Conciliador (ex.: "Conciliação não realizada, não existem informações para gravação", produção
        # 15/10/2026): é resposta do Protheus, não falha de tela -> relatório, sem nova tentativa
        if erros := [m for m in mensagens if any(p in m.lower() for p in ("erro", "falha", "não realizada"))]:
            raise MensagemProtheus(f"Conciliador: {erros[0][:300]}")
        tela.page.wait_for_timeout(500)
    caminho = tela.diagnostico(f"conciliador_sem_toast_{texto[:15]}")
    raise ErroDeTela(f"Conciliador: mensagem '{texto}' não apareceu. Diagnóstico: {caminho}")


def _aplicar_conciliacao(tela: Tela, f: Frame, conciliacao: Conciliacao, visiveis: list) -> None:
    """Calibrado em 02/10/2026 (caução Santander 29000376, homologação):

        aba "Dados não Encontrados" -> marcar todos (checkbox do cabeçalho) -> Ações > Conciliar
        -> toast "Conciliar - Execução realizada com sucesso!" (cria o match, ainda reversível)
        -> "Aplicar Conciliação" -> "Deseja mesmo aplicar todos os dados da Conciliação?" Ok
        -> toast "Conciliação realizada com sucesso" (efetiva: status N -> S)
        -> confere "Saldo atual (Conciliado)" = saldo do extrato -> Sair da Conciliação.
    O filtro (conta + data) garante que só os movimentos desta conta/dia estão na tela.
    """
    chave = conciliacao.conta.chave
    if visiveis:  # já conferidas em _movimentos_a_conciliar (conta, data e soma)
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
    aplicar = f.get_by_role("button", name=s.CONC_APLICAR)
    # Em produção o botão "Aplicar Conciliação" fica visível mesmo sem nada a aplicar (15/10/2026: o clique deu
    # "Conciliação não realizada, não existem informações para gravação"). Match pendente = linha com "Cancelar Match".
    matches = f.get_by_text(s.CONC_CANCELAR_MATCH, exact=True)
    tem_match = any(matches.nth(i).is_visible() for i in range(matches.count()))
    if not visiveis and not tem_match:
        # Nada a conciliar nem a aplicar: o dia já foi conciliado (execução anterior que caiu depois de aplicar)
        saldos = _ler_saldos_do_modal(tela, f, f"conciliador_{chave}_ja_conciliada")
        if motivo := conferir_conciliado(saldos, conciliacao.saldo_extrato):
            raise MensagemProtheus(f"Nada a conciliar em {conciliacao.data:%d/%m/%Y}, mas a conferência não fecha: {motivo}")
        log.info("Conta %s: movimentos de %s já conciliados", conciliacao.conta, conciliacao.data)
        return
    _esperar(tela, aplicar, "aplicar_conciliacao").click()
    _esperar_modal(tela, f, s.CONC_APLICAR_PERGUNTA).get_by_role("button", name=s.CONC_OK).first.click()
    _toast(tela, f, s.CONC_SUCESSO)
    _aguardar_ocioso(tela)
    tela.print(f"conciliador_{chave}_aplicado")

    saldos = _ler_saldos_do_modal(tela, f, f"conciliador_{chave}_saldos_depois")
    _esperar(tela, f.get_by_role("button", name=s.CONC_SAIR), "sair").click()
    _esperar_modal(tela, f, s.CONC_SAIR_PERGUNTA).get_by_role("button", name=s.CONC_OK).first.click()
    _aguardar_ocioso(tela)
    # A conciliação JÁ foi aplicada: problema aqui não é falha técnica (repetir não adianta); vai para o relatório
    if motivo := conferir_conciliado(saldos, conciliacao.saldo_extrato):
        raise MensagemProtheus(f"Conciliação aplicada, mas a conferência não fechou: {motivo}")
    historico = saldos[s.CONC_SALDO_CONCILIADO] - saldos[s.CONC_SALDO_ATUAL]
    if historico:
        log.warning("Conta %s conciliada; o saldo conciliado acumulado difere do bancário em %s (movimentos de dias "
                    "anteriores não conciliados no Conciliador)", conciliacao.conta, historico)


def _ler_saldos_do_modal(tela: Tela, f: Frame, print_nome: str) -> dict[str, Optional[Decimal]]:
    _esperar(tela, f.get_by_role("button", name=s.CONC_SALDOS), "botao_saldos").click()
    modal = _esperar_modal(tela, f, s.CONC_SALDO_ATUAL)
    saldos = saldos_do_modal(" ".join(modal.inner_text().split()))
    tela.print(print_nome)
    modal.get_by_role("button", name=s.CONC_FECHAR, exact=True).first.click()
    return saldos
