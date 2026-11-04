FROM python:3.13-slim

ENV PYTHONUNBUFFERED=1
ENV PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY requirements.txt .
RUN python -m pip install -r requirements.txt

COPY . .
RUN mkdir -p minecraft_server_bedrock/worlds backups uploads runtime

EXPOSE 5000/tcp
EXPOSE 19132/udp
EXPOSE 19133/udp

VOLUME ["/app/minecraft_server_bedrock", "/app/backups"]

CMD ["python", "app.py"]
