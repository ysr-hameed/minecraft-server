#!/usr/bin/env bash
# Run this on the Ubuntu/Debian VPS inside the project folder.
# It installs Node.js, application dependencies, the official Bedrock server,
# and two systemd units (panel + optional firewall helper rules).
set -euo pipefail

APP_DIR="$(cd "$(dirname "$0")" && pwd)"
SERVICE_NAME="minecraft-panel"
RUN_USER="${SUDO_USER:-$USER}"

echo "==> Installing system packages"
sudo apt-get update -y
sudo apt-get install -y curl ca-certificates zip unzip tar

if ! command -v node >/dev/null 2>&1 || [ "$(node -p 'process.versions.node.split(".")[0]' 2>/dev/null || echo 0)" -lt 18 ]; then
  echo "==> Installing Node.js 22 from NodeSource"
  curl -fsSL https://deb.nodesource.com/setup_22.x | sudo -E bash -
  sudo apt-get install -y nodejs
fi

echo "Node: $(node -v) · npm: $(npm -v)"

echo "==> Stopping any previous panel or Minecraft services"
for old in minecraft-server minecraft-panel slyxpanel pocketmine; do
  if systemctl list-unit-files 2>/dev/null | grep -q "^${old}.service"; then
    sudo systemctl disable --now "${old}.service" || true
  fi
done

echo "==> Installing application dependencies"
cd "$APP_DIR"
npm install --no-audit --no-fund

if [ ! -f .env ]; then
  echo "==> Creating .env from .env.example"
  cp .env.example .env
  PANEL_PASSWORD="$(head -c 18 /dev/urandom | base64 | tr -dc 'A-Za-z0-9' | head -c 16)"
  SECRET_KEY="$(head -c 48 /dev/urandom | base64 | tr -dc 'A-Za-z0-9' | head -c 40)"
  sed -i "s|^MC_PANEL_PASSWORD=.*|MC_PANEL_PASSWORD=${PANEL_PASSWORD}|" .env
  sed -i "s|^SECRET_KEY=.*|SECRET_KEY=${SECRET_KEY}|" .env
  sed -i "s|^MC_SERVER_DIR=.*|MC_SERVER_DIR=${APP_DIR}/bedrock_server|" .env
  sed -i "s|^MC_BACKUP_DIR=.*|MC_BACKUP_DIR=${APP_DIR}/backups|" .env
  echo "Generated panel password: ${PANEL_PASSWORD}"
fi

PUBLIC_IP="$(curl -fsSL --max-time 6 https://api.ipify.org 2>/dev/null || true)"
if [ -n "${PUBLIC_IP}" ] && ! grep -q '^MC_UDP_PORTS=' .env; then
  if grep -q '^#\? *MC_UDP_PORTS=' .env; then
    sed -i "s|^#\? *MC_UDP_PORTS=.*|MC_UDP_PORTS=${PUBLIC_IP}:19133:19133|" .env
  else
    printf '\nMC_UDP_PORTS=%s:19133:19133\n' "${PUBLIC_IP}" >> .env
  fi
  echo "Public NetherNet UDP mapping: ${PUBLIC_IP}:19133"
fi

echo "==> Downloading the latest official Bedrock Dedicated Server"
node scripts/install-bds.js

echo "==> Opening Linux firewall ports"
sudo iptables -C INPUT -p udp --dport 19132 -j ACCEPT 2>/dev/null || sudo iptables -I INPUT -p udp --dport 19132 -j ACCEPT
sudo iptables -C INPUT -p udp --dport 19133 -j ACCEPT 2>/dev/null || sudo iptables -I INPUT -p udp --dport 19133 -j ACCEPT
sudo iptables -C INPUT -p tcp --dport 5000 -j ACCEPT 2>/dev/null || sudo iptables -I INPUT -p tcp --dport 5000 -j ACCEPT
sudo iptables -C INPUT -p tcp --dport 19132 -j ACCEPT 2>/dev/null || sudo iptables -I INPUT -p tcp --dport 19132 -j ACCEPT
if command -v netfilter-persistent >/dev/null 2>&1; then sudo netfilter-persistent save || true; fi

echo "==> Installing systemd service"
sudo tee "/etc/systemd/system/${SERVICE_NAME}.service" >/dev/null <<EOF
[Unit]
Description=SlyxPanel - Official Minecraft Bedrock Dedicated Server
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=${RUN_USER}
WorkingDirectory=${APP_DIR}
Environment=NODE_ENV=production
Environment=PATH=/usr/local/bin:/usr/bin:/bin
ExecStart=/usr/bin/env node ${APP_DIR}/server.js
Restart=always
RestartSec=5
TimeoutStopSec=40
KillMode=mixed
LimitNOFILE=65535

[Install]
WantedBy=multi-user.target
EOF

echo "==> Disabling the old Python/PocketMine services if they exist"
for old in minecraft-server minecraft-panel-old slyxpanel; do
  if systemctl list-unit-files | grep -q "^${old}.service"; then
    sudo systemctl disable --now "${old}.service" || true
  fi
done

sudo systemctl daemon-reload
sudo systemctl enable "${SERVICE_NAME}.service"
sudo systemctl restart "${SERVICE_NAME}.service"

sleep 4
sudo systemctl --no-pager --lines=15 status "${SERVICE_NAME}.service" || true

echo
echo "Panel:   http://${PUBLIC_IP:-<VPS_PUBLIC_IP>}:5000"
echo "Bedrock: ${PUBLIC_IP:-<VPS_PUBLIC_IP>}:19132 (TCP signaling, UDP gameplay 19133)"
echo "Open TCP 5000, TCP 19132 and UDP 19133 in your cloud provider's security list."
