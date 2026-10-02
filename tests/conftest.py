import json
from datetime import date
from pathlib import Path

import pytest

from conciliacao.configuracao import carregar_cadastro
from conciliacao.extratos.leitura import ler_resposta_api

FIXTURES = Path(__file__).parent / "fixtures"
DIA = date(2026, 9, 9)


@pytest.fixture(scope="session")
def cadastro():
    return carregar_cadastro()


@pytest.fixture
def resposta_api():
    """Saída real da API para os PDFs de docs_example (movimento de 08 a 10/09/2026)."""
    return json.loads((FIXTURES / "extratos_exemplo.json").read_text(encoding="utf-8"))


@pytest.fixture
def extratos(resposta_api, cadastro):
    return ler_resposta_api(resposta_api, cadastro)
