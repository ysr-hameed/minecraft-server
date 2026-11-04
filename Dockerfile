FROM python:3.13-slim

ENV PYTHONUNBUFFERED=1
ENV PIP_NO_CACHE_DIR=1
ENV PIP_BREAK_SYSTEM_PACKAGES=1
ENV MC_PANEL_PORT=5000

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates libstdc++6 \
    && rm -rf /var/lib/apt/lists/*

RUN useradd --create-home --shell /usr/sbin/nologin panel

WORKDIR /app

COPY requirements.txt .
RUN python -m pip install --no-cache-dir -r requirements.txt

COPY --chown=panel:panel . .
RUN mkdir -p minecraft_server_bedrock/worlds minecraft_server_bedrock/plugins minecraft_server_bedrock/players backups uploads runtime \
    && chown -R panel:panel /app

USER panel

EXPOSE 5000/tcp
EXPOSE 19132/udp
EXPOSE 19132/tcp
EXPOSE 19133/udp

VOLUME ["/app/minecraft_server_bedrock", "/app/backups"]

HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:5000/healthz', timeout=4).read()" || exit 1

CMD ["python", "app.py"]
