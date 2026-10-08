"""Registro de execução em SQLite: garante que nenhum lançamento seja feito duas vezes.

Ciclo de vida de um item:
    planejado -> executando -> salvando -> concluido
                     |             |
                     v             v
                   erro         incerto   (exceção depois de clicar em Salvar: pode ter gravado)

- "erro": falhou antes de salvar; pode ser tentado de novo.
- "incerto": NUNCA é refeito automaticamente; exige conferência humana no Protheus.
- Um item que ficou em "executando"/"salvando" (robô caiu no meio) é tratado como "erro"/"incerto" na próxima execução.
"""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import date, datetime
from pathlib import Path
from typing import Iterator, Optional

from conciliacao.modelos import Conciliacao, ItemPlano, Plano
from conciliacao.planejamento import documento

PLANEJADO, EXECUTANDO, SALVANDO, CONCLUIDO, ERRO, INCERTO = (
    "planejado", "executando", "salvando", "concluido", "erro", "incerto")
PODE_EXECUTAR = (PLANEJADO, ERRO)

ESQUEMA = """
CREATE TABLE IF NOT EXISTS lancamentos (
    chave TEXT PRIMARY KEY,
    data_movimento TEXT NOT NULL,
    rotina TEXT NOT NULL,
    contas TEXT NOT NULL,
    banco_documento TEXT NOT NULL,
    valor TEXT NOT NULL,
    historico TEXT NOT NULL,
    origem_extrato TEXT NOT NULL,
    documento TEXT NOT NULL,
    status TEXT NOT NULL,
    detalhe TEXT,
    criado_em TEXT NOT NULL,
    atualizado_em TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_documento
    ON lancamentos (rotina, banco_documento, data_movimento, documento);
CREATE TABLE IF NOT EXISTS conciliacoes (
    conta TEXT NOT NULL,
    data_movimento TEXT NOT NULL,
    status TEXT NOT NULL,
    saldo_extrato TEXT NOT NULL,
    saldo_protheus TEXT,
    detalhe TEXT,
    atualizado_em TEXT NOT NULL,
    PRIMARY KEY (conta, data_movimento)
);
"""


def _agora() -> str:
    return datetime.now().isoformat(timespec="seconds")


class Registro:
    def __init__(self, caminho: Path | str):
        if caminho != ":memory:":
            Path(caminho).parent.mkdir(parents=True, exist_ok=True)
        self.conexao = sqlite3.connect(str(caminho))
        self.conexao.row_factory = sqlite3.Row
        self.conexao.executescript(ESQUEMA)
        self._simulando = False

    def fechar(self) -> None:
        self.conexao.close()

    def _commit(self) -> None:
        if not self._simulando:
            self.conexao.commit()

    @contextmanager
    def simulacao(self) -> Iterator["Registro"]:
        """Tudo que for gravado dentro do bloco é desfeito no final (dry-run)."""
        self.conexao.commit()
        self._simulando = True
        try:
            yield self
        finally:
            self.conexao.rollback()
            self._simulando = False

    # ------------------------------------------------------------------
    def recuperar_interrompidos(self) -> int:
        """Itens que ficaram no meio de uma execução anterior."""
        cursor = self.conexao.execute(
            "UPDATE lancamentos SET status = CASE status WHEN ? THEN ? ELSE ? END, "
            "detalhe = 'Execução anterior interrompida', atualizado_em = ? WHERE status IN (?, ?)",
            (SALVANDO, INCERTO, ERRO, _agora(), EXECUTANDO, SALVANDO))
        self._commit()
        return cursor.rowcount

    def registrar_plano(self, plano: Plano) -> None:
        """Grava os itens novos e preenche documento/status de todos os itens do plano."""
        for item in plano.itens():
            linha = self.conexao.execute("SELECT documento FROM lancamentos WHERE chave = ?", (item.chave,)).fetchone()
            if not linha and item.chave_legada:
                # Registro gravado com a chave antiga (com o histórico): passa a usar a chave nova
                linha = self.conexao.execute("SELECT documento FROM lancamentos WHERE chave = ?",
                                             (item.chave_legada,)).fetchone()
                if linha:
                    self.conexao.execute("UPDATE lancamentos SET chave = ?, atualizado_em = ? WHERE chave = ?",
                                         (item.chave, _agora(), item.chave_legada))
            if linha:
                item.documento = linha["documento"]
                continue
            item.documento = self._proximo_documento(item)
            self.conexao.execute(
                "INSERT INTO lancamentos (chave, data_movimento, rotina, contas, banco_documento, valor, historico, "
                "origem_extrato, documento, status, criado_em, atualizado_em) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (item.chave, item.data.isoformat(), item.rotina.value, item.contas(), item.banco_documento(),
                 str(item.valor), item.historico, item.origem_extrato, item.documento, PLANEJADO, _agora(), _agora()))
        self._commit()

    def _proximo_documento(self, item: ItemPlano) -> str:
        usados = {linha["documento"] for linha in self.conexao.execute(
            "SELECT documento FROM lancamentos WHERE rotina = ? AND banco_documento = ? AND data_movimento = ?",
            (item.rotina.value, item.banco_documento(), item.data.isoformat()))}
        sequencia = 0
        while documento(item.data, sequencia) in usados:
            sequencia += 1
        return documento(item.data, sequencia)

    def status(self, chave: str) -> Optional[str]:
        linha = self.conexao.execute("SELECT status FROM lancamentos WHERE chave = ?", (chave,)).fetchone()
        return linha["status"] if linha else None

    def marcar(self, chave: str, status: str, detalhe: Optional[str] = None) -> None:
        self.conexao.execute("UPDATE lancamentos SET status = ?, detalhe = ?, atualizado_em = ? WHERE chave = ?",
                             (status, detalhe, _agora(), chave))
        self._commit()

    def itens_do_dia(self, data_movimento: date) -> list[sqlite3.Row]:
        return self.conexao.execute("SELECT * FROM lancamentos WHERE data_movimento = ? ORDER BY criado_em",
                                    (data_movimento.isoformat(),)).fetchall()

    # ------------------------------------------------------------------
    def status_conciliacao(self, conciliacao: Conciliacao) -> Optional[str]:
        linha = self.conexao.execute("SELECT status FROM conciliacoes WHERE conta = ? AND data_movimento = ?",
                                     (conciliacao.conta.chave, conciliacao.data.isoformat())).fetchone()
        return linha["status"] if linha else None

    def marcar_conciliacao(self, conciliacao: Conciliacao, status: str, saldo_protheus=None,
                           detalhe: Optional[str] = None) -> None:
        self.conexao.execute(
            "INSERT INTO conciliacoes (conta, data_movimento, status, saldo_extrato, saldo_protheus, detalhe, atualizado_em) "
            "VALUES (?,?,?,?,?,?,?) ON CONFLICT (conta, data_movimento) DO UPDATE SET status = excluded.status, "
            "saldo_extrato = excluded.saldo_extrato, saldo_protheus = excluded.saldo_protheus, "
            "detalhe = excluded.detalhe, atualizado_em = excluded.atualizado_em",
            (conciliacao.conta.chave, conciliacao.data.isoformat(), status, str(conciliacao.saldo_extrato),
             None if saldo_protheus is None else str(saldo_protheus), detalhe, _agora()))
        self._commit()
