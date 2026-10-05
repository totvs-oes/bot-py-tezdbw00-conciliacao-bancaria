"""Ações genéricas sobre o Protheus WebApp (Playwright, API síncrona).

Os campos AdvPL só validam quando perdem o foco: sempre preencher -> Tab -> esperar.
"""
from __future__ import annotations

import base64
import json
import logging
import re
import time
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Callable, Optional

from playwright.sync_api import Error as PlaywrightError, Locator, Page, TimeoutError as PlaywrightTimeout

from conciliacao.modelos import formatar_brl
from conciliacao.protheus import seletores as s

log = logging.getLogger(__name__)

TIMEOUT_PADRAO = 30_000      # ms
TIMEOUT_GRAVACAO = 90_000    # gravação + contabilização on-line pode demorar
ESPERA_VALIDACAO = 500       # ms após o Tab para o AdvPL validar o campo

# Para cada rótulo AdvPL visível com o texto pedido (em ordem de leitura), o id do campo logo abaixo dele.
# O input fica no shadow DOM do wa-text-input: document.activeElement é o host, não o input
_JS_TEM_FOCO = "e => e.matches(':focus') || e.getRootNode().activeElement === e"

_JS_HELP_ADVPL = r"""() => {
  for (const v of document.querySelectorAll('wa-text-view')) {
    const c = v.getAttribute('caption') || '';
    if (!/Help:|Problema:/.test(c) || !v.checkVisibility()) continue;
    const d = document.createElement('div'); d.innerHTML = c.replace(/<br\s*\/?>/gi, ' ');
    return d.textContent.replace(/\s+/g, ' ').trim();
  }
  return '';
}"""

# Texto do wa-dialog que contém o botão; null se o botão não estiver num diálogo de aviso.
# Sobe atravessando o shadow DOM (o <button> fica dentro do wa-button). A janela principal do Protheus também
# é um wa-dialog (classe dict-twindow) e contém menu e abas: botão dentro dela NÃO é de aviso.
# O texto dos avisos AdvPL fica no caption (HTML) dos wa-text-view.
_JS_TEXTO_DO_DIALOGO = r"""(botao, classeJanelaPrincipal) => {
  let n = botao;
  while (n && n.tagName !== 'WA-DIALOG') n = n.parentNode || n.host;
  if (!n || n.classList.contains(classeJanelaPrincipal)) return null;
  const legendas = Array.from(n.querySelectorAll('wa-text-view')).map((v) => v.getAttribute('caption') || '');
  const d = document.createElement('div'); d.innerHTML = legendas.join(' ').replace(/<br\s*\/?>/gi, ' ');
  return (d.textContent || n.innerText || '').replace(/\s+/g, ' ').trim() || '(sem texto)';
}"""

_JS_CAMPOS_ADVPL = r"""(rotulo) => {
  const limpo = (t) => (t || '').replace(/<[^>]*>/g, '').replace(/\s+/g, ' ').trim().replace(/\s*\*$/, '');
  const visivel = (el) => el.checkVisibility() && el.getBoundingClientRect().width > 0;
  const rotulos = Array.from(document.querySelectorAll('wa-text-view'))
    .filter((el) => visivel(el) && limpo(el.getAttribute('caption')) === rotulo)
    .map((el) => el.getBoundingClientRect())
    .sort((a, b) => a.top - b.top || a.left - b.left);
  const campos = Array.from(document.querySelectorAll('wa-text-input, wa-combobox, wa-multiget'))
    .filter(visivel).map((el) => ({id: el.id, r: el.getBoundingClientRect()}));
  return rotulos.map((r) => {
    const abaixo = campos.filter((c) => c.r.top >= r.top + r.height / 2 && c.r.top <= r.bottom + 30
                                        && Math.abs(c.r.left - r.left) <= 15)
                         .sort((a, b) => (a.r.top - r.bottom) - (b.r.top - r.bottom));
    return abaixo.length ? abaixo[0].id : null;
  }).filter((id) => id);
}"""

# Lista campos, botões e componentes da tela, atravessando shadow DOM. Não lê o valor dos inputs.
_JS_DIAGNOSTICO = """() => {
  const saida = [];
  const texto = (el) => (el.innerText || el.textContent || '').trim().replace(/\\s+/g, ' ').slice(0, 80);
  const rotuloProximo = (el) => {
    const label = el.labels && el.labels[0];
    if (label) return texto(label);
    const anterior = el.closest('div, td, span')?.previousElementSibling;
    return anterior ? texto(anterior) : null;
  };
  const visitar = (raiz, caminho) => {
    for (const el of raiz.querySelectorAll('*')) {
      const tag = el.tagName.toLowerCase();
      if (el.shadowRoot) visitar(el.shadowRoot, caminho + ' >> ' + tag + (el.id ? '#' + el.id : ''));
      const interativo = ['input', 'textarea', 'select', 'button'].includes(tag)
        || el.getAttribute('role') === 'button' || tag.startsWith('wa-') || tag.startsWith('po-');
      if (!interativo) continue;
      const r = el.getBoundingClientRect();
      saida.push({
        tag, dentro_de: caminho || null, id: el.id || null, name: el.getAttribute('name'),
        type: el.getAttribute('type'), placeholder: el.getAttribute('placeholder'),
        aria_label: el.getAttribute('aria-label'), caption: el.getAttribute('caption'),
        title: el.getAttribute('title'), classe: (el.className && el.className.baseVal === undefined) ? String(el.className).slice(0, 80) : null,
        rotulo_proximo: ['input', 'textarea', 'select'].includes(tag) ? rotuloProximo(el) : null,
        texto: ['input', 'textarea', 'select'].includes(tag) ? null : texto(el),
        visivel: r.width > 0 && r.height > 0,
      });
    }
  };
  visitar(document, '');
  return saida;
}"""


class ErroDeTela(Exception):
    """Falha técnica ao interagir com a tela (elemento não encontrado, timeout...)."""


class MensagemProtheus(Exception):
    """O Protheus abriu um diálogo de help/erro. A mensagem vai para o relatório."""


class Tela:
    def __init__(self, page: Page, pasta_prints: Path):
        self.page = page
        self.pasta_prints = pasta_prints
        self.pasta_prints.mkdir(parents=True, exist_ok=True)
        page.set_default_timeout(TIMEOUT_PADRAO)

    # ------------------------------------------------------------------
    def print(self, nome: str) -> Path:
        caminho = self.pasta_prints / f"{re.sub(r'[^A-Za-z0-9_.-]', '_', nome)}.png"
        try:
            # O screenshot do Playwright espera fontes/estabilidade e trava com diálogos AdvPL abertos
            # (Transferência entre C/C). Captura direto pelo protocolo do Chrome: instantâneo.
            cdp = self.page.context.new_cdp_session(self.page)
            try:
                dados = cdp.send("Page.captureScreenshot", {"format": "png"})["data"]
            finally:
                cdp.detach()
            caminho.write_bytes(base64.b64decode(dados))
        except Exception as erro:  # print nunca pode derrubar o robô
            log.warning("Não foi possível salvar o print %s: %s", caminho.name, erro)
        return caminho

    def esperar_ocioso(self) -> None:
        try:
            self.page.wait_for_load_state("networkidle", timeout=TIMEOUT_PADRAO)
        except PlaywrightTimeout:
            pass  # o WebApp mantém websocket aberto; networkidle pode não acontecer

    def _raizes(self, dentro: Optional[Locator]) -> list:
        """Onde procurar: o container informado, ou a página e TODOS os iframes (o WebApp usa iframes)."""
        return [dentro] if dentro is not None else list(self.page.frames)

    def _procurar(self, descricao: str, estrategias: Callable[[object], list[Locator]], indice: int,
                  timeout: int, dentro: Optional[Locator] = None, diagnosticar: bool = True) -> Locator:
        """Tenta as estratégias em todas as raízes até achar um elemento visível, ou estoura o timeout.

        Não falha na hora: a tela do WebApp é desenhada aos poucos depois do carregamento, e iframes
        (ex.: o login PO-UI) são criados depois — por isso a lista de iframes é relida a cada tentativa.
        """
        limite = time.monotonic() + timeout / 1000
        while True:
            for raiz in self._raizes(dentro):
                for alvo in estrategias(raiz):
                    try:
                        # Estratégia devolve um Locator (ordem do HTML) ou uma lista já na ordem certa
                        candidatos = alvo if isinstance(alvo, list) else [alvo.nth(i) for i in range(alvo.count())]
                        visiveis = [c for c in candidatos if c.is_visible()]
                    except PlaywrightError as erro:
                        if "selector" in str(erro).lower() and "pars" in str(erro).lower():
                            raise ErroDeTela(f"Seletor inválido em {descricao}: {erro}") from erro  # bug: falha já
                        continue  # frame recarregou/foi destruído no meio da busca
                    if len(visiveis) > indice:
                        return visiveis[indice]
            if time.monotonic() > limite:
                if not diagnosticar:
                    raise ErroDeTela(f"{descricao} não encontrado")
                caminho = self.diagnostico(f"nao_encontrado_{descricao}")
                raise ErroDeTela(f"{descricao} não encontrado. Diagnóstico da tela salvo em {caminho}")
            # NUNCA time.sleep aqui: congela os eventos do Playwright e iframes novos (o login) não aparecem
            self.page.wait_for_timeout(500)

    def clicar(self, texto: str, exato: bool = False, dentro: Optional[Locator] = None,
               timeout: int = TIMEOUT_PADRAO) -> None:
        alvo = self._procurar(f"botao_{texto}", lambda raiz: [
            raiz.get_by_role("button", name=texto, exact=exato),
            raiz.get_by_text(texto, exact=exato),
        ], 0, timeout, dentro)
        alvo.click()
        self.esperar_ocioso()
        self.verificar_mensagem()

    def botao(self, nome: str | re.Pattern, descricao: str, timeout: int = TIMEOUT_PADRAO) -> Locator:
        """Botão visível pelo nome exato (str) ou por regex; espera aparecer."""
        # O nome acessível dos botões do WebApp vem com espaços em volta ("  Log Off"): comparação exata falha
        exato = None if isinstance(nome, re.Pattern) else False
        return self._procurar(descricao, lambda raiz: [raiz.get_by_role("button", name=nome, exact=exato)],
                              0, timeout)

    def existe(self, texto: str, timeout: int = 5_000) -> bool:
        """Botão ou texto (inclusive caption de componente AdvPL) aparece em até `timeout` ms?"""
        try:
            self._procurar(f"opcional_{texto}", lambda raiz: [
                raiz.get_by_role("button", name=texto, exact=True),
                raiz.locator(f'[caption="{texto}"], [title="{texto}"]'),
            ], 0, timeout, diagnosticar=False)
            return True
        except ErroDeTela:
            return False

    def campo(self, rotulo: str, indice: int = 0, dentro: Optional[Locator] = None,
              timeout: int = TIMEOUT_PADRAO, diagnosticar: bool = True) -> Locator:
        # Formulários MVC: <label>Rótulo   *</label> seguido do componente do campo (espaços de alinhamento no texto)
        texto_label = re.compile(r"^\s*" + re.escape(rotulo).replace("/", r"\/") + r"\s*\*?\s*$")
        return self._procurar(f"campo_{rotulo}", lambda raiz: [
            self._campos_advpl(raiz, rotulo),
            raiz.locator("label", has_text=texto_label).locator("xpath=following-sibling::*[1]").locator("input, select"),
            raiz.get_by_label(rotulo, exact=True),
            raiz.get_by_placeholder(rotulo, exact=True),
            raiz.get_by_role("textbox", name=rotulo, exact=True),
            # Layout clássico AdvPL: input logo após o texto do rótulo (não atravessa shadow DOM)
            raiz.locator(f"xpath=//*[normalize-space(text())='{rotulo}']/following::input[1]"),
        ], indice, timeout, dentro, diagnosticar)

    def valor_do_campo(self, rotulo: str, indice: int = 0) -> Optional[str]:
        """Valor atual do campo, ou None se o campo não está na tela (sem esperar, sem diagnóstico)."""
        try:
            return self.campo(rotulo, indice, timeout=500, diagnosticar=False).input_value().strip()
        except (ErroDeTela, PlaywrightError):
            return None

    def _campos_advpl(self, raiz, rotulo: str) -> list[Locator]:
        """Telas AdvPL (diálogos clássicos): rótulo = <wa-text-view caption="Rótulo"> ("*" se obrigatório),
        mas o campo NÃO é vizinho no HTML (os rótulos vêm todos antes dos campos, fora de ordem).
        Regra visual: o campo do rótulo é o que está logo ABAIXO dele e alinhado à esquerda.
        Rótulos repetidos (Banco de Origem/Destino) ficam na ordem de leitura: de cima para baixo."""
        if isinstance(raiz, Locator):
            return []
        try:
            ids = raiz.evaluate(_JS_CAMPOS_ADVPL, rotulo)
        except PlaywrightError:
            return []
        return [raiz.locator(f"#{i} input, #{i} select").first for i in ids]

    def elemento_css(self, seletor: str, descricao: str, timeout: int = TIMEOUT_PADRAO) -> Locator:
        """Elemento por seletor CSS (atravessa shadow DOM), procurado em todos os iframes."""
        return self._procurar(descricao, lambda raiz: [raiz.locator(seletor)], 0, timeout)

    def diagnostico(self, nome: str) -> Path:
        """Print + JSON com todos os campos/botões da tela (inclusive dentro de iframes e shadow DOM).

        É o que precisamos para calibrar seletores. Valores digitados NÃO são gravados.
        """
        nome = re.sub(r"[^A-Za-z0-9_.-]", "_", nome)
        self.print(nome)
        quadros = []
        for frame in self.page.frames:
            try:
                elementos = frame.evaluate(_JS_DIAGNOSTICO)
            except PlaywrightError as erro:
                elementos = [{"erro": str(erro)}]
            quadros.append({"url": frame.url, "nome": frame.name, "elementos": elementos})
        caminho = self.pasta_prints / f"{nome}.json"
        caminho.write_text(json.dumps(quadros, ensure_ascii=False, indent=1), encoding="utf-8")
        log.info("Diagnóstico da tela salvo em %s (+ .png)", caminho)
        return caminho

    def preencher(self, rotulo: str, valor: str, indice: int = 0, dentro: Optional[Locator] = None) -> None:
        campo = self.campo(rotulo, indice, dentro)
        # O AdvPL devolve o foco ao "próximo campo" quando termina de validar o anterior; se isso chega depois
        # do nosso clique, a digitação cai no campo errado (visto 02/10/2026: Documento digitado no Num Cheque).
        # Por isso: só digita com o foco confirmado no campo, e confere que o foco não saiu durante a digitação.
        for _ in range(5):
            campo.click()
            self.page.wait_for_timeout(200)
            if campo.evaluate(_JS_TEM_FOCO):
                break
        else:
            raise ErroDeTela(f"Campo '{rotulo}' não recebeu o foco")
        # Seleciona o conteúdo pelo próprio input: Ctrl+A vira a letra "A" em alguns campos do WebApp
        campo.evaluate("e => e.select()")
        # Digitação real (dispara as máscaras). O foco é conferido ANTES do último caractere: campo de tamanho
        # fixo (Banco = 3) pula sozinho para o próximo quando enche, e isso não é foco perdido.
        campo.type(valor[:-1], delay=20)
        if not campo.evaluate(_JS_TEM_FOCO):
            caminho = self.diagnostico("foco_perdido")
            raise ErroDeTela(f"O foco saiu do campo '{rotulo}' durante a digitação. Diagnóstico: {caminho}")
        self.page.keyboard.type(valor[-1:])
        campo.press("Tab")
        self.esperar_ocioso()
        # O AdvPL valida o campo no servidor depois do Tab; cliques nesse intervalo são ignorados
        self.page.wait_for_timeout(ESPERA_VALIDACAO)
        self.verificar_mensagem()

    def confirmar(self, texto: str, tentativas: int = 3) -> None:
        """Clica num botão que fecha a tela/diálogo e garante que fechou (o Protheus às vezes ignora o clique
        enquanto ainda processa a validação do último campo). Tenta de novo se o botão continuar na tela."""
        for tentativa in range(1, tentativas + 1):
            botao = self.botao(texto, f"botao_{texto}")
            botao.click()
            for _ in range(10):
                self.page.wait_for_timeout(500)
                try:
                    if not botao.is_visible():
                        self.esperar_ocioso()
                        self.verificar_mensagem()
                        return
                except PlaywrightError:
                    return  # componente destruído junto com o diálogo
            log.info("'%s' não fechou a tela (tentativa %d); clicando de novo", texto, tentativa)
        caminho = self.diagnostico(f"nao_fechou_{texto}")
        raise ErroDeTela(f"'{texto}' clicado {tentativas} vezes e a tela não fechou. Diagnóstico: {caminho}")

    def preencher_data(self, rotulo: str, dia: date, indice: int = 0, dentro: Optional[Locator] = None) -> None:
        self.preencher(rotulo, dia.strftime("%d/%m/%Y"), indice, dentro)

    def preencher_valor(self, rotulo: str, valor: Decimal, indice: int = 0, dentro: Optional[Locator] = None) -> None:
        self.preencher(rotulo, formatar_brl(valor).replace(".", ""), indice, dentro)

    def _item_de_menu(self, tag: str, texto: str, comeca_com: bool) -> Optional[Locator]:
        """Item de menu visível (wa-menu-item / wa-menu-popup-item) pelo caption sem as tags <u>."""
        ids = self.page.evaluate(
            """([tag, texto, prefixo]) => Array.from(document.querySelectorAll(tag)).filter((el) => {
                 const c = (el.getAttribute('caption') || '').replace(/<[^>]*>/g, '').trim();
                 return el.checkVisibility() && (prefixo ? c.startsWith(texto) : c === texto);
               }).map((el) => el.id)""", [tag, texto, comeca_com])
        return self.page.locator(f"#{ids[0]}") if ids else None

    def item_de_menu_visivel(self, texto: str) -> bool:
        return self._item_de_menu("wa-menu-item", texto, texto.endswith("(")) is not None

    def clicar_menu(self, texto: str, popup: bool = False, timeout: int = TIMEOUT_PADRAO) -> None:
        """Clica num item do menu lateral (popup=False, compara "começa com") ou do popup de Outras Ações."""
        tag = "wa-menu-popup-item" if popup else "wa-menu-item"
        limite = time.monotonic() + timeout / 1000

        def procurar() -> Optional[Locator]:
            # Comparação exata: "Movimento Bancario" não pode casar com o grupo "Movimento Bancario (5)".
            # Grupos do menu são pedidos terminando em "(" ("Atualizações (") e aí vale "começa com".
            return self._item_de_menu(tag, texto, texto.endswith("("))

        while (item := procurar()) is None:
            if time.monotonic() > limite:
                caminho = self.diagnostico(f"menu_nao_encontrado_{texto}")
                raise ErroDeTela(f"Item de menu '{texto}' não encontrado. Diagnóstico: {caminho}")
            self.page.wait_for_timeout(500)
        item.click()
        self.esperar_ocioso()
        self.page.wait_for_timeout(ESPERA_VALIDACAO)
        self.verificar_mensagem()

    def acao_do_popup(self, botao: str, item: str, tentativas: int = 3) -> None:
        """Abre o popup de um botão (ex.: "Outras Ações") e clica no item. O Protheus às vezes ignora o
        clique que abre o popup (tela ainda ocupada após a ação anterior): confere e tenta de novo."""
        for tentativa in range(1, tentativas + 1):
            self.botao(botao, f"botao_{botao}").click()
            for _ in range(10):
                self.page.wait_for_timeout(300)
                if (alvo := self._item_de_menu("wa-menu-popup-item", item, False)) is not None:
                    alvo.click()
                    self.esperar_ocioso()
                    self.page.wait_for_timeout(ESPERA_VALIDACAO)
                    self.verificar_mensagem()
                    return
            log.info("Popup de '%s' não abriu (tentativa %d); clicando de novo", botao, tentativa)
            # Esc só com um popup aberto (sem o nosso item): sem popup, o Esc FECHA a rotina inteira no
            # Protheus (visto em 05/10/2026: a rotina sumiu e o "Outras Ações" com ela)
            if self._popup_aberto():
                self.page.keyboard.press("Escape")
                self.page.wait_for_timeout(ESPERA_VALIDACAO)
        caminho = self.diagnostico(f"popup_sem_{item}")
        raise ErroDeTela(f"Item '{item}' do popup '{botao}' não apareceu. Diagnóstico: {caminho}")

    def _popup_aberto(self) -> bool:
        return self.page.evaluate(
            "() => Array.from(document.querySelectorAll('wa-menu-popup-item')).some((e) => e.checkVisibility())")

    def selecionar_filial(self, codigo: str) -> None:
        """Diálogo "Filiais" antes do formulário. Na homologação ele NÃO aparece (a rotina já abre na
        filial do ambiente); se aparecer em outro ambiente, escolhe a filial e confirma."""
        if not self.existe(s.DIALOGO_FILIAIS, timeout=2_000):
            return
        self.page.get_by_text(codigo, exact=True).first.click()
        self.confirmar(s.BOTAO_OK)

    def fechar_avisos(self, espera_ms: int = 15_000, quieto_ms: int = 3_000, maximo: int = 5) -> list[str]:
        """Fecha avisos informativos do Protheus: wa-dialog com botão "Fechar". Ex. (homologação, logo após o
        "Entrar"): "Este ambiente utiliza base de Desenvolvimento. Seu uso como ambiente de Produção não é
        recomendado e autorizado". Pode não existir em produção: nenhum aviso = segue normalmente.

        Só clica em "Fechar" que esteja DENTRO de um wa-dialog (nunca em aba/menu). Observa até `espera_ms` e
        termina antes se passar `quieto_ms` sem aviso novo. Devolve os textos fechados (ficam no log e em print).
        """
        textos: list[str] = []
        limite = time.monotonic() + espera_ms / 1000
        ultimo = time.monotonic()
        while time.monotonic() < limite and time.monotonic() - ultimo < quieto_ms / 1000 and len(textos) < maximo:
            achado = self._fechar_de_dialogo()
            if achado is None:
                self.page.wait_for_timeout(500)
                continue
            texto, botao = achado
            self.print(f"aviso_protheus_{len(textos) + 1}")
            log.warning("Aviso do Protheus fechado: %s", texto)
            botao.click()
            self.esperar_ocioso()
            self.page.wait_for_timeout(ESPERA_VALIDACAO)
            textos.append(texto)
            ultimo = time.monotonic()
        return textos

    def _fechar_de_dialogo(self) -> Optional[tuple[str, Locator]]:
        botoes = self.page.get_by_role("button", name=re.compile(r"^\s*" + re.escape(s.AVISO_FECHAR) + r"\s*$"))
        for i in range(botoes.count()):
            botao = botoes.nth(i)
            try:
                if (botao.is_visible()
                        and (texto := botao.evaluate(_JS_TEXTO_DO_DIALOGO, s.JANELA_PRINCIPAL_CLASSE)) is not None):
                    return texto, botao
            except PlaywrightError:
                continue  # componente sumiu entre a contagem e a leitura
        return None

    def help_aberto(self) -> bool:
        return bool(self.page.evaluate(_JS_HELP_ADVPL))

    def fechar_help(self) -> Optional[str]:
        """Fecha o Help AdvPL aberto (ex.: "Help: FA100BCO / Problema: Banco/Agencia/Conta não cadastrado") e
        devolve o texto; None se não há Help. É um wa-dialog sem role=dialog; o texto fica no caption (HTML) de
        um wa-text-view; fecha com o "Fechar" DESSE diálogo. Calibrado 02/10/2026."""
        texto_help = self.page.evaluate(_JS_HELP_ADVPL)
        if not texto_help:
            return None
        self.print("mensagem_protheus")
        botoes = self.page.get_by_role("button", name=re.compile(r"^\s*" + re.escape(s.HELP_FECHAR) + r"\s*$"))
        for i in range(botoes.count()):
            botao = botoes.nth(i)
            try:
                texto = botao.evaluate(_JS_TEXTO_DO_DIALOGO, s.JANELA_PRINCIPAL_CLASSE) if botao.is_visible() else None
            except PlaywrightError:
                continue
            if texto and ("Help:" in texto or "Problema:" in texto):
                botao.click()
                self.page.wait_for_timeout(ESPERA_VALIDACAO)
                break
        return texto_help

    def limpar_campo_com_foco(self) -> None:
        """Apaga o campo que está com o foco (o input fica no shadow DOM do wa-text-input). Usado só para
        DESCARTAR um formulário: o AdvPL valida o campo de novo ao sair dele (inclusive pelo Cancelar) e o Help
        de validação voltaria (visto em 05/10/2026 com 100DOCEXIS no Número Doc.)."""
        if self.page.evaluate("""() => {
              let a = document.activeElement;
              while (a && a.shadowRoot && a.shadowRoot.activeElement) a = a.shadowRoot.activeElement;
              if (!a || !['INPUT', 'TEXTAREA'].includes(a.tagName)) return false;
              a.select(); return true;
            }"""):
            self.page.keyboard.press("Delete")
            self.page.wait_for_timeout(ESPERA_VALIDACAO)

    def verificar_mensagem(self) -> None:
        """Se o Protheus abriu um diálogo de erro/help, lê o texto, tira print, fecha e levanta MensagemProtheus."""
        if texto_help := self.fechar_help():
            raise MensagemProtheus(texto_help[:500])
        dialogos = self.page.get_by_role("dialog")
        for i in range(dialogos.count()):
            dialogo = dialogos.nth(i)
            if not dialogo.is_visible():
                continue
            texto = dialogo.inner_text()
            titulo = texto.strip().splitlines()[0] if texto.strip() else ""
            if any(t.lower() in titulo.lower() for t in s.DIALOGOS_DE_ERRO):
                self.print("mensagem_protheus")
                fechar = dialogo.get_by_role("button", name=re.compile("^(OK|Fechar)$", re.I))
                if fechar.count():
                    fechar.first.click()
                raise MensagemProtheus(" ".join(texto.split())[:500])
