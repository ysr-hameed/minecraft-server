FROM node:22-bookworm-slim

ENV NODE_ENV=production
ENV MC_PANEL_PORT=5000
ENV MC_SERVER_PORT=19132
ENV MC_TRANSPORT=raknet

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl zip unzip \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY package.json ./
RUN npm install --omit=optional --no-audit --no-fund

COPY . .
RUN mkdir -p bedrock_server backups uploads runtime logs

EXPOSE 5000/tcp
EXPOSE 19132/udp
EXPOSE 19133/udp

VOLUME ["/app/bedrock_server", "/app/backups"]

HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 \
  CMD node -e "fetch('http://127.0.0.1:5000/healthz').then(r=>process.exit(r.ok?0:1)).catch(()=>process.exit(1))"

CMD ["node", "server.js"]
