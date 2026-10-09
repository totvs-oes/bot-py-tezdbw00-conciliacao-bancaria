"""Credenciais no Gerenciador de Credenciais do Windows (keyring), com um cofre em memória no lugar do real."""
import keyring
import pytest
from keyring.backend import KeyringBackend

from conciliacao import credenciais


class CofreEmMemoria(KeyringBackend):
    priority = 1

    def __init__(self):
        super().__init__()
        self.itens = {}

    def get_password(self, servico, usuario):
        return self.itens.get((servico, usuario))

    def set_password(self, servico, usuario, senha):
        self.itens[(servico, usuario)] = senha

    def delete_password(self, servico, usuario):
        self.itens.pop((servico, usuario), None)


@pytest.fixture
def cofre(monkeypatch):
    anterior = keyring.get_keyring()
    falso = CofreEmMemoria()
    keyring.set_keyring(falso)
    for nome in credenciais.NOMES:
        monkeypatch.delenv(nome, raising=False)
    yield falso
    keyring.set_keyring(anterior)


def test_variavel_de_ambiente_vem_antes_do_gerenciador(cofre, monkeypatch):
    credenciais.definir("PROTHEUS_SENHA", "do-cofre")
    assert credenciais.obter("PROTHEUS_SENHA") == "do-cofre"
    monkeypatch.setenv("PROTHEUS_SENHA", "do-ambiente")       # Docker / testes
    assert credenciais.obter("PROTHEUS_SENHA") == "do-ambiente"
    monkeypatch.setenv("PROTHEUS_SENHA", "")                  # linha vazia no .env: vale o Gerenciador
    assert credenciais.obter("PROTHEUS_SENHA") == "do-cofre"
    assert cofre.itens == {("AEROFLEX RPA/PROTHEUS_SENHA", "PROTHEUS_SENHA"): "do-cofre"}


def test_sem_credencial_devolve_o_padrao(cofre):
    assert credenciais.obter("SMTP_SENHA") == ""
    assert credenciais.obter("SMTP_SENHA", "x") == "x"


def test_importar_env_grava_no_gerenciador_e_apaga_do_env(cofre, tmp_path):
    env = tmp_path / ".env"
    env.write_text("PROTHEUS_URL=https://protheus\nOUTRA_VARIAVEL=1\nPROTHEUS_SENHA=s3nh@$x\nSMTP_SENHA=\n"
                   "RPA_API_TOKEN=abc\n", encoding="utf-8")
    assert credenciais.importar_env(env) == ["PROTHEUS_SENHA", "PROTHEUS_URL", "RPA_API_TOKEN"]
    assert credenciais.do_gerenciador("PROTHEUS_SENHA") == "s3nh@$x" and credenciais.do_gerenciador("RPA_API_TOKEN") == "abc"
    texto = env.read_text(encoding="utf-8")
    assert "s3nh@" not in texto and "abc" not in texto
    assert credenciais.do_gerenciador("PROTHEUS_URL") == "https://protheus"   # configuração da lista também vai
    assert "OUTRA_VARIAVEL=1" in texto                                    # o que não está na lista fica no .env
    assert "PROTHEUS_SENHA=   # no Gerenciador de Credenciais (AEROFLEX RPA/PROTHEUS_SENHA)" in texto


def test_configuracao_no_gerenciador_chega_ao_ambiente(cofre, monkeypatch):
    """Configuração vazia no .env (PROTHEUS_URL=, SMTP_PORTA=) vem do Gerenciador; sem nenhum dos dois, vale o padrão."""
    from conciliacao.configuracao import carregar_ambiente
    for nome in credenciais.CONFIGURACOES:
        monkeypatch.setenv(nome, "")
    credenciais.definir("PROTHEUS_URL", "https://protheus.cliente:8800/webapp/")
    credenciais.definir("SMTP_PORTA", "25")
    ambiente = carregar_ambiente()
    assert ambiente.protheus_url.startswith("https://protheus.cliente:8800/webapp")
    assert ambiente.smtp_porta == 25
    assert ambiente.api_url == "http://localhost:5000/extratos"


def test_importar_env_nao_mexe_no_env_se_o_gerenciador_falhar(cofre, tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text("PROTHEUS_SENHA=segredo\n", encoding="utf-8")
    monkeypatch.setattr(cofre, "set_password", lambda *a: None)          # cofre que "perde" a gravação
    with pytest.raises(RuntimeError, match=".env não alterado"):
        credenciais.importar_env(env)
    assert env.read_text(encoding="utf-8") == "PROTHEUS_SENHA=segredo\n"


def test_execucao_agendada_nao_roda_em_fim_de_semana_nem_feriado(monkeypatch, cadastro):
    """Sem --data-movimento (Agendador): sábado, domingo e FERIADOS do .env terminam em 0 sem processar nada."""
    from argparse import Namespace
    from datetime import date
    import conciliacao.__main__ as cli
    from conciliacao.configuracao import carregar_ambiente
    ambiente = carregar_ambiente()
    monkeypatch.setattr(cli, "executar_dia", lambda *a: pytest.fail("não devia processar"))
    args = Namespace(data_movimento=None, dry_run=False, ensaio=False)
    monkeypatch.setattr(cli, "_hoje", lambda: date(2026, 9, 12))            # sábado
    assert cli.comando_executar(args, ambiente, cadastro) == 0
    ambiente.feriados = frozenset({date(2026, 11, 20)})
    monkeypatch.setattr(cli, "_hoje", lambda: date(2026, 11, 20))           # feriado (sexta)
    assert cli.comando_executar(args, ambiente, cadastro) == 0
