"""Edição de .xlsx direto no XML (sem Excel e sem openpyxl).

openpyxl descarta gráficos e imagens ao salvar; o modelo MIT045 tem os dois (Painel, Curva S). Aqui o arquivo é
aberto como zip, só as células tocadas mudam e todo o resto (gráficos, imagens, validações, estilos) é copiado igual.
"""
from __future__ import annotations

import html
import re
import shutil
import tempfile
import zipfile
from datetime import date
from pathlib import Path
from typing import Optional
from xml.sax.saxutils import escape


def serial(d: date) -> int:
    """Data no formato numérico do Excel."""
    return (d - date(1899, 12, 30)).days


def numero_da_coluna(coluna: str) -> int:
    n = 0
    for letra in coluna:
        n = n * 26 + ord(letra) - 64
    return n


class Estilos:
    """cellXfs do styles.xml; clona estilos acrescentando quebra de texto (texto longo nas células)."""

    def __init__(self, xml: str):
        self.xml = xml
        bloco = re.search(r'<cellXfs count="\d+">(.*?)</cellXfs>', xml, re.S).group(1)
        self.xfs = re.findall(r"<xf\b[^>]*/>|<xf\b[^>]*>.*?</xf>", bloco, re.S)
        self._quebra: dict[int, int] = {}

    def quebra(self, s: int) -> int:
        if s not in self._quebra:
            xf = re.sub(r"<alignment\b[^>]*/>", "", self.xfs[s])
            alinhamento = '<alignment vertical="top" wrapText="1"/>'
            if xf.endswith("/>"):
                xf = xf[:-2] + f' applyAlignment="1">{alinhamento}</xf>'
            else:
                xf = xf.replace(">", f">{alinhamento}", 1)
                if "applyAlignment" not in xf.split(">", 1)[0]:
                    xf = xf.replace("<xf ", '<xf applyAlignment="1" ', 1)
            self.xfs.append(xf)
            self._quebra[s] = len(self.xfs) - 1
        return self._quebra[s]

    def salvar(self) -> str:
        return re.sub(r'<cellXfs count="\d+">.*?</cellXfs>',
                      f'<cellXfs count="{len(self.xfs)}">{"".join(self.xfs)}</cellXfs>', self.xml, count=1, flags=re.S)


class Aba:
    """Uma planilha (worksheet). Endereços no formato "C25"."""

    def __init__(self, xml: str, estilos: Estilos, compartilhadas: list[str]):
        self.xml, self.estilos, self.compartilhadas = xml, estilos, compartilhadas

    # ---- leitura -------------------------------------------------------------------------------------------------
    def _linha(self, r: int) -> Optional[re.Match]:
        return re.search(r'<row r="%d"(?:\s[^>]*?)?(?:/>|>.*?</row>)' % r, self.xml, re.S)

    def _celula(self, ref: str) -> tuple[Optional[re.Match], Optional[re.Match]]:
        linha = self._linha(int(re.sub(r"[A-Z]", "", ref)))
        if not linha:
            return None, None
        return linha, re.search(r'<c r="%s"(?:\s[^>]*?)?(?:/>|>.*?</c>)' % ref, linha.group(0), re.S)

    def texto(self, ref: str) -> Optional[str]:
        """Valor exibido da célula como texto (string compartilhada, inline ou número); None se vazia."""
        _, c = self._celula(ref)
        if not c:
            return None
        celula = c.group(0)
        tipo = re.search(r'\st="(\w+)"', celula.split(">", 1)[0])
        tipo = tipo.group(1) if tipo else None
        if tipo == "inlineStr":
            valor = "".join(re.findall(r"<t[^>]*>(.*?)</t>", celula, re.S))
            return html.unescape(valor) or None
        v = re.search(r"<v>(.*?)</v>", celula, re.S)
        if not v or v.group(1) == "":
            return None
        if tipo == "s":
            return self.compartilhadas[int(v.group(1))] or None
        return html.unescape(v.group(1))

    def estilo(self, ref: str) -> int:
        _, c = self._celula(ref)
        s = re.search(r'\ss="(\d+)"', c.group(0)) if c else None
        return int(s.group(1)) if s else 0

    def ultima_linha(self, coluna: str, inicio: int) -> int:
        """Última linha preenchida em sequência a partir de `inicio` (inclusive)."""
        r = inicio
        while self.texto(f"{coluna}{r + 1}") is not None:
            r += 1
        return r

    # ---- escrita -------------------------------------------------------------------------------------------------
    def _garantir_linha(self, r: int) -> None:
        # Linha vazia exportada pelo Google Sheets como <row .../>: abre
        self.xml = re.sub(r'(<row r="%d"(?:\s[^>]*?)?)/>' % r, r"\1></row>", self.xml, count=1)
        if self._linha(r):
            return
        nova = f'<row r="{r}"></row>'
        depois = next((m for m in re.finditer(r'<row r="(\d+)"', self.xml) if int(m.group(1)) > r), None)
        if depois:
            self.xml = self.xml[:depois.start()] + nova + self.xml[depois.start():]
        elif "<sheetData/>" in self.xml:
            self.xml = self.xml.replace("<sheetData/>", f"<sheetData>{nova}</sheetData>", 1)
        else:
            self.xml = self.xml.replace("</sheetData>", f"{nova}</sheetData>", 1)

    def definir(self, ref: str, valor, modelo: Optional[str] = None, quebra: bool = False) -> None:
        """Grava o valor. O estilo vem da célula-modelo (ex.: mesma coluna da última linha preenchida) ou da própria."""
        r = int(re.sub(r"[A-Z]", "", ref))
        self._garantir_linha(r)
        s = self.estilo(modelo or ref)
        if quebra:
            s = self.estilos.quebra(s)
        if valor is None or valor == "":
            nova = f'<c r="{ref}" s="{s}"/>'
        elif isinstance(valor, date):
            nova = f'<c r="{ref}" s="{s}"><v>{serial(valor)}</v></c>'
        elif isinstance(valor, (int, float)):
            nova = f'<c r="{ref}" s="{s}"><v>{valor}</v></c>'
        else:
            nova = f'<c r="{ref}" s="{s}" t="inlineStr"><is><t xml:space="preserve">{escape(str(valor))}</t></is></c>'
        linha, c = self._celula(ref)
        texto = linha.group(0)
        if c:
            texto = texto[:c.start()] + nova + texto[c.end():]
        else:
            coluna = numero_da_coluna(re.sub(r"\d", "", ref))
            pos = next((m.start() for m in re.finditer(r'<c r="([A-Z]+)\d+"', texto)
                        if numero_da_coluna(m.group(1)) > coluna), None)
            if pos is None:
                pos = texto.rindex("</row>")
            texto = texto[:pos] + nova + texto[pos:]
        self.xml = self.xml[:linha.start()] + texto + self.xml[linha.end():]

    def valor_calculado(self, ref: str, valor: str) -> None:
        """Atualiza o valor guardado de uma célula de fórmula (para quem abre a planilha sem recalcular)."""
        linha, c = self._celula(ref)
        if not c or "<f" not in c.group(0):
            return
        celula = c.group(0)
        nova = re.sub(r"<v>.*?</v>|<v/>", f"<v>{escape(valor)}</v>", celula, count=1, flags=re.S)
        if "<v" not in celula:
            nova = celula.replace("</c>", f"<v>{escape(valor)}</v></c>")
        texto = linha.group(0).replace(celula, nova, 1)
        self.xml = self.xml[:linha.start()] + texto + self.xml[linha.end():]

    def altura(self, r: int, pontos: float) -> None:
        self._garantir_linha(r)
        linha = self._linha(r)
        cabecalho = linha.group(0).split(">", 1)[0]
        if ' ht="' in cabecalho:
            novo = re.sub(r' ht="[\d.]+"', f' ht="{pontos}"', cabecalho, count=1)
        else:
            novo = cabecalho.rstrip("/") + f' ht="{pontos}" customHeight="1"'
        self.xml = self.xml[:linha.start()] + novo + self.xml[linha.start() + len(cabecalho):]

    def filtro_ate(self, ultima_coluna: str, ultima_linha: int) -> None:
        """Estende o autofiltro existente até a linha informada."""
        self.xml = re.sub(r'<autoFilter ref="(\$?[A-Z]+\$?\d+):\$?[A-Z]+\$?\d+"/>',
                          lambda m: f'<autoFilter ref="{m.group(1)}:${ultima_coluna}${ultima_linha}"/>', self.xml, count=1)


class PastaDeTrabalho:
    """Arquivo .xlsx aberto para edição. Use como contexto: salva em `destino` ao sair sem erro
    (destino None = só leitura)."""

    def __init__(self, origem: Path, destino: Optional[Path]):
        self.origem, self.destino = Path(origem), Path(destino) if destino else None
        self._pasta = Path(tempfile.mkdtemp())
        with zipfile.ZipFile(self.origem) as z:
            z.extractall(self._pasta)
            self._ordem = [i.filename for i in z.infolist()]
        self.estilos = Estilos(self._ler("xl/styles.xml"))
        self.compartilhadas = self._strings_compartilhadas()
        self._caminhos = self._abas_por_nome()
        self._abertas: dict[str, Aba] = {}

    def __enter__(self) -> "PastaDeTrabalho":
        return self

    def __exit__(self, tipo, *_) -> None:
        try:
            if tipo is None and self.destino:
                self.salvar()
        finally:
            shutil.rmtree(self._pasta, ignore_errors=True)

    def _ler(self, parte: str) -> str:
        return (self._pasta / parte).read_text(encoding="utf-8")

    def _gravar(self, parte: str, texto: str) -> None:
        (self._pasta / parte).parent.mkdir(parents=True, exist_ok=True)
        (self._pasta / parte).write_text(texto, encoding="utf-8")

    def _strings_compartilhadas(self) -> list[str]:
        if not (self._pasta / "xl/sharedStrings.xml").exists():
            return []
        xml = self._ler("xl/sharedStrings.xml")
        return [html.unescape("".join(re.findall(r"<t[^>]*>(.*?)</t>", si, re.S)))
                for si in re.findall(r"<si>(.*?)</si>", xml, re.S)]

    def _abas_por_nome(self) -> dict[str, str]:
        livro, rels = self._ler("xl/workbook.xml"), self._ler("xl/_rels/workbook.xml.rels")
        alvos = {m.group(1): m.group(2) for m in re.finditer(r'<Relationship Id="([^"]+)"[^>]*Target="([^"]+)"', rels)}
        alvos.update({m.group(2): m.group(1) for m in re.finditer(r'<Relationship Target="([^"]+)"[^>]*Id="([^"]+)"', rels)})
        abas = {}
        for m in re.finditer(r'<sheet [^>]*name="([^"]+)"[^>]*r:id="([^"]+)"', livro):
            abas[html.unescape(m.group(1))] = "xl/" + alvos[m.group(2)].lstrip("/").removeprefix("xl/")
        return abas

    def tem_aba(self, nome: str) -> bool:
        return nome in self._caminhos

    def aba(self, nome: str) -> Aba:
        if nome not in self._abertas:
            self._abertas[nome] = Aba(self._ler(self._caminhos[nome]), self.estilos, self.compartilhadas)
        return self._abertas[nome]

    def criar_aba(self, nome: str, xml: str) -> Aba:
        """Acrescenta uma aba nova (workbook.xml, rels e [Content_Types].xml)."""
        numeros = [int(n) for n in re.findall(r"worksheets/sheet(\d+)\.xml", "\n".join(self._caminhos.values()))]
        numero = max(numeros, default=0) + 1
        parte = f"xl/worksheets/sheet{numero}.xml"
        rels = self._ler("xl/_rels/workbook.xml.rels")
        rid = f"rIdRT{numero}"
        self._gravar("xl/_rels/workbook.xml.rels", rels.replace(
            "</Relationships>", f'<Relationship Id="{rid}" Type="http://schemas.openxmlformats.org/officeDocument/2006/'
            f'relationships/worksheet" Target="worksheets/sheet{numero}.xml"/></Relationships>'))
        livro = self._ler("xl/workbook.xml")
        ids = [int(i) for i in re.findall(r'sheetId="(\d+)"', livro)]
        self._gravar("xl/workbook.xml", livro.replace(
            "</sheets>", f'<sheet state="visible" name="{escape(nome)}" sheetId="{max(ids) + 1}" r:id="{rid}"/></sheets>'))
        tipos = self._ler("[Content_Types].xml")
        self._gravar("[Content_Types].xml", tipos.replace(
            "</Types>", f'<Override PartName="/{parte}" ContentType="application/vnd.openxmlformats-officedocument.'
            f'spreadsheetml.worksheet+xml"/></Types>'))
        self._gravar(parte, xml)
        self._ordem.append(parte)
        self._caminhos[nome] = parte
        return self.aba(nome)

    def salvar(self) -> None:
        for nome, aba in self._abertas.items():
            self._gravar(self._caminhos[nome], aba.xml)
        self._gravar("xl/styles.xml", self.estilos.salvar())
        livro = self._ler("xl/workbook.xml")
        if "fullCalcOnLoad" not in livro:  # fórmulas (Painel, Curva S) recalculadas ao abrir
            livro = re.sub(r"<calcPr\s*/>", '<calcPr fullCalcOnLoad="1"/>', livro)
            self._gravar("xl/workbook.xml", livro)
        self.destino.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(self.destino, "w", zipfile.ZIP_DEFLATED) as z:
            for parte in ["[Content_Types].xml"] + [p for p in self._ordem if p != "[Content_Types].xml"]:
                z.write(self._pasta / parte, parte)
