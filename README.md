# SlyxPanel - PocketMine-MP Bedrock Panel

A Flask control panel for a pure Minecraft Bedrock server powered by PocketMine-MP 5.x. It is built for Termux on Android ARM64 and also runs on a Linux VPS such as Oracle Cloud.

- Bedrock only: PocketMine-MP + PHP, UDP `19132` and NetherNet TCP `19132`
- Latest supported release: **PocketMine-MP 5.51.1 for Minecraft Bedrock 26.60** (protocol 2225)
- One-click version switching for Bedrock 26.60, 26.52/26.50 and legacy 26.30
- Minecraft Bedrock Mobile, Xbox and Windows Bedrock clients can connect
- No Java server, no Paper, no Purpur, no Geyser, no `server.jar`
- No `psutil`, so no `/proc/stat` permission crash on Android
- No virtual environment needed; run the project from `~/minecraft_panel`

The upstream `pmmp/PocketMine-MP` project ended support at 5.44.3 for Bedrock 26.30. This panel uses the maintained **Axolotl-PM** fork for Bedrock 26.50, 26.52 and 26.60 releases.

## Termux Quick Start

```bash
termux-setup-storage
mv /storage/emulated/0/Download/minecraft_panel ~/minecraft_panel
cd ~/minecraft_panel
pkg update -y
pkg install -y python curl tar zip unzip
python -m pip install --break-system-packages -r requirements.txt
curl -L -o minecraft_server_bedrock/PocketMine-MP.phar https://github.com/axolotl-pm/PocketMine-MP/releases/download/5.51.1/PocketMine-MP.phar
python app.py
```

If the project is already in `~/minecraft_panel`, skip the `mv` line.

Open the panel:

```text
http://localhost:5000
```

Default password: `admin123`. Change it in `.env`:

```dotenv
MC_PANEL_PASSWORD=your-strong-password
SECRET_KEY=your-long-random-secret
```

On the first server start the panel automatically downloads a PocketMine-compatible PHP runtime if the system PHP does not have the required extensions. PocketMine-MP.phar is also downloaded automatically when missing.

The recommended PHP runtime is the Axolotl-PM PHP 8.4 build because Bedrock 26.60 NetherNet needs the `webrtc` extension. If an older runtime is present, the panel replaces it automatically when a NetherNet release is selected.

## Connect From Minecraft Bedrock Mobile

1. Open Minecraft Bedrock.
2. Go to **Play** -> **Servers** -> **Add Server**.
3. Use address `127.0.0.1` when the phone hosts both the panel and the game. On Wi-Fi use the LAN IP shown on the dashboard.
4. Use port `19132`. RakNet uses **UDP 19132**. Bedrock 26.60 NetherNet may also use **TCP 19132** on the same port.

The server must be running before the client joins. On a VPS, open `19132/UDP`, `19132/TCP` and `19133/UDP` in the firewall or Oracle Security List. The panel itself uses TCP port `5000`.

## Features

- Dashboard with Termux-safe CPU, RAM and disk values, server status and online players
- Start, stop and restart with a graceful PocketMine `stop` and world save
- Live console, log viewer and command execution for `say`, `kick`, `ban`, `op`, `whitelist` and every other PocketMine command
- Worlds: upload ZIP, download ZIP, activate, delete, and create a new world with seed, game mode, difficulty and world type
- Players: search, filter, whitelist, operator, ban, unban and kick
- Backups: manual and daily automatic backups named `backup_YYYY-MM-DD_HH-MM_#12.zip`, with download, restore and delete
- Optional S3 upload when `AWS_BUCKET_NAME` is set and `boto3` is installed separately
- Settings editor for PocketMine `server.properties`
- PocketMine version switcher with automatic PHAR and PHP runtime updates
- File manager for everything inside `minecraft_server_bedrock/`
- Password authentication from `MC_PANEL_PASSWORD`

## PocketMine Version Switching

Open **Settings -> PocketMine version**, choose a release and press **Switch and update**. If the server is running, the panel stops it safely, downloads the new PHAR, updates the PHP runtime when needed, and starts the server again.

The version list is not hard-coded as a one-time choice. The panel reads the maintained Axolotl-PM GitHub releases and BedrockProtocol tags every 6 hours, detects the Bedrock version and protocol from the release metadata, and adds new releases to the dropdown automatically.

Automatic updates are enabled by default. When a newer verified PocketMine-MP release appears, the panel switches to it with the same safe stop/update/start process. You can disable this in **Settings -> PocketMine version**. Set `MC_AUTO_UPDATE=false` in `.env` to disable it by environment instead.

| PocketMine-MP | Minecraft Bedrock | Protocol | Notes |
| --- | --- | --- | --- |
| 5.51.1 | 26.60 | 2225 | Latest stable, NetherNet + RakNet |
| 5.50.0 | 26.50 / 26.52 | 2193 | Use for the old 26.52 client |
| 5.49.2 | 26.50 / 26.52 | 2193 | RakNet only fallback |
| 5.44.3 | 26.30 | 1001 | Legacy upstream release |

The client and server must use the same protocol. The panel detects new releases automatically, but the game and server only match when they share the same protocol.

## Daily Backups

Set the hour in `.env`:

```dotenv
BACKUP_HOUR=3
```

The panel creates one automatic backup per day at `BACKUP_HOUR`. If the panel was offline at that hour (phone asleep, process killed), the missed backup is created on the next panel start instead of silently skipped. Backups are named `backup_YYYY-MM-DD_HH-MM_#N_daily.zip` and appear on the Backups page with a type badge and a Download link. Manual backups are available on the Worlds and Backups pages. A safety backup is created automatically before every restore.

## S3 / Neon Object Storage Copy

Every backup (daily and manual) is uploaded to S3-compatible storage. Set these in `.env`:

```dotenv
AWS_BUCKET_NAME=assets
AWS_ENDPOINT_URL_S3=https://your-endpoint.storage.c-8.us-east-2.aws.neon.tech
AWS_ACCESS_KEY_ID=...
AWS_SECRET_ACCESS_KEY=...
AWS_REGION=us-east-2
```

Uploads go to `s3://<bucket>/minecraft-backups/<backup-name>.zip`. Backups still work locally without S3. Never commit `.env` — it is already gitignored.

## VPS / Oracle Cloud

```bash
git clone https://github.com/ysr-hameed/minecraft-server.git ~/minecraft_panel
cd ~/minecraft_panel
bash deploy_oracle.sh
nano .env
python3 app.py
```

Open these ports:

- `5000/TCP` for the panel
- `19132/UDP` for Minecraft Bedrock RakNet
- `19132/TCP` for Bedrock 26.60 NetherNet
- `19133/UDP` for the IPv6 listener

PocketMine PHP binaries are downloaded automatically for Termux ARM64, Linux x86_64 and macOS. On Linux ARM64 there is no official PocketMine PHP build; set `MC_PHP_BINARY` to a PocketMine-compatible PHP 8.x binary if you use that platform.

For a long-running VPS process, use `tmux` or `screen`:

```bash
tmux new -s panel
python3 app.py
```

## Docker

Build the image with the included `Dockerfile`:

```bash
docker build -t slyxpanel .
docker run -d --name slyxpanel \
  -p 5000:5000 \
  -p 19132:19132/udp \
  -p 19132:19132/tcp \
  -p 19133:19133/udp \
  -v slyx_worlds:/app/minecraft_server_bedrock \
  -v slyx_backups:/app/backups \
  slyxpanel
```

The image runs the panel as a non-root user, stores worlds and backups in volumes, and checks `/healthz`. PocketMine and its PHP runtime are downloaded automatically from the official release assets on first start. The panel is Bedrock only, so do not map TCP `25565` or try to use a Java client.

## Project Layout

```text
app.py                          Flask routes and authentication
mc_core.py                      PocketMine process, stats, worlds, backups, files
templates/                      Dashboard, console, worlds, players, files, settings
static/style.css                Full dark responsive UI
minecraft_server_bedrock/       PocketMine-MP.phar, worlds, plugins, server.properties
backups/                        Local backup archives
uploads/                        Temporary uploads
runtime/                        Downloaded PocketMine PHP runtime
requirements.txt                Flask, requests, python-dotenv, Werkzeug
```

## Useful Commands

```bash
# Check the panel syntax
python -m py_compile app.py mc_core.py

# Start the panel
python app.py

# Start the Bedrock server from the panel console equivalent
cd minecraft_server_bedrock
php PocketMine-MP.phar --no-wizard

# List listening UDP sockets on Linux
ss -lunp | grep 19132
```

The panel starts PocketMine with the same command: `php PocketMine-MP.phar --no-wizard`, using the selected version and its compatible PHP runtime.

## Notes

- Do not create a virtual environment inside `/storage/emulated/0/`; Android shared storage does not support the required symlinks.
- The panel never uses `psutil`. RAM comes from `/proc/meminfo`, disk from `shutil.disk_usage`, and CPU reports `0` on Termux to avoid `/proc/stat` permission errors.
- World and player data are runtime files and are ignored by git.
- The panel is Bedrock only. Connect with Bedrock on UDP/TCP `19132`, not with a Java client on TCP `25565`.
- Bedrock 26.60 server releases require the `webrtc` PHP extension for NetherNet. The panel downloads the Axolotl-PM PHP build automatically.
