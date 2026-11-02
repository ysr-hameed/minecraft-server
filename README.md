# SlyxPanel - Minecraft Server Manager (Python)

Modern, lightweight Minecraft Server Panel built in Python + Flask.
Runs on low-end VPS, Oracle Cloud Free Tier, Termux, Raspberry Pi.

## Features
- Java (Paper) + Bedrock Mobile support
- Dashboard: CPU, RAM, Disk, Players, TPS-like status, Connect Guide
- Live Console + Logs viewer + Execute commands
- Worlds: Upload .zip, Download, Create new with seed/difficulty/gamemode, Delete
- Players: Whitelist / Op / Ban / Kick with filter & search
- Backups: Manual + Daily auto (date+number), Download, Restore, S3 + Google Drive
- Settings: Version update, server.properties editor, Memory, Port, etc.
- File Manager: Browse / Edit / Upload any file in server folder
- Auth: Password from ENV (MC_PANEL_PASSWORD)
- Optimized for Termux & 1GB RAM

## Quick Start - Termux / Linux / Oracle

```bash
pkg install python git openjdk-17 -y  # Termux
# Oracle Ubuntu:
# sudo apt update && sudo apt install python3 python3-pip openjdk-17-jre-headless -y

pip install -r requirements.txt
cp .env.example .env
# Edit .env password
python app.py
# Open http://localhost:5000
# Login: password from .env
```

## Oracle Cloud Deploy
1. Create Ubuntu 22.04 instance (Ampere Free Tier)
2. Open ports: 5000 (panel), 25565 (Java), 19132 UDP (Bedrock), 19133 UDP
3. Run: `bash deploy_oracle.sh`
4. Panel at http://YOUR_PUBLIC_IP:5000

## Structure
- app.py - Flask panel
- mc_core.py - Server process, version, backup, file logic
- templates/ - Modern UI
- static/style.css - Design system
- minecraft_server/ - server files, worlds
- backups/ - local backups

## Backup Naming
`backup_2026-11-02_03-00_#12_world.zip`

## Connect Guide
- Java PC: Add Server -> IP:25565
- Bedrock Mobile: Play -> Servers -> Add -> IP, Port 19132
# minecraft-server
