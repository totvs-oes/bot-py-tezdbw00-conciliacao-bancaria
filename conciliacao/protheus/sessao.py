"""Sessão no Protheus WebApp: abre o Edge, faz login com Data Base = data do movimento e abre rotinas."""
from __future__ import annotations

import logging
import re
import time
from datetime import date
from pathlib import Path
from typing import Optional

from playwright.sync_api import Browser, Error as PlaywrightError, Locator, Page, Playwright, sync_playwright

from conciliacao.configuracao import Ambiente
from conciliacao.protheus import seletores as s
from conciliacao.protheus.tela import ErroDeTela, Tela

log = logging.getLogger(__name__)

TIMEOUT_CARGA_INICIAL = 120_000  # ms
ESPERA_ENTRAR_S = 45             # depois do Entrar, quanto esperar a tela 2 sumir antes de tentar de novo

# Tela 2 do login (PO-UI): códigos de Grupo/Filial/Ambiente; null = tela não está neste frame
_JS_CAMPOS_DE_AMBIENTE = """() => {
    const campos = ['company_code', 'branch_code', 'environment_code']
        .map((nome) => document.querySelector(`po-lookup[name="${nome}"] input`));
    if (campos.every((c) => !c)) return null;
    return campos.map((c) => (c ? c.value.trim() : ''));
}"""


def _sumiu(page: Page, elemento: Locator, timeout: int) -> bool:
    """True quando o elemento some. O login PO-UI fica num iframe que é descartado depois do Enter:
    iframe destruído também conta como "sumiu" (o Playwright levanta erro em vez de responder)."""
    limite = time.monotonic() + timeout / 1000
    while time.monotonic() < limite:
        try:
            if not elemento.is_visible():
                return True
        except PlaywrightError:
            return True
        page.wait_for_timeout(500)  # não usar time.sleep: congela os eventos do Playwright
    return False


class SessaoProtheus:
    def __init__(self, ambiente: Ambiente, data_base: date, filial: str, pasta_prints: Path,
                 pausar_se_falhar_login: bool = False, cdp_url: str = ""):
        """cdp_url: conecta num Chrome já aberto e LOGADO por uma pessoa (--remote-debugging-port),
        em vez de abrir um navegador e fazer login. Usado na calibração/homologação assistida."""
        self.cdp_url = cdp_url
        if not cdp_url and not (ambiente.protheus_url and ambiente.protheus_usuario and ambiente.protheus_senha):
            raise ErroDeTela("PROTHEUS_URL/PROTHEUS_USUARIO/PROTHEUS_SENHA não configurados no .env")
        self.ambiente = ambiente
        self.data_base = data_base
        self.filial = filial
        self.pasta_prints = pasta_prints
        self.pausar_se_falhar_login = pausar_se_falhar_login
        self._playwright: Playwright | None = None
        self._navegador: Browser | None = None
        self.tela: Tela | None = None

    def __enter__(self) -> "SessaoProtheus":
        self._playwright = sync_playwright().start()
        if self.cdp_url:
            return self._conectar_existente()
        # Usa o navegador já instalado na máquina ("chrome" ou "msedge"): dispensa baixar o do Playwright
        self._navegador = self._playwright.chromium.launch(channel=self.ambiente.protheus_navegador,
                                                           headless=not self.ambiente.protheus_visivel)
        contexto = self._navegador.new_context(
            viewport={"width": 1600, "height": 900}, locale="pt-BR",
            # Servidor interno com certificado autoassinado (acesso só pela VPN)
            ignore_https_errors=self.ambiente.protheus_ignorar_certificado,
        )
        self.tela = Tela(contexto.new_page(), self.pasta_prints)
        try:
            self._login()
        except ErroDeTela:
            if not self.pausar_se_falhar_login:
                self.__exit__()
                raise
            # Modo calibração: deixa o navegador aberto com o Inspector para explorar a tela
            log.exception("Login falhou; abrindo o Playwright Inspector para calibração")
            self.tela.page.pause()
        return self

    def _conectar_existente(self) -> "SessaoProtheus":
        """Assume a aba do Protheus de um Chrome já logado (o login foi feito por uma pessoa)."""
        self._navegador = self._playwright.chromium.connect_over_cdp(self.cdp_url)
        paginas = [p for c in self._navegador.contexts for p in c.pages]
        protheus = [p for p in paginas if "/webapp" in p.url or "/app-root" in p.url]
        if not protheus:
            raise ErroDeTela(f"Nenhuma aba do Protheus no Chrome em {self.cdp_url} (abas: {[p.url for p in paginas]})")
        self.tela = Tela(protheus[0], self.pasta_prints)
        log.info("Conectado na aba já logada: %s", protheus[0].url)
        self.tela.fechar_avisos(espera_ms=3_000, quieto_ms=1_000)  # aviso pós-login que a pessoa não fechou
        self.definir_data_base()
        return self

    def __exit__(self, *_) -> None:
        # Conectado num Chrome de outra pessoa: só desconecta, nunca fecha o navegador dela
        if self._navegador and not self.cdp_url:
            self._navegador.close()
        if self._playwright:
            self._playwright.stop()

    def _login(self) -> None:
        tela, page = self.tela, self.tela.page
        log.info("Login no Protheus (data base %s)", self.data_base.strftime("%d/%m/%Y"))
        page.goto(self.ambiente.protheus_url, wait_until="load", timeout=TIMEOUT_CARGA_INICIAL)
        # Tela 1 (PO-UI): usuário e senha, confirma com Enter
        usuario = tela.elemento_css(s.LOGIN_USUARIO_CSS, "campo_usuario", timeout=TIMEOUT_CARGA_INICIAL)
        tela.diagnostico("01_tela_login")  # registro para calibração; barato e ajuda quando a tela muda
        usuario.fill(self.ambiente.protheus_usuario)
        senha = tela.elemento_css(s.LOGIN_SENHA_CSS, "campo_senha")
        senha.fill(self.ambiente.protheus_senha)  # fill: a senha não passa por log nem por digitação simulada
        senha.press("Enter")

        # Tela 2: só procura o "Entrar" depois que a tela 1 sumiu (senão clicaria no botão da tela anterior)
        if not _sumiu(page, senha, TIMEOUT_CARGA_INICIAL):
            tela.diagnostico("02_login_nao_avancou")
            raise ErroDeTela("Login não avançou após o Enter (usuário/senha inválidos?)")
        self._entrar()
        self._esperar_tela_inicial()
        # Aviso(s) com "Fechar" logo após o Entrar (homologação: "base de Desenvolvimento"); sem aviso, segue
        tela.fechar_avisos()
        tela.print("03_login_ok")
        log.info("Login concluído")
        self.definir_data_base()

    def _entrar(self, tentativas: int = 3) -> None:
        """Tela 2 (PO-UI "session-settings"): Grupo/Filial/Ambiente são preenchidos pelo servidor alguns segundos depois
        de a tela aparecer. Clicar em Entrar antes disso dá "formulário inválido" e a tela não sai do lugar (visto em
        08/10/2026, depois que o login deixou de esperar a rede ociosa). Espera os códigos preenchidos antes do clique;
        se a tela continuar mesmo assim, espera de novo e repete."""
        tela = self.tela
        for tentativa in range(1, tentativas + 1):
            self._esperar_ambiente_preenchido()
            tela.print("02_tela_pos_login")
            tela.clicar(s.LOGIN_ENTRAR, timeout=TIMEOUT_CARGA_INICIAL, esperar_rede=False)
            if self._saiu_da_tela_de_ambiente(ESPERA_ENTRAR_S):
                return
            log.info("Entrar não saiu da tela de Grupo/Filial/Ambiente (tentativa %d); esperando e clicando de novo",
                     tentativa)
        caminho = tela.diagnostico("login_entrar_nao_avancou")
        raise ErroDeTela(f"A tela de Grupo/Filial/Ambiente não avançou depois de {tentativas} cliques em Entrar. "
                         f"Diagnóstico: {caminho}")

    def _campos_de_ambiente(self) -> Optional[list[str]]:
        """Valores dos códigos de Grupo/Filial/Ambiente da tela 2, ou None se a tela não está aberta."""
        for frame in self.tela.page.frames:
            try:
                valores = frame.evaluate(_JS_CAMPOS_DE_AMBIENTE)
            except PlaywrightError:
                continue  # iframe sendo trocado
            if valores is not None:
                return valores
        return None

    def _esperar_ambiente_preenchido(self) -> None:
        limite = time.monotonic() + TIMEOUT_CARGA_INICIAL / 1000
        while time.monotonic() < limite:
            valores = self._campos_de_ambiente()
            if valores is None or all(valores):  # sem os campos (outra versão da tela) ou todos preenchidos
                self.tela.page.wait_for_timeout(1000)  # o PO-UI ainda valida o formulário logo depois
                return
            self.tela.page.wait_for_timeout(500)
        caminho = self.tela.diagnostico("ambiente_nao_preenchido")
        raise ErroDeTela(f"Grupo/Filial/Ambiente não foram preenchidos na tela de login. Diagnóstico: {caminho}")

    def _saiu_da_tela_de_ambiente(self, segundos: float) -> bool:
        limite = time.monotonic() + segundos
        while time.monotonic() < limite:
            if self._campos_de_ambiente() is None or self._tela_inicial_ou_aviso():
                return True
            self.tela.page.wait_for_timeout(500)
        return False

    def _tela_inicial_ou_aviso(self) -> bool:
        tela = self.tela
        try:
            return tela.aviso_aberto() or any(
                f.get_by_role("button", name=s.CABECALHO_DATA_BASE).first.is_visible() for f in tela.page.frames)
        except PlaywrightError:
            return False  # tela sendo redesenhada / iframe destruído

    def _esperar_tela_inicial(self) -> None:
        """Depois do "Entrar": espera o aviso pós-login OU o botão de data base do cabeçalho (tela inicial).
        O aviso pode cobrir a tela inicial, por isso qualquer um dos dois serve."""
        tela = self.tela
        limite = time.monotonic() + TIMEOUT_CARGA_INICIAL / 1000
        while time.monotonic() < limite:
            if self._tela_inicial_ou_aviso():
                return
            tela.page.wait_for_timeout(500)
        caminho = tela.diagnostico("tela_inicial_nao_carregou")
        raise ErroDeTela(f"A tela inicial não carregou depois do Entrar. Diagnóstico: {caminho}")

    def definir_data_base(self) -> None:
        """Data base da sessão = data do movimento (a operadora trabalha com a data do extrato processado).

        Pelo botão de data do cabeçalho -> diálogo "Data base*" -> Confirmar. Confere o cabeçalho no final:
        sem isso os lançamentos sairiam com a data de hoje — falhar é melhor que lançar com data errada.
        """
        tela, esperado = self.tela, self.data_base.strftime("%d/%m/%Y")
        botao = tela.botao(s.CABECALHO_DATA_BASE, "botao_data_base", timeout=TIMEOUT_CARGA_INICIAL)
        if botao.inner_text().strip() == esperado:
            log.info("Data base já é %s", esperado)
            return
        botao.click()
        tela.preencher_data(s.DATA_BASE_CAMPO, self.data_base)
        tela.confirmar(s.DATA_BASE_CONFIRMAR)
        if tela.botao(s.CABECALHO_DATA_BASE, "botao_data_base").inner_text().strip() != esperado:
            caminho = tela.diagnostico("data_base_nao_alterada")
            raise ErroDeTela(f"Data base não ficou {esperado}. Diagnóstico: {caminho}")
        log.info("Data base alterada para %s", esperado)

    def abrir_rotina(self, nome: str) -> Tela:
        """Deixa a rotina na tela: volta para a aba dela se já estiver aberta; senão abre pelo menu.

        Calibrado 02/10/2026: cada rotina aberta vira uma aba no topo ("Movimento Bancario - 01/010101 [...]");
        o ícone TOTVS (canto superior esquerdo) volta ao menu; abrir a rotina pede de novo o diálogo de
        ambiente (Data base/Grupo/Filial) — a data base é conferida ali também.
        """
        tela = self.tela
        if self._rotina_ativa() == nome:
            return tela
        # Aberta mas em segundo plano: a aba (wa-tab-button) fica na barra do topo em qualquer tela.
        # Nunca abrir de novo pelo menu: isso cria uma SEGUNDA instância da rotina.
        aba = tela.page.locator("wa-tab-button").filter(has_text=re.compile(r"^\s*" + re.escape(nome) + " - "))
        if aba.count():
            aba.first.click()
            tela.esperar_ocioso()
            self._conferir_rotina_ativa(nome)
            return tela

        tela.page.mouse.click(*s.ICONE_MENU_INICIAL)
        tela.esperar_ocioso()
        caminho = s.MENU_CAMINHO[nome]
        for i, passo in enumerate(caminho):
            proximo = caminho[i + 1] if i + 1 < len(caminho) else None
            if proximo and tela.item_de_menu_visivel(proximo):
                continue  # submenu já expandido: clicar de novo recolheria
            self._clicar_grupo(passo, proximo)
        self._confirmar_ambiente()
        self._conferir_rotina_ativa(nome)
        self._cancelar_dialogo_moedas()
        log.info("Rotina '%s' aberta", nome)
        return tela

    def _cancelar_dialogo_moedas(self, espera_ms: int = 5_000) -> None:
        """Diálogo "Moedas" (cotações Dólar/UFIR/Euro/Iene/taxa de juros) que o Protheus abre junto com a rotina quando a
        data base não tem cotação cadastrada (homologação, 14/09/2026). Cancelar: não grava nada; Confirmar gravaria
        cotação zero na tabela de moedas. Em produção a cliente cadastra as cotações; sem o diálogo, segue."""
        tela = self.tela
        dialogo = tela.page.locator("wa-dialog.dict-msdialog").filter(
            has=tela.page.locator(f'wa-text-view[caption="{s.DIALOGO_MOEDAS}"]'))
        limite = time.monotonic() + espera_ms / 1000
        while time.monotonic() < limite:
            try:
                if dialogo.count() and dialogo.first.is_visible():
                    tela.print("dialogo_moedas")
                    dialogo.first.locator(f'wa-button[title="{s.DIALOGO_MOEDAS_CANCELAR}"] button').click()
                    tela.page.wait_for_timeout(1500)
                    log.warning("Diálogo '%s' (cotações da data base não cadastradas) cancelado", s.DIALOGO_MOEDAS)
                    return
            except PlaywrightError:
                pass  # tela sendo redesenhada
            tela.page.wait_for_timeout(500)

    def _clicar_grupo(self, passo: str, proximo: Optional[str], tentativas: int = 3) -> None:
        """Clica no item do menu e, se for um grupo, confere que ele expandiu (o próximo passo apareceu).
        Logo após o login a tela inicial ainda carrega e o Protheus ignora o clique: o grupo fica destacado
        mas não abre (visto em 05/10/2026). Só clica de novo se o próximo item NÃO apareceu — clicar num
        grupo aberto o recolheria."""
        tela = self.tela
        for tentativa in range(1, tentativas + 1):
            tela.clicar_menu(passo)
            if proximo is None:
                return
            limite = time.monotonic() + 5
            while time.monotonic() < limite:
                if tela.item_de_menu_visivel(proximo):
                    return
                tela.page.wait_for_timeout(500)
            log.info("Menu '%s' não expandiu (tentativa %d); clicando de novo", passo, tentativa)
        caminho = tela.diagnostico(f"menu_nao_expandiu_{passo}")
        raise ErroDeTela(f"O menu '{passo}' não expandiu depois de {tentativas} cliques. Diagnóstico: {caminho}")

    def _rotina_ativa(self) -> str:
        """Nome da rotina da aba ativa ("Movimento Bancario - 01/010101 [...]" -> "Movimento Bancario")."""
        legendas = self.tela.page.evaluate(
            """() => Array.from(document.querySelectorAll('wa-tab-page[active]'))
                     .map((e) => e.getAttribute('caption') || '').filter((c) => c.includes(' - ') && c.includes('['))""")
        return legendas[0].split(" - ")[0].strip() if legendas else ""

    def _conferir_rotina_ativa(self, nome: str) -> None:
        """Trava de segurança: nunca preencher nada numa rotina diferente da pedida.
        Espera a aba ativar (rotinas PO-UI como o Conciliador levam alguns segundos)."""
        limite = time.monotonic() + 60
        while (ativa := self._rotina_ativa()) != nome and time.monotonic() < limite:
            self.tela.page.wait_for_timeout(1000)
        if ativa != nome:
            caminho = self.tela.diagnostico("rotina_errada")
            raise ErroDeTela(f"Esperava a rotina '{nome}' na tela, mas a ativa é '{ativa or 'nenhuma'}'. "
                             f"Diagnóstico: {caminho}")

    def _confirmar_ambiente(self) -> None:
        """Diálogo Data base/Grupo/Filial/Ambiente que aparece ao abrir uma rotina."""
        tela = self.tela
        campo = tela.campo(s.DATA_BASE_CAMPO, timeout=TIMEOUT_CARGA_INICIAL)
        esperado = self.data_base.strftime("%d/%m/%Y")
        if campo.input_value().strip() != esperado:
            tela.preencher_data(s.DATA_BASE_CAMPO, self.data_base)
        if tela.campo(s.AMBIENTE_FILIAL).input_value().strip() != self.filial:
            tela.preencher(s.AMBIENTE_FILIAL, self.filial)
        tela.confirmar(s.DATA_BASE_CONFIRMAR)
