"""Textos e rótulos das telas do Protheus WebApp — o ÚNICO lugar com dependência de tela.

Calibrado em 02/10/2026 na homologação (TOTVS Manufatura MSSQL Teste, usuário TOTVS.RPA):
login, data base, menu, browse do Movimento Bancário, Pagar, Receber e Transferência entre C/C
(preenchidos e cancelados). ⚠ Ainda NÃO calibrados: o que acontece DEPOIS de gravar (tela contábil,
segundo Salvar do Receber) e o Novo Conciliador Backoffice.
Prefira texto visível/rótulo a ids gerados (COMPnnnn muda a cada abertura de tela).
"""
import re

# --- Login (PO-UI). Calibrado em 02/10/2026 no ambiente de homologação.
# Os ids são "po-login[<uuid>]" / "po-password[<uuid>]": o uuid muda a cada carga, por isso "começa com".
# Não usar atributos "_ngcontent-ng-c..." do Angular: mudam a cada build do Protheus.
LOGIN_USUARIO_CSS = 'input[id^="po-login"], [id^="po-login"] input'
LOGIN_SENHA_CSS = 'input[id^="po-password"], [id^="po-password"] input'
# Segunda tela (depois do Enter na senha): confirmar com o botão "Entrar"
LOGIN_ENTRAR = "Entrar"

# --- Data base (calibrado 02/10/2026): botão com a data no cabeçalho -> diálogo AdvPL
# Barra escapada de propósito: o Playwright serializa a regex no seletor e "/" crua quebra o parser
CABECALHO_DATA_BASE = re.compile(r"\d{2}\/\d{2}\/\d{4}")
DATA_BASE_CAMPO = "Data base"          # wa-text-view caption="Data base*" + wa-text-input
DATA_BASE_CONFIRMAR = "Confirmar"
AMBIENTE_FILIAL = "Filial"             # mesmo diálogo; valor esperado 010101 (MATRIZ)
# Ícone TOTVS no canto superior esquerdo: volta ao menu inicial (não tem texto/id estável; posição fixa)
ICONE_MENU_INICIAL = (22, 20)

# --- Menu lateral (wa-menu-item, caption com <u> na letra de atalho, comparado sem as tags).
# Grupos terminam com "(" (o caption real é "Movimento Bancario (5)") e casam por "começa com";
# os demais itens casam por texto EXATO — senão "Movimento Bancario" casaria com o grupo e o clique erraria.
# A busca do menu não filtra com texto colado: navegar pela árvore, como na documentação.
MENU_CAMINHO = {
    "Movimento Bancario": ("Atualizações (", "Movimento Bancario (", "Movimento Bancario"),
    "Novo Conciliador Backoffice": ("Atualizações (", "Movimento Bancario (", "Novo Conciliador Backoffice"),
}
ROTINA_MOVIMENTO_BANCARIO = "Movimento Bancario"
ROTINA_CONCILIADOR = "Novo Conciliador Backoffice"
# Ao abrir uma rotina, o Protheus pede de novo Data base/Grupo/Filial/Ambiente (mesmo diálogo da data base)

# --- Browse Movimentação Bancária (FINA100). Botões: "+Pagar", "Visualizar", "Outras Ações"
BOTAO_PAGAR = "Pagar"
BOTAO_OUTRAS_ACOES = "Outras Ações"
# Itens do popup (wa-menu-popup-item) de Outras Ações
ACAO_TRANSFERENCIA = "Transferência entre C/C"
ACAO_RECEBER = "Receber"

# --- Diálogo de filiais: NÃO aparece na homologação (a rotina já abre na filial escolhida no ambiente).
# Mantido como opcional caso a configuração do usuário de produção seja diferente.
DIALOGO_FILIAIS = "Filiais"
BOTAO_OK = "OK"

# --- Diálogo Transferência entre C/C (AdvPL; rótulos repetidos: índice 0 = Origem, 1 = Destino)
TRANSF_BANCO = "Banco"
TRANSF_AGENCIA = "Agência"
TRANSF_CONTA = "Conta"
TRANSF_NATUREZA = "Natureza"
TRANSF_DATA_CREDITO = "Data de Crédito"
TRANSF_TIPO_MOV = "Tipo Mov."
TRANSF_NUMERO_DOC = "Número Doc."
TRANSF_VALOR = "Valor"
TRANSF_HISTORICO = "Histórico"
TRANSF_BENEFICIARIO = "Beneficiário"
TRANSF_CONFIRMAR = "Ok"
TRANSF_CANCELAR = "Cancelar"

# --- Formulário MVC "Movimentação Bancária - PAGAR" / "- RECEBER" (mesmos campos; <label> + wa-text-input)
MOV_DATA = "DT Movimen"
MOV_NUMERARIO = "Numerario"
MOV_VALOR = "Vlr.Movim."
MOV_NATUREZA = "Natureza"
MOV_BANCO = "Banco"
MOV_AGENCIA = "Agencia"
MOV_CONTA = "Conta Banco"
MOV_HISTORICO = "Historico"
MOV_DOCUMENTO = "Documento"
MOV_BENEFICIARIO = "Beneficiario"
BOTAO_SALVAR = "Salvar"
BOTAO_CANCELAR = "Cancelar"

# --- Lançamento contábil exibido ~10 s após gravar (estouro on-line). Calibrado 02/10/2026 (tarifa):
# diálogo AdvPL title="Lancamentos Contabeis" com Filial/Data/Lote/Sub-Lote/Doc, grid de partidas e
# botões Outras Ações / Cancelar / Salvar. Salvar contabiliza; depois o Protheus reabre o form em branco.
CONTABIL_DIALOGO = 'wa-dialog[title="Lancamentos Contabeis"]'
CONTABIL_SALVAR = "Salvar"

# --- Novo Conciliador Backoffice (CTBA940, PO-UI num iframe). Calibrado 02/10/2026.
CONC_FRAME_URL = "ctba940"
CONC_CARREGANDO = 'div.po-overlay[aria-busy="true"]'     # overlay de "carregando" do PO-UI
CONC_MENU_CONCILIADOR = "Conciliador"                    # po-menu-item do menu lateral do Conciliador
CONC_CONFIGURACAO = "Selecione uma configuração de conciliação"   # placeholder do po-combo
CONC_CONFIGURACAO_0024 = "0024-Conciliação Bancária Manual"
CONC_VER_FILTROS = "Ver Filtros"
CONC_FILTRO_DATA_DE = "Data Dispon. de"
CONC_FILTRO_DATA_ATE = "Data Dispon. até"
CONC_FILTRO_BANCO = "Banco igual a"
CONC_FILTRO_AGENCIA = "Agencia igual a"
CONC_FILTRO_CONTA = "Conta Banco igual a"
CONC_APLICAR_FILTRO = "Aplicar"
CONC_SALDOS = "Saldos bancários"
CONC_SALDO_ATUAL = "Saldo atual (Bancário)"
CONC_FECHAR = "Fechar"
CONC_ABA_NAO_ENCONTRADOS = "Dados não Encontrados"
CONC_ABA_CONCILIACAO = "Dados da Conciliação"
CONC_CHECKBOX_LINHA = 'po-checkbox[name="checkbox"]'     # dentro: div[role=checkbox][aria-checked]
CONC_ACOES = "Ações"
CONC_CONCILIAR = "Conciliar"
CONC_APLICAR = "Aplicar Conciliação"
CONC_OK = "Ok"
CONC_MATCH_SUCESSO = "Conciliar - Execução realizada com sucesso"   # depois de Ações > Conciliar (match)
CONC_SUCESSO = "Conciliação realizada com sucesso"                 # depois de Aplicar Conciliação (efetiva)
CONC_APLICAR_PERGUNTA = "Deseja mesmo aplicar todos os dados da Conciliação?"
CONC_SALDO_CONCILIADO = "Saldo atual (Conciliado)"
CONC_TOAST = "po-toaster, .po-toaster"
CONC_SAIR = "Sair da Conciliação"
CONC_SAIR_PERGUNTA = "Deseja mesmo sair desta conciliação?"

# --- Mensagens de erro/alerta do Protheus (diálogos modais)
HELP_FECHAR = "Fechar"  # botão do diálogo de Help AdvPL
DIALOGOS_DE_ERRO = ("Help", "Atenção", "Erro", "Problema", "Aviso")
