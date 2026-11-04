# SlyxPanel - PocketMine-MP Bedrock Panel

A Flask control panel for a pure Minecraft Bedrock server powered by PocketMine-MP 5.x. It is built for Termux on Android ARM64 and also runs on a Linux VPS such as Oracle Cloud.

- Bedrock only: PocketMine-MP + PHP, UDP port `19132`
- Minecraft Bedrock Mobile, Xbox and Windows Bedrock clients can connect
- No Java server, no Paper, no Purpur, no Geyser, no `server.jar`
- No `psutil`, so no `/proc/stat` permission crash on Android
- No virtual environment needed; run the project from `~/minecraft_panel`

## Termux Quick Start

```bash
termux-setup-storage
mv /storage/emulated/0/Download/minecraft_panel ~/minecraft_panel
cd ~/minecraft_panel
pkg update -y
pkg install -y python curl tar zip unzip
python -m pip install --break-system-packages -r requirements.txt
curl -L -o minecraft_server_bedrock/PocketMine-MP.phar https://github.com/pmmp/PocketMine-MP/releases/latest/download/PocketMine-MP.phar
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

## Connect From Minecraft Bedrock Mobile

1. Open Minecraft Bedrock.
2. Go to **Play** -> **Servers** -> **Add Server**.
3. Use address `127.0.0.1` when the phone hosts both the panel and the game. On Wi-Fi use the LAN IP shown on the dashboard.
4. Use port `19132`. This is a **UDP** port.

The server must be running before the client joins. On a VPS, open `19132/UDP` in the firewall or Oracle Security List. The panel itself uses TCP port `5000`.

## Features

- Dashboard with Termux-safe CPU, RAM and disk values, server status and online players
- Start, stop and restart with a graceful PocketMine `stop` and world save
- Live console, log viewer and command execution for `say`, `kick`, `ban`, `op`, `whitelist` and every other PocketMine command
- Worlds: upload ZIP, download ZIP, activate, delete, and create a new world with seed, game mode, difficulty and world type
- Players: search, filter, whitelist, operator, ban, unban and kick
- Backups: manual and daily automatic backups named `backup_YYYY-MM-DD_HH-MM_#12.zip`, with download, restore and delete
- Optional S3 upload when `AWS_BUCKET_NAME` is set and `boto3` is installed separately
- Settings editor for PocketMine `server.properties`
- File manager for everything inside `minecraft_server_bedrock/`
- Password authentication from `MC_PANEL_PASSWORD`

## Daily Backups

Set the hour in `.env`:

```dotenv
BACKUP_HOUR=3
```

The panel creates one automatic backup per day during that hour. Manual backups are available on the Worlds and Backups pages. A safety backup is created automatically before every restore.

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
- `19132/UDP` for Minecraft Bedrock

PocketMine PHP binaries are downloaded automatically for Termux ARM64, Linux x86_64 and macOS. On Linux ARM64 there is no official PocketMine PHP build; set `MC_PHP_BINARY` to a PocketMine-compatible PHP 8.x binary if you use that platform.

For a long-running VPS process, use `tmux` or `screen`:

```bash
tmux new -s panel
python3 app.py
```

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

The panel starts PocketMine with the same command: `php PocketMine-MP.phar --no-wizard`.

## Notes

- Do not create a virtual environment inside `/storage/emulated/0/`; Android shared storage does not support the required symlinks.
- The panel never uses `psutil`. RAM comes from `/proc/meminfo`, disk from `shutil.disk_usage`, and CPU reports `0` on Termux to avoid `/proc/stat` permission errors.
- World and player data are runtime files and are ignored by git.
- The panel is Bedrock only. Connect with Bedrock on UDP `19132`, not with a Java client on TCP `25565`.
