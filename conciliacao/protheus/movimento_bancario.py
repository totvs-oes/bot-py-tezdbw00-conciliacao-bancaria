"""Rotinas 2, 3 e 4: Movimento Bancário (FINA100). Telas calibradas em 02/10/2026 na homologação.

Cada função recebe `antes_de_salvar`: o executor marca o item como "salvando" no registro antes do clique
final. Se algo falhar depois disso, o item fica "incerto" e não é refeito automaticamente.

ensaio=True: preenche tudo, confere o que o Protheus aceitou e CANCELA em vez de gravar.
"""
from __future__ import annotations

import logging
import time
from datetime import date
from decimal import Decimal
from typing import Callable, Optional

from conciliacao.modelos import Conta, Rendimento, Tarifa, Transferencia, formatar_brl
from conciliacao.protheus import seletores as s
from conciliacao.protheus.sessao import SessaoProtheus
from conciliacao.protheus.tela import ErroDeTela, Tela

log = logging.getLogger(__name__)

ESPERA_POS_GRAVAR = 45  # s: a tela contábil apareceu ~10 s depois do Salvar na homologação
ESPERA_REABERTURA = 15  # s: o formulário em branco reabre alguns segundos depois da tela contábil


def _conferir(tela: Tela, esperado: list[tuple[str, int, object]]) -> None:
    """Relê cada campo depois de preenchido: o Protheus pode ter trocado o valor (gatilho, máscara, F3)."""
    divergencias = []
    for rotulo, indice, valor in esperado:
        lido = tela.campo(rotulo, indice).input_value().strip()
        if isinstance(valor, date):
            ok = lido == valor.strftime("%d/%m/%Y")
        elif isinstance(valor, Decimal):
            ok = lido in (formatar_brl(valor), formatar_brl(valor).replace(".", ""))
        else:
            ok = lido.upper() == str(valor).strip().upper()
        if not ok:
            divergencias.append(f"{rotulo}#{indice}: esperado {valor!s}, tela {lido!r}")
    if divergencias:
        caminho = tela.diagnostico("campos_divergentes")
        raise ErroDeTela("Campos diferentes do esperado: " + "; ".join(divergencias) + f". Diagnóstico: {caminho}")


def _preencher(tela: Tela, campos: list[tuple[str, int, object]]) -> None:
    for rotulo, indice, valor in campos:
        if isinstance(valor, date):
            tela.preencher_data(rotulo, valor, indice)
        elif isinstance(valor, Decimal):
            tela.preencher_valor(rotulo, valor, indice)
        else:
            tela.preencher(rotulo, str(valor), indice)
    _conferir(tela, campos)


def _gravar(tela: Tela, botao: str, rotulo_historico: str, historico: str, cancelar: str) -> None:
    """Clica em gravar e trata o que o Protheus faz depois. Calibrado em 02/10/2026 (tarifa, homologação):

        Salvar -> formulário fecha (~2 s) -> diálogo "Lancamentos Contabeis" (~10 s) -> Salvar nele
               -> o Protheus reabre um formulário EM BRANCO (inclusão contínua) -> Cancelar

    Nunca repete o clique se ele pode ter gravado: só clica de novo se o formulário continua aberto
    COM os nossos dados (= o Protheus ignorou o clique enquanto validava o último campo).
    """
    for tentativa in range(1, 4):
        tela.botao(botao, f"botao_{botao}").click()
        inicio = time.monotonic()
        while time.monotonic() - inicio < ESPERA_POS_GRAVAR:
            tela.page.wait_for_timeout(1000)
            tela.verificar_mensagem()  # help/erro do Protheus -> MensagemProtheus (item vira "incerto")
            contabil = tela.page.locator(s.CONTABIL_DIALOGO)
            if contabil.count() and contabil.first.is_visible():
                tela.print("lancamento_contabil")
                contabil.first.get_by_role("button", name=s.CONTABIL_SALVAR).first.click()
                _esperar_sumir(tela, contabil.first, "Lancamentos Contabeis")
                _cancelar_formulario_em_branco(tela, rotulo_historico, cancelar)
                return
            ainda_preenchido = tela.valor_do_campo(rotulo_historico) == historico
            if ainda_preenchido and time.monotonic() - inicio > 8:
                break  # clique ignorado: formulário intacto, nada gravado -> pode clicar de novo
        else:
            # Formulário fechou e não veio tela contábil em ESPERA_POS_GRAVAR: gravou sem contabilização on-line
            log.warning("Gravado sem tela de lançamento contábil em %ss", ESPERA_POS_GRAVAR)
            _cancelar_formulario_em_branco(tela, rotulo_historico, cancelar)
            return
        log.info("'%s' ignorado pelo Protheus (tentativa %d); formulário intacto, clicando de novo", botao, tentativa)
    caminho = tela.diagnostico("gravar_sem_efeito")
    raise ErroDeTela(f"'{botao}' clicado 3 vezes sem efeito (formulário continua preenchido). Diagnóstico: {caminho}")


def _esperar_sumir(tela: Tela, elemento, descricao: str, timeout: int = 60) -> None:
    limite = time.monotonic() + timeout
    while time.monotonic() < limite:
        tela.page.wait_for_timeout(1000)
        tela.verificar_mensagem()
        try:
            if not elemento.is_visible():
                return
        except Exception:
            return  # componente destruído junto com o diálogo
    caminho = tela.diagnostico(f"nao_fechou_{descricao}")
    raise ErroDeTela(f"'{descricao}' não fechou depois de salvar. Diagnóstico: {caminho}")


def _cancelar_formulario_em_branco(tela: Tela, rotulo_historico: str, cancelar: str) -> None:
    """Depois de gravar, o Protheus reabre o formulário vazio para o próximo lançamento: fechar.
    A reabertura pode demorar vários segundos depois da tela contábil (visto na transferência em 02/10/2026:
    o diálogo em branco ficou aberto e bloqueou o "Outras Ações" do item seguinte), então espera com folga."""
    limite = time.monotonic() + ESPERA_REABERTURA
    while time.monotonic() < limite:
        tela.page.wait_for_timeout(1000)
        valor = tela.valor_do_campo(rotulo_historico)
        if valor == "":
            _fechar_em_branco(tela, rotulo_historico, cancelar)
            return
        if valor:
            tela.diagnostico("pos_gravar_inesperado")  # conteúdo inesperado: não mexer, registrar
            return


def _fechar_em_branco(tela: Tela, rotulo_historico: str, cancelar: str) -> None:
    for _ in range(3):
        tela.confirmar(cancelar)
        tela.page.wait_for_timeout(1000)
        if tela.valor_do_campo(rotulo_historico) is None:
            return
    caminho = tela.diagnostico("formulario_em_branco_nao_fechou")
    raise ErroDeTela(f"Formulário em branco não fechou com '{cancelar}'. Diagnóstico: {caminho}")


def _fechar_formulario_pendente(tela: Tela) -> None:
    """Antes de cada lançamento: um formulário de inclusão aberto bloqueia o browse (popup/+Pagar não
    respondem). Vazio -> fecha; com dados -> para (pode ser lançamento pela metade: não arriscar)."""
    for rotulo, cancelar in ((s.TRANSF_HISTORICO, s.TRANSF_CANCELAR), (s.MOV_HISTORICO, s.BOTAO_CANCELAR)):
        valor = tela.valor_do_campo(rotulo)
        if valor == "":
            log.info("Formulário em branco aberto na tela (%s): fechando", rotulo)
            _fechar_em_branco(tela, rotulo, cancelar)
        elif valor:
            caminho = tela.diagnostico("formulario_aberto_com_dados")
            raise ErroDeTela(f"Há um formulário aberto com dados (histórico {valor!r}). Diagnóstico: {caminho}")


def descartar_formulario(tela: Tela, historico: Optional[str] = None) -> bool:
    """Cancela o formulário de inclusão aberto SEM gravar. Com `historico`: só se ele for o NOSSO lançamento
    (depois de um Help, formulário intacto = nada gravado). Sem: qualquer formulário aberto (usar só quando
    o item ainda não chegou ao gravar). Devolve True se cancelou."""
    for rotulo, cancelar in ((s.TRANSF_HISTORICO, s.TRANSF_CANCELAR), (s.MOV_HISTORICO, s.BOTAO_CANCELAR)):
        valor = tela.valor_do_campo(rotulo)
        if valor is not None and (historico is None or valor == historico):
            _fechar_em_branco(tela, rotulo, cancelar)
            return True
    return False


def _campos_conta(conta: Conta, indice: int, natureza: str) -> list[tuple[str, int, object]]:
    return [(s.TRANSF_BANCO, indice, conta.banco), (s.TRANSF_AGENCIA, indice, conta.agencia),
            (s.TRANSF_CONTA, indice, conta.numero), (s.TRANSF_NATUREZA, indice, natureza)]


def incluir_transferencia(sessao: SessaoProtheus, item: Transferencia, constantes: dict,
                          antes_de_salvar: Callable[[], None], ensaio: bool = False) -> None:
    tela = sessao.abrir_rotina(s.ROTINA_MOVIMENTO_BANCARIO)
    _fechar_formulario_pendente(tela)
    tela.acao_do_popup(s.BOTAO_OUTRAS_ACOES, s.ACAO_TRANSFERENCIA)
    tela.selecionar_filial(constantes["filial"])

    natureza = constantes["natureza_transferencia"]
    _preencher(tela, [
        *_campos_conta(item.origem, 0, natureza),
        *_campos_conta(item.destino, 1, natureza),
        (s.TRANSF_DATA_CREDITO, 0, item.data),
        (s.TRANSF_TIPO_MOV, 0, constantes["tipo_mov_transferencia"]),
        (s.TRANSF_NUMERO_DOC, 0, item.documento),
        (s.TRANSF_VALOR, 0, item.valor),
        (s.TRANSF_HISTORICO, 0, item.historico),
        (s.TRANSF_BENEFICIARIO, 0, constantes["beneficiario_transferencia"]),
    ])
    tela.print(f"transf_{item.origem.banco}_{item.documento}_preenchido")
    if ensaio:
        tela.confirmar(s.TRANSF_CANCELAR)
        return

    antes_de_salvar()
    _gravar(tela, s.TRANSF_CONFIRMAR, s.TRANSF_HISTORICO, item.historico, s.TRANSF_CANCELAR)
    tela.print(f"transf_{item.origem.banco}_{item.documento}_gravado")


def _campos_movimento(item: Tarifa, natureza: str, numerario: str, beneficiario: str = "") -> list:
    campos = [(s.MOV_DATA, 0, item.data), (s.MOV_NUMERARIO, 0, numerario), (s.MOV_VALOR, 0, item.valor),
              (s.MOV_NATUREZA, 0, natureza), (s.MOV_BANCO, 0, item.conta.banco),
              (s.MOV_AGENCIA, 0, item.conta.agencia), (s.MOV_CONTA, 0, item.conta.numero),
              (s.MOV_HISTORICO, 0, item.historico), (s.MOV_DOCUMENTO, 0, item.documento)]
    if beneficiario:
        campos.append((s.MOV_BENEFICIARIO, 0, beneficiario))
    return campos


def incluir_tarifa(sessao: SessaoProtheus, item: Tarifa, constantes: dict,
                   antes_de_salvar: Callable[[], None], ensaio: bool = False) -> None:
    tela = sessao.abrir_rotina(s.ROTINA_MOVIMENTO_BANCARIO)
    _fechar_formulario_pendente(tela)
    for tentativa in range(1, 4):  # o clique em "+Pagar" às vezes é ignorado logo após a ação anterior
        tela.clicar(s.BOTAO_PAGAR)
        tela.selecionar_filial(constantes["filial"])
        try:
            tela.campo(s.MOV_DATA, timeout=10_000)
            break
        except ErroDeTela:
            if tentativa == 3:
                raise
    _preencher(tela, _campos_movimento(item, constantes["natureza_tarifa"], constantes["numerario"]))
    tela.print(f"tarifa_{item.conta.banco}_{item.documento}_preenchido")
    if ensaio:
        tela.confirmar(s.BOTAO_CANCELAR)
        return

    antes_de_salvar()
    _gravar(tela, s.BOTAO_SALVAR, s.MOV_HISTORICO, item.historico, s.BOTAO_CANCELAR)
    tela.print(f"tarifa_{item.conta.banco}_{item.documento}_gravado")


def incluir_rendimento(sessao: SessaoProtheus, item: Rendimento, constantes: dict,
                       antes_de_salvar: Callable[[], None], ensaio: bool = False) -> None:
    tela = sessao.abrir_rotina(s.ROTINA_MOVIMENTO_BANCARIO)
    _fechar_formulario_pendente(tela)
    tela.acao_do_popup(s.BOTAO_OUTRAS_ACOES, s.ACAO_RECEBER)
    tela.selecionar_filial(constantes["filial"])
    _preencher(tela, _campos_movimento(item, constantes["natureza_rendimento"], constantes["numerario"],
                                       constantes["beneficiario_rendimento"]))
    tela.print(f"rend_{item.conta.banco}_{item.documento}_preenchido")
    if ensaio:
        tela.confirmar(s.BOTAO_CANCELAR)
        return

    antes_de_salvar()
    # O "salvar duas vezes" da documentação é o Salvar do formulário + o Salvar da tela contábil
    _gravar(tela, s.BOTAO_SALVAR, s.MOV_HISTORICO, item.historico, s.BOTAO_CANCELAR)
    tela.print(f"rend_{item.conta.banco}_{item.documento}_gravado")
