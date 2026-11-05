# SlyxPanel — Official Minecraft Bedrock Server + Node.js Panel

A Node.js control panel for the **official Mojang Bedrock Dedicated Server (BDS)**. It always installs the latest official Linux build, so the server version matches current Minecraft Bedrock clients.

The old PocketMine-Axolotl setup only supported Bedrock 1.26.60 (protocol 2225). Current clients are on 1.26.62.x, and PocketMine's NetherNet transport needs extra UDP ports. That is why nobody could join. This version fixes both problems:

- Official `bedrock_server` from `minecraft.net` (latest stable automatically)
- NetherNet transport with a **fixed** public UDP gameplay port (`19132`) so the firewall can pass it
- `TCP 19132` (NetherNet signaling) and `UDP 19132` (gameplay), both opened by `deploy_vps.sh`
- Node.js panel with systemd service, no Python, no PHP, no Docker required

## Features

- Dashboard with status, CPU, RAM, disk, uptime, players and version
- Start / stop / restart with graceful world save
- Live console with command input and quick commands
- Players: allow list, operators, bans, kicks (official Bedrock commands)
- Worlds: activate, create, upload ZIP, download ZIP, delete
- Backups: manual + daily, `save hold` / `save resume` consistency, restore and download
- Optional S3-compatible backup upload (Neon, AWS, MinIO, ...)
- Settings editor for `server.properties`
- File manager for the whole `bedrock_server/` folder
- Version switcher fed by Mojang's official download API
- Automatic update check and optional automatic install when idle

## VPS deployment (Ubuntu / Debian)

```bash
git clone https://github.com/ysr-hameed/minecraft-server.git ~/minecraft-panel
cd ~/minecraft-panel
bash deploy_vps.sh
```

The script installs Node.js 22, npm dependencies, the latest official Bedrock server, firewall rules and the `minecraft-panel` systemd service. It generates a random panel password on first run and prints it.

Open in your cloud provider's security list / firewall:

| Port | Protocol | Purpose |
| --- | --- | --- |
| `5000` | TCP | Web panel |
| `19132` | TCP | NetherNet signaling |
| `19133` | UDP | NetherNet gameplay (recommended) |
| `19132` | UDP | Optional RakNet/legacy gameplay |

Then open `http://<VPS_PUBLIC_IP>:5000` and add the server in Minecraft Bedrock:

```text
Address: <VPS_PUBLIC_IP>
Port:    19132
```

The Minecraft client must be the same version as the server. The panel installs the latest official build automatically, so keep clients updated.

## Manual run

```bash
sudo apt install -y curl zip unzip
npm install
node scripts/install-bds.js
cp .env.example .env
nano .env
node server.js
```

## Configuration (` .env`)

| Variable | Default | Meaning |
| --- | --- | --- |
| `MC_PANEL_PASSWORD` | `admin123` | Web panel password — change it |
| `SECRET_KEY` | generated | Session signing secret |
| `MC_PANEL_PORT` | `5000` | Panel HTTP port |
| `MC_SERVER_DIR` | `./bedrock_server` | Official server files and worlds |
| `MC_BACKUP_DIR` | `./backups` | Backup archives |
| `MC_SERVER_PORT` | `19132` | NetherNet signaling/TCP port |
| `MC_SERVER_PORT_V6` | `19133` | Bedrock IPv6 port (ignored by NetherNet) |
| `MC_UDP_PORTS` | empty | NetherNet gameplay UDP mapping, e.g. `203.0.113.10:19133:19133` |
| `MC_TRANSPORT` | `nethernet` | Required by current official BDS builds |
| `MC_AUTO_UPDATE` | `true` | Check/install newest official build |
| `MC_AUTO_START` | `true` | Start the server with the panel |
| `BACKUP_HOUR` | `3` | Daily backup hour (0–23) |

Optional S3 copies:

```dotenv
AWS_BUCKET_NAME=assets
AWS_ENDPOINT_URL_S3=https://your-endpoint.storage.example.com
AWS_ACCESS_KEY_ID=...
AWS_SECRET_ACCESS_KEY=...
AWS_REGION=us-east-2
```

Install the optional dependency with `npm install @aws-sdk/client-s3`. Backups always work locally without it.

## systemd

`deploy_vps.sh` creates `/etc/systemd/system/minecraft-panel.service`.

```bash
sudo systemctl status minecraft-panel
sudo systemctl restart minecraft-panel
sudo journalctl -u minecraft-panel -f
```

The panel manages `bedrock_server` as a child process and stops it gracefully on `SIGTERM`.

## NetherNet on a VPS

Official Bedrock Dedicated Server **1.26.62.1 and newer only support NetherNet**. RakNet is rejected with:

```text
Your current connection type is not set to NetherNet.
In this release, NetherNet is the only supported transport type.
```

NetherNet works in two phases:

1. The client connects to `server-port` over **TCP** for the HTTP signaling handshake.
2. Both sides negotiate a **UDP** connection for gameplay.

By default BDS lets the OS choose a random UDP port for gameplay. On a VPS the firewall does not allow that random port, so the server appears online, then the client fails with **`Door` / `InitialConnection-83`**. This panel sets:

```properties
transport=nethernet
server-udp-ports=203.0.113.10:19133:19133
```

The `PUBLIC_IP:PUBLIC:INTERNAL` form tells the client which public UDP address to use. Set it with `MC_UDP_PORTS` in `.env` (do not use a hostname; use the public IPv4 literal). Use a gameplay UDP port that is **different from the TCP signaling port**; `19133` is the tested default here. Both `TCP 19132` and `UDP 19133` are opened by `deploy_vps.sh`. Keep these ports open in your cloud security list as well.

You can verify signaling from any machine:

```bash
curl http://<VPS_PUBLIC_IP>:19132/v1/join
```

It should return the server name, protocol and version.

## Project layout

```text
server.js                 Express app, auth and routes
src/config.js             .env configuration
src/manager.js            Official BDS install, process, console, backups, worlds, players
src/stats.js              CPU / RAM / disk readers
src/s3.js                 Optional S3 backup upload
views/                    EJS pages
public/style.css          Panel theme
scripts/install-bds.js    Download the latest official Bedrock server
deploy_vps.sh             One-command VPS installer
```

## Troubleshooting

- **Panel reachable, server not joinable:** confirm `UDP 19133` is open in both the Linux firewall and the cloud security list. Check the console for `Server started.` and `Accepting clients on [::]:19132`.
- **`Door` / `InitialConnection-83`:** TCP signaling works, but the client cannot reach the gameplay UDP port. Set `MC_UDP_PORTS=<PUBLIC_IP>:19133:19133`, open UDP 19133 in the cloud firewall, and restart. Do not map gameplay UDP to the same port as TCP signaling.
- **`Terracotta` / `InitialConnection-149`:** TCP signaling is not reachable. Confirm TCP 19132 is open and `curl http://<VPS_PUBLIC_IP>:19132/v1/join` returns JSON.
- **Version mismatch:** press **Download / reinstall latest** in Settings, or restart the panel. It reads Mojang's official download API.
- **A player is stuck / world not saving:** use **Stop** in the panel, never kill the process, so the official server can save.
- **Operator changes while offline:** the panel needs the player's XUID. Let the player join once, then use **Op**.
