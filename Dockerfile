# Robô de conciliação bancária (Playwright + Google Chrome headless)
# Debian 12 (bookworm): distribuição suportada pelo "playwright install --with-deps"
FROM python:3.13-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    # "Dia útil anterior a hoje" depende do fuso: o container precisa estar no horário de Brasília
    TZ=America/Sao_Paulo \
    # Arquivos do Playwright fora do /root, para o usuário "robo" conseguir ler
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright

WORKDIR /app

# Navegador primeiro (camada pesada, muda pouco). Mesma versão do Playwright usada no desenvolvimento.
# "chrome" instala o Google Chrome real (só amd64), o mesmo navegador em que as telas foram calibradas.
RUN apt-get update \
    && apt-get install -y --no-install-recommends tzdata \
    && pip install "playwright==1.63.0" \
    && playwright install --with-deps chrome \
    && rm -rf /var/lib/apt/lists/*

# Dependências e código do robô
COPY pyproject.toml ./
COPY conciliacao ./conciliacao
RUN pip install .

COPY config ./config

# Não roda como root. A pasta de saída já nasce com o dono certo (o volume herda a permissão).
RUN useradd --create-home --uid 1000 robo \
    && mkdir -p /app/saida \
    && chown robo:robo /app/saida
USER robo

EXPOSE 5001

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:5001/saude', timeout=4)"

# Padrão: API HTTP (POST /execucoes). Para outros comandos:
#   docker compose run --rm robo planejar --data-movimento 2026-09-09
ENTRYPOINT ["python", "-m", "conciliacao"]
CMD ["api", "--host", "0.0.0.0", "--port", "5001"]
