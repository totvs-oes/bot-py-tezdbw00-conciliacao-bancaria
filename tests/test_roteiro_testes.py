"""Roteiro de testes automático (roteiro_testes/): planilha MIT045, escolha dos prints e leitura do relatório."""
import os
import time
import zipfile
from datetime import date, datetime, timedelta
from pathlib import Path

from roteiro_testes.etapas import _status_dos_itens, _tabela
from roteiro_testes.evidencias import Evidencia, recorte, selecionar_prints
from roteiro_testes.mit045 import AJUSTE, EXITO, Numeracao, Roteiro, gravar
from roteiro_testes.xlsx import PastaDeTrabalho

STRINGS = ["001 - Leitura de extratos", "004 - Lançamentos no Movimento Bancário", "Leitura de extratos",
           "Lançamentos no Movimento Bancário", "1.0", "Primeira versão"]


def _aba(linhas: str) -> str:
    return ('<?xml version="1.0" encoding="UTF-8"?><worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/'
            f'2006/main"><sheetData>{linhas}</sheetData><autoFilter ref="$B$5:$T$7"/></worksheet>')


def _planilha(caminho: Path) -> Path:
    """MIT045 mínimo: Início, Cenários (001 e 004) e Roteiro (0011 e 0042), sem a aba Evidências."""
    s = lambda i: f't="s"><v>{i}</v>'   # noqa: E731
    partes = {
        "[Content_Types].xml": '<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/'
                               'content-types"></Types>',
        "xl/workbook.xml": '<?xml version="1.0"?><workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/'
                           'main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets>'
                           '<sheet name="Início" sheetId="2" r:id="rId1"/><sheet name="Cenários" sheetId="4" r:id="rId2"/>'
                           '<sheet name="Roteiro" sheetId="5" r:id="rId3"/></sheets><calcPr/></workbook>',
        "xl/_rels/workbook.xml.rels": '<?xml version="1.0"?><Relationships xmlns="http://schemas.openxmlformats.org/'
                                      'package/2006/relationships">' + "".join(
            f'<Relationship Id="rId{i}" Type="x" Target="worksheets/sheet{i}.xml"/>' for i in (1, 2, 3)) + '</Relationships>',
        "xl/styles.xml": '<?xml version="1.0"?><styleSheet><cellXfs count="2"><xf numFmtId="0"/><xf numFmtId="14"/>'
                         '</cellXfs></styleSheet>',
        "xl/sharedStrings.xml": '<?xml version="1.0"?><sst>' + "".join(f"<si><t>{t}</t></si>" for t in STRINGS) + "</sst>",
        "xl/worksheets/sheet1.xml": _aba(f'<row r="15"><c r="C15"/></row><row r="16"><c r="C16" {s(4)}</c></row>'
                                         f'<row r="18"><c r="C18" {s(5)}</c></row>'),
        "xl/worksheets/sheet2.xml": _aba(f'<row r="6"><c r="B6" t="inlineStr"><is><t>001</t></is></c><c r="C6" {s(2)}</c>'
                                         f'</row><row r="7"><c r="B7" t="inlineStr"><is><t>004</t></is></c>'
                                         f'<c r="C7" {s(3)}</c></row>'),
        "xl/worksheets/sheet3.xml": _aba(
            f'<row r="6"><c r="B6" t="str"><f>CONCATENATE(LEFT(C6,3),D6)</f><v>0011</v></c><c r="C6" {s(0)}</c>'
            '<c r="D6" t="inlineStr"><is><t>1</t></is></c><c r="L6" s="1"><v>46302</v></c></row>'
            f'<row r="7"><c r="B7" t="str"><f>CONCATENATE(LEFT(C7,3),D7)</f><v>0042</v></c><c r="C7" {s(1)}</c>'
            '<c r="D7" t="inlineStr"><is><t>2</t></is></c></row>'
            '<row r="8"><c r="B8" t="str"><f>CONCATENATE(LEFT(C8,3),D8)</f><v></v></c></row><row r="9"/>'),
    }
    with zipfile.ZipFile(caminho, "w") as z:
        for nome, texto in partes.items():
            z.writestr(nome, texto)
    return caminho


def test_numeracao_continua_a_da_planilha(tmp_path):
    numeracao = Numeracao.da_planilha(_planilha(tmp_path / "mit045.xlsx"))
    assert numeracao.reservar(Roteiro("001", "", "", "", "", EXITO, "")).codigo == "0012"
    assert numeracao.reservar(Roteiro("004", "", "", "", "", EXITO, "")).codigo == "0043"
    assert numeracao.reservar(Roteiro("006", "", "", "", "", EXITO, "")).codigo == "0061"
    assert numeracao.reservar(Roteiro("001", "", "", "", "", EXITO, "")).codigo == "0013"


def test_gravar_acrescenta_sem_alterar_o_que_existe(tmp_path):
    origem = _planilha(tmp_path / "mit045.xlsx")
    numeracao = Numeracao.da_planilha(origem)
    roteiros = [numeracao.reservar(Roteiro("001", "Leitura", "Extratos de 15/09", "desc", "esperado", EXITO, "obs",
                                           [Evidencia("0012_leitura.png", "Leitura dos PDFs")])),
                numeracao.reservar(Roteiro("006", "Conciliação", "Saldos de 15/09", "desc", "esperado", AJUSTE, "obs 2",
                                           [Evidencia("0061_01_a.png", "a"), Evidencia("0061_02_b.png", "b")]))]
    destino = gravar(origem, tmp_path / "saida.xlsx", roteiros, date(2026, 10, 9), "Fulano", "testes de 15/09")
    with zipfile.ZipFile(origem) as z:
        original = z.read("xl/worksheets/sheet3.xml")

    with PastaDeTrabalho(destino, None) as livro:
        rot = livro.aba("Roteiro")
        assert rot.texto("C6") == "001 - Leitura de extratos" and rot.texto("B6") == "0011"   # intacto
        assert [rot.texto(f"C{r}") for r in (8, 9)] == ["001 - Leitura de extratos", "006 - Conciliação bancária"]
        assert [rot.texto(f"D{r}") for r in (8, 9)] == ["2", "1"]
        assert rot.texto("B8") == "0012" and rot.texto("O9") == AJUSTE and rot.texto("J8") == "Fulano"
        assert rot.texto("T9") == "0061_01_a.png\n0061_02_b.png"
        assert rot.texto("L8") == str(46304)                       # data em formato Excel
        assert '<autoFilter ref="$B$5:$T$9"/>' in rot.xml
        cen = livro.aba("Cenários")
        assert (cen.texto("B8"), cen.texto("C8")) == ("006", "Conciliação bancária")         # cenário criado
        evi = livro.aba("Evidências")                                                          # aba criada
        assert [evi.texto(f"D{r}") for r in (5, 6, 7)] == ["0012_leitura.png", "0061_01_a.png", "0061_02_b.png"]
        assert evi.texto("B7") == "0061"
        ini = livro.aba("Início")
        assert ini.texto("C16") == "1.1" and ini.texto("C15") == "09/10/2026"
        assert ini.texto("C18").startswith("Primeira versão | 1.1 (09/10/2026): testes de 15/09")
    with zipfile.ZipFile(origem) as z:
        assert z.read("xl/worksheets/sheet3.xml") == original                                   # origem não muda


def test_selecao_de_prints_da_execucao(tmp_path):
    antigo = tmp_path / "tarifa_341_100926_gravado.png"
    antigo.write_bytes(b"x")
    os.utime(antigo, (time.time() - 3600, time.time() - 3600))   # de outra execução
    for nome in ("01_tela_login.png", "03_login_ok.png", "aviso_protheus_1.png",
                 "tarifa_341_150926_preenchido.png", "tarifa_341_150926_gravado.png",
                 "tarifa_341_1509261_preenchido.png", "tarifa_341_1509261_gravado.png",
                 "transf_237_150926_preenchido.png", "lancamento_contabil.png", "mensagem_protheus.png",
                 "conciliador_abc_cc_saldos.png", "conciliador_santander_caucao_376_aplicado.png"):
        (tmp_path / nome).write_bytes(b"x")
    selecao = selecionar_prints(tmp_path, datetime.now() - timedelta(minutes=5))
    assert [p.name for p, _ in selecao.login] == ["01_tela_login.png", "aviso_protheus_1.png", "03_login_ok.png"]
    assert [p.name for p, _ in selecao.lancamentos] == [
        "tarifa_341_150926_preenchido.png", "tarifa_341_150926_gravado.png", "transf_237_150926_preenchido.png",
        "lancamento_contabil.png"]
    assert [p.name for p, _ in selecao.falhas] == ["mensagem_protheus.png"]
    assert {p.name for p, _ in selecao.conciliacao} == {"conciliador_santander_caucao_376_aplicado.png",
                                                        "conciliador_abc_cc_saldos.png"}


RELATORIO = """# Conciliação
## Transferências (Rotina 2)

| Status | Documento | Contas | Valor | Histórico | Origem | Obs. |
|---|---|---|---:|---|---|---|
| concluido | 150926 | a → b | 1,00 | X | f#1 |  |
| erro | 1509261 | a → c | 2,00 | Y | f#2 | Protheus recusou |

## Tarifas (Rotina 3)

| Status | Documento | Contas | Valor | Histórico | Origem | Obs. |
|---|---|---|---:|---|---|---|
| concluido | 150926 | a | 0,89 | T | f#3 |  |

## Conciliações (Rotina 5)

| Conta | Saldo do extrato | Status | Detalhe |
|---|---:|---|---|
| abc_cc | 1,00 | divergente | dif |
| santander_caucao_376 | 0,00 | conciliado |  |

## Pendências (ação humana)
"""


def test_leitura_do_relatorio():
    assert _status_dos_itens(RELATORIO) == {"concluido": 2, "erro": 1}
    conciliacoes = _tabela(recorte(RELATORIO, "## Conciliações", "## Pendências"))
    assert [l[2] for l in conciliacoes] == ["divergente", "conciliado"]
