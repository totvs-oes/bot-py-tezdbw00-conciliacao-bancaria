"""Credenciais no Gerenciador de Credenciais do Windows (biblioteca keyring), fora do .env.

Ordem de leitura de cada segredo:
    1. variável de ambiente (inclusive a que veio do .env) — Docker, testes e quem ainda não migrou;
    2. Gerenciador de Credenciais do Windows, credencial genérica "AEROFLEX RPA/<NOME>" do usuário que roda o robô.

No servidor, o .env fica só com configurações (URL, pastas, e-mails) e os segredos ficam no Gerenciador:

    python -m conciliacao.credenciais importar-env      # copia os segredos do .env para o Gerenciador e os apaga do .env
    python -m conciliacao.credenciais definir PROTHEUS_SENHA
    python -m conciliacao.credenciais listar            # mostra onde cada um está (sem mostrar o valor)

As credenciais ficam no perfil do usuário do Windows: cadastre logado como o usuário que roda a tarefa agendada.
"""
from __future__ import annotations

import argparse
import getpass
import os
import re
import sys
from pathlib import Path
from typing import Optional

SERVICO = "AEROFLEX RPA"
SEGREDOS = ("PROTHEUS_USUARIO", "PROTHEUS_SENHA", "SMTP_USUARIO", "SMTP_SENHA", "API_EXTRATOS_TOKEN", "RPA_API_TOKEN")
# Não são segredos, mas também ficam no Gerenciador: no servidor da cliente o .env fica vazio
CONFIGURACOES = ("FONTE_EXTRATOS", "PASTA_EXTRATOS", "API_EXTRATOS_URL", "PROTHEUS_URL", "PROTHEUS_AMBIENTE",
                 "PROTHEUS_VISIVEL", "PROTHEUS_IGNORAR_CERTIFICADO", "PROTHEUS_NAVEGADOR", "PROTHEUS_CDP_URL", "FERIADOS",
                 "PASTA_SAIDA", "SMTP_HOST", "SMTP_PORTA", "EMAIL_REMETENTE", "EMAIL_DESTINO_OPERACAO", "EMAIL_DESTINO_TI")
NOMES = SEGREDOS + CONFIGURACOES
ARQUIVO_ENV = Path(__file__).resolve().parent.parent / ".env"


def _alvo(nome: str) -> str:
    return f"{SERVICO}/{nome}"


def do_gerenciador(nome: str) -> Optional[str]:
    """Valor no Gerenciador de Credenciais; None se não houver (ou se não houver cofre, ex.: Linux/Docker)."""
    try:
        import keyring
        return keyring.get_password(_alvo(nome), nome)
    except Exception:  # noqa: BLE001 — sem keyring/sem cofre: só a variável de ambiente vale
        return None


def obter(nome: str, padrao: str = "") -> str:
    return os.environ.get(nome) or do_gerenciador(nome) or padrao


def definir(nome: str, valor: str) -> None:
    import keyring
    keyring.set_password(_alvo(nome), nome, valor)


def remover(nome: str) -> None:
    import keyring
    keyring.delete_password(_alvo(nome), nome)


def _valores_do_env(arquivo: Path) -> dict[str, str]:
    from dotenv import dotenv_values
    return {k: v for k, v in dotenv_values(arquivo).items() if k in NOMES and v}


def importar_env(arquivo: Path = ARQUIVO_ENV, limpar: bool = True) -> list[str]:
    """Copia os segredos preenchidos no .env para o Gerenciador e (limpar=True) deixa a linha vazia no .env.
    O .env só é alterado depois de todas as gravações no Gerenciador darem certo e serem conferidas."""
    valores = _valores_do_env(arquivo)
    for nome, valor in valores.items():
        definir(nome, valor)
        if do_gerenciador(nome) != valor:
            raise RuntimeError(f"{nome}: o Gerenciador de Credenciais não devolveu o valor gravado; .env não alterado")
    if limpar and valores:
        texto = arquivo.read_text(encoding="utf-8")
        for nome in valores:
            texto = re.sub(rf"^(\s*{nome}\s*=).*$", rf"\1   # no Gerenciador de Credenciais ({_alvo(nome)})",
                           texto, flags=re.M)
        arquivo.write_text(texto, encoding="utf-8")
    return sorted(valores)


def _origem(nome: str) -> str:
    from dotenv import dotenv_values
    no_env = bool(dotenv_values(ARQUIVO_ENV).get(nome)) if ARQUIVO_ENV.exists() else False
    if no_env:
        return ".env (migrar: importar-env)"
    if os.environ.get(nome):
        return "variável de ambiente"
    if do_gerenciador(nome):
        return "Gerenciador de Credenciais"
    return "padrão do código" if nome in CONFIGURACOES else "NÃO CONFIGURADO"


def main(argv: Optional[list[str]] = None) -> int:
    p = argparse.ArgumentParser(prog="python -m conciliacao.credenciais", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="comando", required=True)
    sub.add_parser("listar", help="Onde está cada segredo (sem mostrar o valor)")
    p_def = sub.add_parser("definir", help="Grava um valor (segredo digitado sem eco)")
    p_def.add_argument("nome", choices=NOMES)
    p_rem = sub.add_parser("remover", help="Apaga um segredo do Gerenciador")
    p_rem.add_argument("nome", choices=NOMES)
    p_imp = sub.add_parser("importar-env", help="Copia segredos e configurações do .env para o Gerenciador e apaga do .env")
    p_imp.add_argument("--manter-env", action="store_true", help="Não apaga os valores do .env")
    args = p.parse_args(argv)

    if args.comando == "listar":
        for nome in NOMES:
            print(f"  {nome:30} {_origem(nome)}")
    elif args.comando == "definir":
        ler = getpass.getpass if args.nome in SEGREDOS else input
        valor = ler(f"{args.nome}: ").strip()
        if not valor:
            print("Valor vazio: nada gravado.")
            return 1
        definir(args.nome, valor)
        print(f"{args.nome} gravado em '{_alvo(args.nome)}'.")
    elif args.comando == "remover":
        remover(args.nome)
        print(f"{args.nome} removido do Gerenciador.")
    else:
        importados = importar_env(limpar=not args.manter_env)
        print(f"Importados para o Gerenciador: {', '.join(importados) or 'nenhum (já estavam fora do .env)'}"
              + ("" if args.manter_env or not importados else ". Valores apagados do .env."))
    return 0


if __name__ == "__main__":
    sys.exit(main())
