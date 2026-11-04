from __future__ import annotations

import datetime as dt
import os
import platform
import re
import shutil
import socket
import subprocess
import tarfile
import tempfile
import threading
import time
import zipfile
from collections import deque
from pathlib import Path

import requests

BASE_DIR = Path(__file__).resolve().parent
SERVER_DIR = Path(os.getenv("MC_SERVER_DIR", str(BASE_DIR / "minecraft_server_bedrock"))).expanduser().resolve()
BACKUP_DIR = Path(os.getenv("MC_BACKUP_DIR", str(BASE_DIR / "backups"))).expanduser().resolve()
UPLOAD_DIR = Path(os.getenv("MC_UPLOAD_DIR", str(BASE_DIR / "uploads"))).expanduser().resolve()
RUNTIME_DIR = Path(os.getenv("MC_RUNTIME_DIR", str(BASE_DIR / "runtime"))).expanduser().resolve()
WORLDS_DIR = SERVER_DIR / "worlds"

PHAR_NAME = "PocketMine-MP.phar"
PHAR_PATH = SERVER_DIR / PHAR_NAME
POCKETMINE_URL = "https://github.com/pmmp/PocketMine-MP/releases/latest/download/PocketMine-MP.phar"
PM_PHP_URLS = {
    ("android", "arm64"): "https://github.com/pmmp/PHP-Binaries/releases/download/pm5-php-8.4-latest/PHP-8.4-Android-arm64-PM5.tar.gz",
    ("linux", "x86_64"): "https://github.com/pmmp/PHP-Binaries/releases/download/pm5-php-8.4-latest/PHP-8.4-Linux-x86_64-PM5.tar.gz",
    ("darwin", "arm64"): "https://github.com/pmmp/PHP-Binaries/releases/download/pm5-php-8.4-latest/PHP-8.4-MacOS-arm64-PM5.tar.gz",
    ("darwin", "x86_64"): "https://github.com/pmmp/PHP-Binaries/releases/download/pm5-php-8.4-latest/PHP-8.4-MacOS-x86_64-PM5.tar.gz",
}

ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")
PLAYER_NAME_RE = re.compile(r"^[A-Za-z0-9_. -]{1,32}$")
WORLD_NAME_RE = re.compile(r"^[A-Za-z0-9_-]{1,32}$")
PROPERTY_KEY_RE = re.compile(r"^[A-Za-z0-9._-]{1,80}$")
BACKUP_NAME_RE = re.compile(r"^backup_\d{4}-\d{2}-\d{2}_\d{2}-\d{2}_#\d+\.zip$")
JOIN_RE = re.compile(r"^(.{1,32}?) joined the game\.?$")
LEAVE_RE = re.compile(r"^(.{1,32}?) left the game\.?$")
VERSION_RE = re.compile(r"PocketMine-MP version ([0-9][0-9A-Za-z.+-]*)")
REQUIRED_EXTENSIONS = {
    "chunkutils2",
    "crypto",
    "curl",
    "encoding",
    "gmp",
    "igbinary",
    "leveldb",
    "mbstring",
    "morton",
    "openssl",
    "phar",
    "pmmpthread",
    "sockets",
    "yaml",
    "zip",
    "zlib",
}

DEFAULT_PROPERTIES = {
    "server-port": os.getenv("MC_BEDROCK_PORT", os.getenv("MC_PORT", "19132")),
    "server-portv6": os.getenv("MC_BEDROCK_PORT_V6", "19133"),
    "enable-ipv6": "true",
    "server-ip": "",
    "server-ipv6": "",
    "motd": os.getenv("MC_MOTD", "SlyxCraft Bedrock Server"),
    "white-list": "false",
    "max-players": os.getenv("MC_MAX_PLAYERS", "20"),
    "gamemode": os.getenv("MC_GAMEMODE", "survival"),
    "force-gamemode": "false",
    "hardcore": "false",
    "pvp": "true",
    "difficulty": os.getenv("MC_DIFFICULTY", "normal"),
    "level-name": os.getenv("MC_LEVEL_NAME", "world"),
    "level-seed": "",
    "level-type": "DEFAULT",
    "generator-settings": "",
    "enable-query": "true",
    "query.port": os.getenv("MC_BEDROCK_PORT", os.getenv("MC_PORT", "19132")),
    "auto-save": "true",
    "view-distance": "10",
    "xbox-auth": "true",
    "language": "eng",
    "enable-rcon": "false",
    "rcon.port": "25575",
    "rcon.password": "",
}

BOOL_PROPERTIES = {
    "enable-ipv6",
    "white-list",
    "force-gamemode",
    "hardcore",
    "pvp",
    "enable-query",
    "auto-save",
    "xbox-auth",
    "enable-rcon",
}

for directory in (SERVER_DIR, BACKUP_DIR, UPLOAD_DIR, RUNTIME_DIR, WORLDS_DIR, SERVER_DIR / "plugins"):
    directory.mkdir(parents=True, exist_ok=True)


def is_termux() -> bool:
    return bool(
        os.getenv("TERMUX_VERSION")
        or "com.termux" in os.getenv("PREFIX", "")
        or "android" in platform.platform().lower()
    )


def platform_label() -> str:
    if is_termux():
        return "Termux / Android"
    return f"{platform.system()} / {platform.machine()}"


def strip_ansi(text: str) -> str:
    return ANSI_RE.sub("", text).replace("\x00", "").rstrip("\r\n")


def human_size(size: int) -> str:
    size = float(max(0, size))
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024 or unit == "TB":
            return f"{size:.1f} {unit}" if unit != "B" else f"{int(size)} B"
        size /= 1024
    return "0 B"


def get_lan_ip() -> str:
    sock = None
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.connect(("8.8.8.8", 80))
        ip = sock.getsockname()[0]
        if ip and not ip.startswith("127."):
            return ip
    except OSError:
        pass
    finally:
        if sock:
            sock.close()
    try:
        ip = socket.gethostbyname(socket.gethostname())
        if ip and not ip.startswith("127."):
            return ip
    except OSError:
        pass
    return "127.0.0.1"


def safe_extract_zip(archive: Path, destination: Path) -> None:
    destination = destination.resolve()
    destination.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive) as zf:
        for info in zf.infolist():
            name = info.filename.replace("\\", "/")
            if not name or name.startswith("/") or ".." in Path(name).parts:
                raise ValueError(f"Unsafe path in archive: {info.filename}")
            target = (destination / name).resolve()
            if target != destination and destination not in target.parents:
                raise ValueError(f"Unsafe path in archive: {info.filename}")
            mode = (info.external_attr >> 16) & 0o170000
            if mode == 0o120000:
                continue
            if info.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(info) as source, target.open("wb") as output:
                shutil.copyfileobj(source, output)


def looks_like_world(path: Path) -> bool:
    return (
        (path / "level.dat").is_file()
        or (path / "levelname.txt").is_file()
        or (path / "db").is_dir()
        or (path / "region").is_dir()
    )


class ServerManager:
    def __init__(self) -> None:
        self.process: subprocess.Popen | None = None
        self._logs: deque[str] = deque(maxlen=4000)
        self._log_lock = threading.Lock()
        self._players: set[str] = set()
        self._started_at: float | None = None
        self._version = "5.x"
        self._action_lock = threading.RLock()
        self._download_lock = threading.Lock()
        self._backup_lock = threading.Lock()
        self._cpu_previous: tuple[int, int] | None = None
        self._php_cache: dict[tuple[str, float], tuple[bool, list[str]]] = {}
        self._bundled_php: Path | None = None
        self._auto_started = False
        self._auto_stop = threading.Event()
        self._last_daily_backup = ""
        self._ensure_files()

    @property
    def server_type(self) -> str:
        return "PocketMine-MP"

    @property
    def version(self) -> str:
        return self._version

    @property
    def log_lines(self) -> list[str]:
        return self.logs_snapshot(4000)

    def _append_log(self, line: str) -> None:
        line = strip_ansi(str(line))
        if not line:
            return
        with self._log_lock:
            self._logs.append(line)
        self._track_log_line(line)

    def _track_log_line(self, line: str) -> None:
        message = line.split("]: ", 1)[-1].strip()
        joined = JOIN_RE.match(message)
        if joined:
            self._players.add(joined.group(1).strip())
            return
        left = LEAVE_RE.match(message)
        if left:
            self._players.discard(left.group(1).strip())
            return
        version = VERSION_RE.search(line)
        if version:
            self._version = version.group(1)

    def logs_snapshot(self, limit: int = 300) -> list[str]:
        with self._log_lock:
            lines = list(self._logs)
        if not lines:
            log_file = SERVER_DIR / "server.log"
            if log_file.is_file():
                try:
                    with log_file.open("r", encoding="utf-8", errors="ignore") as handle:
                        lines = [strip_ansi(item) for item in deque(handle, maxlen=limit)]
                except OSError:
                    lines = []
        return lines[-limit:]

    def _read_meminfo(self) -> tuple[int, int, int]:
        try:
            values: dict[str, int] = {}
            with open("/proc/meminfo", "r", encoding="ascii", errors="ignore") as handle:
                for line in handle:
                    parts = line.split()
                    if len(parts) >= 2:
                        values[parts[0].rstrip(":")] = int(parts[1])
            total = values.get("MemTotal", 0)
            available = values.get("MemAvailable")
            if available is None:
                available = values.get("MemFree", 0) + values.get("Buffers", 0) + values.get("Cached", 0)
            used = max(0, total - available)
            return total // 1024, used // 1024, (used / total * 100.0) if total else 0.0
        except (OSError, ValueError, PermissionError):
            return 0, 0, 0.0

    def _cpu_percent(self) -> float:
        if is_termux():
            return 0.0
        try:
            with open("/proc/stat", "r", encoding="ascii", errors="ignore") as handle:
                fields = handle.readline().split()
            numbers = [int(value) for value in fields[1:]]
            if len(numbers) < 4:
                return 0.0
            idle = numbers[3] + (numbers[4] if len(numbers) > 4 else 0)
            total = sum(numbers)
            previous = self._cpu_previous
            self._cpu_previous = (total, idle)
            if not previous:
                return 0.0
            total_delta = total - previous[0]
            idle_delta = idle - previous[1]
            if total_delta <= 0:
                return 0.0
            return round(max(0.0, min(100.0, (1.0 - idle_delta / total_delta) * 100.0)), 1)
        except (OSError, ValueError, PermissionError):
            return 0.0

    def _process_memory_mb(self) -> float:
        if not self.process or self.process.poll() is not None:
            return 0.0
        try:
            status_file = Path(f"/proc/{self.process.pid}/status")
            for line in status_file.read_text(encoding="ascii", errors="ignore").splitlines():
                if line.startswith("VmRSS:"):
                    return round(int(line.split()[1]) / 1024.0, 1)
        except (OSError, ValueError, PermissionError):
            pass
        return 0.0

    def _uptime(self) -> tuple[int, str]:
        if not self.process or self.process.poll() is not None or not self._started_at:
            return 0, "Stopped"
        seconds = max(0, int(time.time() - self._started_at))
        hours, remainder = divmod(seconds, 3600)
        minutes, secs = divmod(remainder, 60)
        if hours:
            text = f"{hours}h {minutes}m"
        elif minutes:
            text = f"{minutes}m {secs}s"
        else:
            text = f"{secs}s"
        return seconds, text

    def status(self) -> dict:
        running = self.process is not None and self.process.poll() is None
        ram_total, ram_used, ram_percent = self._read_meminfo()
        try:
            disk = shutil.disk_usage(str(BASE_DIR))
            disk_used = disk.used / (1024**3)
            disk_total = disk.total / (1024**3)
            disk_percent = (disk.used / disk.total * 100.0) if disk.total else 0.0
        except (OSError, PermissionError):
            disk_used = disk_total = disk_percent = 0.0
        properties = self.get_properties()
        players = sorted(self._players, key=str.lower)
        uptime_seconds, uptime_text = self._uptime()
        return {
            "running": running,
            "type": self.server_type,
            "version": self._version,
            "port": properties.get("server-port", "19132"),
            "host": get_lan_ip(),
            "cpu": self._cpu_percent() if running else 0.0,
            "ram_used": ram_used,
            "ram_total": ram_total,
            "ram_percent": round(ram_percent, 1),
            "disk_used": round(disk_used, 1),
            "disk_total": round(disk_total, 1),
            "disk_percent": round(disk_percent, 1),
            "proc_mem_mb": self._process_memory_mb(),
            "players_online": players,
            "player_count": len(players),
            "max_players": properties.get("max-players", "20"),
            "uptime_seconds": uptime_seconds,
            "uptime": uptime_text,
            "platform": platform_label(),
            "pid": self.process.pid if running and self.process else None,
        }

    def _default_properties_text(self) -> str:
        lines = [
            "# PocketMine-MP Bedrock server properties",
            "# This server listens on UDP. Change values and restart the server.",
        ]
        for key, value in DEFAULT_PROPERTIES.items():
            lines.append(f"{key}={value}")
        return "\n".join(lines) + "\n"

    def _ensure_files(self) -> None:
        for directory in (SERVER_DIR, WORLDS_DIR, SERVER_DIR / "plugins", SERVER_DIR / "players"):
            directory.mkdir(parents=True, exist_ok=True)
        properties_file = SERVER_DIR / "server.properties"
        if not properties_file.exists():
            properties_file.write_text(self._default_properties_text(), encoding="utf-8")
        for name in ("ops.txt", "white-list.txt", "banned-players.txt", "banned-ips.txt"):
            path = SERVER_DIR / name
            if not path.exists():
                path.write_text("", encoding="utf-8")

    def get_properties(self) -> dict[str, str]:
        self._ensure_files()
        properties = dict(DEFAULT_PROPERTIES)
        try:
            for raw in (SERVER_DIR / "server.properties").read_text(encoding="utf-8", errors="ignore").splitlines():
                line = raw.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, value = line.split("=", 1)
                key = key.strip()
                if PROPERTY_KEY_RE.fullmatch(key):
                    properties[key] = value.strip()
        except OSError:
            pass
        return properties

    def save_properties(self, updates: dict[str, str]) -> tuple[bool, str]:
        clean: dict[str, str] = {}
        for key, value in updates.items():
            key = str(key).strip()
            value = str(value).replace("\r", "").replace("\n", "").strip()
            if not PROPERTY_KEY_RE.fullmatch(key):
                return False, f"Invalid property name: {key}"
            if key in BOOL_PROPERTIES:
                value = "true" if value.lower() in {"1", "true", "on", "yes"} else "false"
            clean[key] = value
        if not clean:
            return False, "No settings supplied"
        properties_file = SERVER_DIR / "server.properties"
        existing: list[str] = []
        if properties_file.exists():
            existing = properties_file.read_text(encoding="utf-8", errors="ignore").splitlines()
        used: set[str] = set()
        output: list[str] = []
        for raw in existing:
            stripped = raw.strip()
            if stripped and not stripped.startswith("#") and "=" in stripped:
                key = stripped.split("=", 1)[0].strip()
                if key in clean:
                    output.append(f"{key}={clean[key]}")
                    used.add(key)
                    continue
            output.append(raw)
        for key, value in clean.items():
            if key not in used:
                output.append(f"{key}={value}")
        temporary = properties_file.with_suffix(".tmp")
        temporary.write_text("\n".join(output).rstrip() + "\n", encoding="utf-8")
        temporary.replace(properties_file)
        self._ensure_files()
        return True, "Settings saved. Restart the server to apply changes."

    def _php_compatible(self, php_binary: Path | str) -> tuple[bool, list[str]]:
        php_path = Path(php_binary)
        try:
            cache_key = (str(php_path.resolve()), php_path.stat().st_mtime)
        except OSError:
            return False, ["PHP binary not found"]
        if cache_key in self._php_cache:
            return self._php_cache[cache_key]
        try:
            result = subprocess.run(
                [str(php_path), "-m"],
                capture_output=True,
                text=True,
                timeout=20,
                check=False,
            )
            modules = {
                line.strip().lower()
                for line in result.stdout.splitlines()
                if line.strip() and not line.startswith("[")
            }
            missing = sorted(REQUIRED_EXTENSIONS - modules)
            compatible = result.returncode == 0 and not missing
            value = (compatible, missing)
        except (OSError, subprocess.SubprocessError) as error:
            value = (False, [str(error)])
        self._php_cache[cache_key] = value
        return value

    def _bundled_php_path(self) -> Path | None:
        if self._bundled_php and self._bundled_php.exists():
            return self._bundled_php
        php_root = RUNTIME_DIR / "php"
        if not php_root.exists():
            return None
        candidates = sorted(php_root.rglob("bin/php"), key=lambda path: len(path.parts))
        for candidate in candidates:
            if candidate.is_file():
                self._bundled_php = candidate
                return candidate
        return None

    def _php_download_platform(self) -> tuple[str, str] | None:
        system = platform.system().lower()
        machine = platform.machine().lower()
        arch = "arm64" if machine in {"aarch64", "arm64"} else "x86_64" if machine in {"x86_64", "amd64"} else machine
        if is_termux():
            system = "android"
        key = (system, arch)
        return key if key in PM_PHP_URLS else None

    def _download_php_runtime(self) -> tuple[Path | None, str]:
        key = self._php_download_platform()
        if not key:
            return None, (
                "No official PocketMine PHP runtime is available for "
                f"{platform.system()} {platform.machine()}. Set MC_PHP_BINARY to a "
                "PocketMine-compatible PHP 8.x binary, or use Linux x86_64, Windows, "
                "macOS, or Termux ARM64."
            )
        url = PM_PHP_URLS[key]
        archive = RUNTIME_DIR / "pocketmine-php.tar.gz"
        destination = RUNTIME_DIR / "php"
        try:
            self._append_log(f"[panel] Downloading PocketMine PHP runtime for {key[0]}/{key[1]} ...")
            with requests.get(url, stream=True, timeout=(20, 300), headers={"User-Agent": "SlyxPanel/1.0"}) as response:
                response.raise_for_status()
                with archive.open("wb") as output:
                    for chunk in response.iter_content(chunk_size=1024 * 256):
                        if chunk:
                            output.write(chunk)
            if archive.stat().st_size < 1_000_000:
                raise RuntimeError("Downloaded PHP archive is incomplete")
            if destination.exists():
                shutil.rmtree(destination)
            destination.mkdir(parents=True, exist_ok=True)
            with tarfile.open(archive, "r:gz") as tar:
                try:
                    tar.extractall(destination, filter="data")
                except TypeError:
                    tar.extractall(destination)
            php_path = self._bundled_php_path()
            if not php_path:
                raise RuntimeError("PHP binary was not found in the downloaded archive")
            php_path.chmod(php_path.stat().st_mode | 0o111)
            compatible, missing = self._php_compatible(php_path)
            if not compatible:
                raise RuntimeError("Downloaded PHP is missing extensions: " + ", ".join(missing))
            self._append_log(f"[panel] PocketMine PHP ready: {php_path}")
            return php_path, "PocketMine PHP runtime downloaded"
        except Exception as error:
            self._append_log(f"[panel] PHP runtime download failed: {error}")
            return None, f"Could not download PocketMine PHP runtime: {error}"
        finally:
            try:
                archive.unlink(missing_ok=True)
            except OSError:
                pass

    def _resolve_php(self) -> tuple[Path | None, str]:
        configured = os.getenv("MC_PHP_BINARY", "").strip()
        if configured:
            path = Path(configured).expanduser()
            compatible, missing = self._php_compatible(path)
            if compatible:
                return path, "Configured PHP is ready"
            return None, f"MC_PHP_BINARY is missing extensions: {', '.join(missing)}"
        bundled = self._bundled_php_path()
        if bundled:
            compatible, missing = self._php_compatible(bundled)
            if compatible:
                return bundled, "Bundled PHP is ready"
            self._append_log(f"[panel] Bundled PHP is not usable: {', '.join(missing)}")
        system_php = shutil.which("php")
        if system_php:
            compatible, missing = self._php_compatible(system_php)
            if compatible:
                return Path(system_php), "System PHP is ready"
            self._append_log(f"[panel] System PHP is missing PocketMine extensions: {', '.join(missing)}")
        with self._download_lock:
            bundled = self._bundled_php_path()
            if bundled:
                compatible, _ = self._php_compatible(bundled)
                if compatible:
                    return bundled, "Bundled PHP is ready"
            downloaded, message = self._download_php_runtime()
            if downloaded:
                return downloaded, message
            if system_php:
                missing = self._php_compatible(system_php)[1]
                return None, (
                    f"{message}. System PHP cannot run PocketMine-MP; missing: "
                    f"{', '.join(missing)}"
                )
            return None, message

    def ensure_pocketmine(self, force: bool = False) -> tuple[bool, str]:
        if PHAR_PATH.is_file() and PHAR_PATH.stat().st_size > 1_000_000 and not force:
            return True, "PocketMine-MP is ready"
        temporary = SERVER_DIR / f".{PHAR_NAME}.part"
        try:
            self._append_log("[panel] Downloading PocketMine-MP.phar ...")
            with requests.get(POCKETMINE_URL, stream=True, timeout=(20, 300), headers={"User-Agent": "SlyxPanel/1.0"}) as response:
                response.raise_for_status()
                final_url = response.url
                with temporary.open("wb") as output:
                    for chunk in response.iter_content(chunk_size=1024 * 256):
                        if chunk:
                            output.write(chunk)
            if temporary.stat().st_size < 1_000_000:
                raise RuntimeError("Downloaded PocketMine-MP.phar is incomplete")
            temporary.replace(PHAR_PATH)
            version_match = re.search(r"/download/([^/]+)/", final_url)
            if version_match:
                self._version = version_match.group(1)
            self._append_log(f"[panel] PocketMine-MP.phar ready ({human_size(PHAR_PATH.stat().st_size)})")
            return True, "PocketMine-MP.phar downloaded"
        except Exception as error:
            self._append_log(f"[panel] PocketMine download failed: {error}")
            return False, (
                f"Could not download PocketMine-MP.phar: {error}. "
                f"Place {PHAR_NAME} in {SERVER_DIR} manually."
            )
        finally:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass

    def _start_unlocked(self) -> tuple[bool, str]:
        if self.process and self.process.poll() is None:
            return False, "Server is already running"
        php_path, php_message = self._resolve_php()
        if not php_path:
            return False, php_message
        phar_ok, phar_message = self.ensure_pocketmine()
        if not phar_ok:
            return False, phar_message
        self._ensure_files()
        self._players.clear()
        self._started_at = time.time()
        command = [str(php_path), PHAR_NAME, "--no-wizard"]
        environment = os.environ.copy()
        environment["PATH"] = f"{php_path.parent}{os.pathsep}{environment.get('PATH', '')}"
        php_ini = php_path.parent / "php.ini"
        if php_ini.is_file():
            environment["PHPRC"] = str(php_ini)
        try:
            self._append_log(f"[panel] Starting: {' '.join(command)}")
            self.process = subprocess.Popen(
                command,
                cwd=str(SERVER_DIR),
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
                env=environment,
                start_new_session=True,
            )
            threading.Thread(target=self._reader_loop, args=(self.process,), daemon=True, name="pocketmine-console").start()
            return True, "PocketMine-MP is starting"
        except Exception as error:
            self.process = None
            self._started_at = None
            self._append_log(f"[panel] Failed to start server: {error}")
            return False, f"Failed to start server: {error}"

    def start(self) -> tuple[bool, str]:
        with self._action_lock:
            return self._start_unlocked()

    def _reader_loop(self, process: subprocess.Popen) -> None:
        try:
            if process.stdout:
                for raw_line in iter(process.stdout.readline, ""):
                    if raw_line:
                        self._append_log(raw_line)
                    else:
                        break
        finally:
            code = process.poll()
            if code is not None:
                self._append_log(f"[panel] PocketMine-MP stopped (exit code {code})")
            self._players.clear()
            self._started_at = None

    def _stop_unlocked(self) -> tuple[bool, str]:
        process = self.process
        if not process or process.poll() is not None:
            self._players.clear()
            return False, "Server is not running"
        self._append_log("[panel] Sending stop command. Saving worlds ...")
        sent = self.send_command("stop")
        timeout = float(os.getenv("MC_STOP_TIMEOUT", "20"))
        deadline = time.time() + timeout
        while process.poll() is None and time.time() < deadline:
            time.sleep(0.25)
        if process.poll() is None:
            self._append_log("[panel] Graceful stop timed out, terminating process")
            try:
                process.terminate()
                process.wait(timeout=8)
            except (OSError, subprocess.TimeoutExpired):
                try:
                    process.kill()
                except OSError:
                    pass
        self._players.clear()
        self._started_at = None
        if sent:
            return True, "Server stopped"
        return True, "Server stopped (console was unavailable)"

    def stop(self) -> tuple[bool, str]:
        with self._action_lock:
            return self._stop_unlocked()

    def restart(self) -> tuple[bool, str]:
        with self._action_lock:
            if self.process and self.process.poll() is None:
                stop_ok, stop_message = self._stop_unlocked()
                if not stop_ok and "not running" not in stop_message.lower():
                    return False, stop_message
            time.sleep(0.8)
            return self._start_unlocked()

    def send_command(self, command: str) -> tuple[bool, str]:
        command = str(command).replace("\r", "").replace("\n", "").strip()
        if command.startswith("/"):
            command = command[1:]
        if not command:
            return False, "Empty command"
        if not self.process or self.process.poll() is not None or not self.process.stdin:
            return False, "Server is not running"
        try:
            self.process.stdin.write(command + "\n")
            self.process.stdin.flush()
            self._append_log(f"> {command}")
            return True, "Command sent"
        except (OSError, ValueError) as error:
            self._append_log(f"[panel] Command failed: {error}")
            return False, f"Command failed: {error}"

    def runtime_status(self) -> dict:
        bundled = self._bundled_php_path()
        system_php = Path(shutil.which("php")) if shutil.which("php") else None
        php_path = bundled or system_php
        php_ready = False
        missing: list[str] = []
        if php_path:
            php_ready, missing = self._php_compatible(php_path)
        return {
            "platform": platform_label(),
            "server_dir": str(SERVER_DIR),
            "phar": PHAR_PATH.is_file() and PHAR_PATH.stat().st_size > 1_000_000,
            "phar_size": human_size(PHAR_PATH.stat().st_size) if PHAR_PATH.is_file() else "0 B",
            "php": str(php_path) if php_path else "not installed",
            "php_ready": php_ready,
            "missing_extensions": missing,
            "bundled_php": str(bundled) if bundled else "",
            "download_available": self._php_download_platform() is not None,
        }

    def _next_backup_number(self) -> int:
        highest = 0
        if BACKUP_DIR.exists():
            for path in BACKUP_DIR.glob("backup_*.zip"):
                match = re.search(r"_#(\d+)\.zip$", path.name)
                if match:
                    highest = max(highest, int(match.group(1)))
        return highest + 1

    def _backup_excluded(self, path: Path) -> bool:
        relative = path.relative_to(SERVER_DIR)
        parts = set(relative.parts)
        if path.name == PHAR_NAME or path.name.startswith(f".{PHAR_NAME}"):
            return True
        if path.name in {"server.log", "server.log.gz", "crashdump.log"}:
            return True
        if "crashdumps" in parts:
            return True
        if path.suffix in {".lock", ".pid"}:
            return True
        return False

    def create_backup(self, label: str = "manual") -> tuple[bool, str]:
        with self._backup_lock:
            try:
                if self.process and self.process.poll() is None:
                    self.send_command("save-all")
                    time.sleep(1.2)
                timestamp = dt.datetime.now()
                number = self._next_backup_number()
                filename = f"backup_{timestamp.strftime('%Y-%m-%d_%H-%M')}_#{number}.zip"
                destination = BACKUP_DIR / filename
                temporary = destination.with_suffix(".zip.part")
                with zipfile.ZipFile(temporary, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
                    for root, directories, files in os.walk(SERVER_DIR):
                        directories[:] = [name for name in directories if name not in {"crashdumps", ".git"}]
                        for name in files:
                            path = Path(root) / name
                            try:
                                if self._backup_excluded(path):
                                    continue
                                archive.write(path, path.relative_to(SERVER_DIR).as_posix())
                            except (OSError, ValueError):
                                continue
                temporary.replace(destination)
                self._append_log(f"[panel] Backup created: {filename} ({human_size(destination.stat().st_size)})")
                self._upload_cloud(destination)
                return True, filename
            except Exception as error:
                self._append_log(f"[panel] Backup failed: {error}")
                return False, f"Backup failed: {error}"

    def _upload_cloud(self, path: Path) -> None:
        bucket = os.getenv("AWS_BUCKET_NAME", "").strip()
        if not bucket:
            return
        try:
            import boto3
        except ImportError:
            self._append_log("[panel] S3 skipped: boto3 is not installed")
            return
        try:
            client = boto3.client(
                "s3",
                aws_access_key_id=os.getenv("AWS_ACCESS_KEY_ID"),
                aws_secret_access_key=os.getenv("AWS_SECRET_ACCESS_KEY"),
                region_name=os.getenv("AWS_REGION", "us-east-1"),
            )
            client.upload_file(str(path), bucket, f"minecraft-backups/{path.name}")
            self._append_log(f"[panel] Backup uploaded to S3: {path.name}")
        except Exception as error:
            self._append_log(f"[panel] S3 upload failed: {error}")

    def list_backups(self) -> list[dict]:
        backups = []
        if not BACKUP_DIR.exists():
            return backups
        for path in BACKUP_DIR.glob("*.zip"):
            try:
                stat = path.stat()
            except OSError:
                continue
            backups.append(
                {
                    "name": path.name,
                    "size": stat.st_size,
                    "size_mb": round(stat.st_size / (1024 * 1024), 2),
                    "size_h": human_size(stat.st_size),
                    "date": dt.datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M"),
                    "mtime": stat.st_mtime,
                }
            )
        return sorted(backups, key=lambda item: item["mtime"], reverse=True)

    def backup_path(self, filename: str) -> Path | None:
        name = Path(filename).name
        if not name.endswith(".zip"):
            return None
        path = BACKUP_DIR / name
        try:
            resolved = path.resolve()
            if resolved.parent != BACKUP_DIR.resolve():
                return None
        except OSError:
            return None
        return path if path.is_file() else None

    def delete_backup(self, filename: str) -> tuple[bool, str]:
        path = self.backup_path(filename)
        if not path:
            return False, "Backup not found"
        try:
            path.unlink()
            return True, f"Deleted {path.name}"
        except OSError as error:
            return False, f"Could not delete backup: {error}"

    def restore_backup(self, filename: str) -> tuple[bool, str]:
        path = self.backup_path(filename)
        if not path:
            return False, "Backup not found"
        with self._action_lock:
            if self.process and self.process.poll() is None:
                stop_ok, stop_message = self._stop_unlocked()
                if not stop_ok:
                    return False, f"Could not stop server before restore: {stop_message}"
            safety_ok, safety_message = self.create_backup("pre-restore")
            if not safety_ok:
                return False, f"Could not create safety backup: {safety_message}"
            temporary = Path(tempfile.mkdtemp(prefix="restore_", dir=str(BASE_DIR)))
            try:
                safe_extract_zip(path, temporary)
                for entry in list(temporary.iterdir()):
                    if entry.name == PHAR_NAME:
                        continue
                    target = SERVER_DIR / entry.name
                    if target.is_dir():
                        shutil.rmtree(target)
                    elif target.exists():
                        target.unlink()
                    shutil.move(str(entry), str(target))
                self._ensure_files()
                self._append_log(f"[panel] Restored backup: {path.name}")
                return True, f"Restored {path.name}. Safety backup: {safety_message}"
            except Exception as error:
                self._append_log(f"[panel] Restore failed: {error}")
                return False, f"Restore failed: {error}"
            finally:
                shutil.rmtree(temporary, ignore_errors=True)

    def _world_path(self, name: str) -> Path | None:
        if not WORLD_NAME_RE.fullmatch(name):
            return None
        for candidate in (WORLDS_DIR / name, SERVER_DIR / name):
            if candidate.is_dir() and looks_like_world(candidate):
                return candidate
        return None

    def list_worlds(self) -> list[dict]:
        properties = self.get_properties()
        active = properties.get("level-name", "world").strip()
        found: dict[str, Path] = {}
        if WORLDS_DIR.is_dir():
            for path in WORLDS_DIR.iterdir():
                if path.is_dir() and not path.is_symlink():
                    found[path.name] = path
        for path in SERVER_DIR.iterdir():
            if not path.is_dir() or path.is_symlink() or path.name in {"worlds", "plugins", "players", "resource_packs", "crashdumps"}:
                continue
            if looks_like_world(path):
                found.setdefault(path.name, path)
        worlds = []
        for name, path in found.items():
            try:
                files = [item for item in path.rglob("*") if item.is_file()]
                size = sum(item.stat().st_size for item in files)
            except OSError:
                files = []
                size = 0
            worlds.append(
                {
                    "name": name,
                    "size": size,
                    "size_mb": round(size / (1024 * 1024), 2),
                    "size_h": human_size(size),
                    "files": len(files),
                    "active": name == active,
                }
            )
        return sorted(worlds, key=lambda item: (not item["active"], item["name"].lower()))

    def create_world(self, name: str, seed: str = "", gamemode: str = "survival", difficulty: str = "normal", level_type: str = "DEFAULT") -> tuple[bool, str]:
        name = name.strip()
        if not WORLD_NAME_RE.fullmatch(name):
            return False, "World name must use letters, numbers, - or _ (max 32 characters)"
        if self._world_path(name) or (WORLDS_DIR / name).exists() or (SERVER_DIR / name).exists():
            return False, f"World '{name}' already exists"
        input_gamemode = gamemode.strip().lower() or "survival"
        input_difficulty = difficulty.strip().lower() or "normal"
        input_level_type = level_type.strip().upper() or "DEFAULT"
        if input_gamemode not in {"survival", "creative", "adventure", "spectator"}:
            return False, "Invalid game mode"
        if input_difficulty not in {"peaceful", "easy", "normal", "hard"}:
            return False, "Invalid difficulty"
        if input_level_type not in {"DEFAULT", "FLAT", "VOID"}:
            return False, "Invalid world type"
        updates = {
            "level-name": name,
            "level-seed": seed.strip(),
            "gamemode": input_gamemode,
            "difficulty": input_difficulty,
            "level-type": input_level_type,
        }
        ok, message = self.save_properties(updates)
        if not ok:
            return False, message
        return True, f"World '{name}' configured. Restart the server to generate it."

    def create_world_zip(self, name: str) -> Path | None:
        world_path = self._world_path(name)
        if not world_path:
            return None
        destination = UPLOAD_DIR / f"world_{world_path.name}_{int(time.time())}.zip"
        with zipfile.ZipFile(destination, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
            for root, _, files in os.walk(world_path):
                for filename in files:
                    path = Path(root) / filename
                    archive.write(path, path.relative_to(world_path).as_posix())
        return destination

    def install_world_zip(self, archive_path: Path, name: str, replace: bool = False) -> tuple[bool, str]:
        name = (name or archive_path.stem).strip()
        if not WORLD_NAME_RE.fullmatch(name):
            return False, "World name must use letters, numbers, - or _ (max 32 characters)"
        if self.process and self.process.poll() is None:
            return False, "Stop the server before uploading a world"
        temporary = Path(tempfile.mkdtemp(prefix="world_upload_", dir=str(UPLOAD_DIR)))
        extracted = temporary / "content"
        try:
            safe_extract_zip(archive_path, extracted)
            if looks_like_world(extracted):
                source = extracted
            else:
                direct = [entry for entry in extracted.iterdir() if entry.is_dir()]
                if len(direct) == 1 and looks_like_world(direct[0]):
                    source = direct[0]
                else:
                    matches = [
                        path
                        for path in extracted.rglob("*")
                        if path.is_dir() and ((path / "level.dat").is_file() or (path / "levelname.txt").is_file())
                    ]
                    source = min(matches, key=lambda path: len(path.parts)) if matches else None
            if not source:
                return False, "ZIP does not contain a PocketMine world (level.dat or levelname.txt not found)"
            target = WORLDS_DIR / name
            if target.exists():
                if not replace:
                    return False, f"World '{name}' already exists. Enable replace to overwrite it."
                safety = BACKUP_DIR / f"world_{name}_{dt.datetime.now().strftime('%Y-%m-%d_%H-%M-%S')}.zip"
                with zipfile.ZipFile(safety, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as backup_zip:
                    for root, _, files in os.walk(target):
                        for filename in files:
                            path = Path(root) / filename
                            backup_zip.write(path, path.relative_to(target).as_posix())
                shutil.rmtree(target)
            shutil.move(str(source), str(target))
            return True, f"World '{name}' uploaded. Set it active in Settings or create it by name."
        except Exception as error:
            return False, f"World upload failed: {error}"
        finally:
            shutil.rmtree(temporary, ignore_errors=True)

    def activate_world(self, name: str) -> tuple[bool, str]:
        if not self._world_path(name):
            return False, "World not found"
        return self.save_properties({"level-name": name})

    def delete_world(self, name: str) -> tuple[bool, str]:
        world_path = self._world_path(name)
        if not world_path:
            return False, "World not found"
        if self.process and self.process.poll() is None:
            return False, "Stop the server before deleting a world"
        properties = self.get_properties()
        if properties.get("level-name") == name:
            return False, "The active world cannot be deleted. Activate another world first."
        safety = BACKUP_DIR / f"world_{name}_{dt.datetime.now().strftime('%Y-%m-%d_%H-%M-%S')}.zip"
        try:
            with zipfile.ZipFile(safety, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as backup_zip:
                for root, _, files in os.walk(world_path):
                    for filename in files:
                        path = Path(root) / filename
                        backup_zip.write(path, path.relative_to(world_path).as_posix())
            shutil.rmtree(world_path)
            return True, f"World '{name}' deleted. Safety copy: {safety.name}"
        except OSError as error:
            return False, f"Could not delete world: {error}"

    def _read_names(self, filename: str) -> list[str]:
        path = SERVER_DIR / filename
        if not path.is_file():
            return []
        names = []
        try:
            for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
                name = line.strip()
                if name and not name.startswith("#") and PLAYER_NAME_RE.fullmatch(name):
                    names.append(name)
        except OSError:
            pass
        return names

    def _write_names(self, filename: str, names: list[str]) -> None:
        seen: set[str] = set()
        output = []
        for name in names:
            key = name.lower()
            if key not in seen and PLAYER_NAME_RE.fullmatch(name):
                seen.add(key)
                output.append(name)
        path = SERVER_DIR / filename
        temporary = path.with_suffix(".tmp")
        temporary.write_text("\n".join(output) + ("\n" if output else ""), encoding="utf-8")
        temporary.replace(path)

    def _update_name_file(self, filename: str, name: str, add: bool) -> None:
        names = self._read_names(filename)
        filtered = [item for item in names if item.lower() != name.lower()]
        if add:
            filtered.append(name)
        self._write_names(filename, filtered)

    def get_players(self) -> list[dict]:
        whitelist = self._read_names("white-list.txt") or self._read_names("whitelist.txt")
        operators = self._read_names("ops.txt")
        banned = self._read_names("banned-players.txt")
        online = {name.lower(): name for name in self._players}
        players: dict[str, dict] = {}

        def merge(name: str, field: str) -> None:
            key = name.lower()
            if key not in players:
                players[key] = {
                    "name": online.get(key, name),
                    "whitelisted": False,
                    "op": False,
                    "banned": False,
                    "online": key in online,
                }
            players[key][field] = True

        for name in whitelist:
            merge(name, "whitelisted")
        for name in operators:
            merge(name, "op")
        for name in banned:
            merge(name, "banned")
        for key, name in online.items():
            if key not in players:
                players[key] = {
                    "name": name,
                    "whitelisted": False,
                    "op": False,
                    "banned": False,
                    "online": True,
                }
        return sorted(players.values(), key=lambda item: (not item["online"], item["name"].lower()))

    def player_action(self, name: str, action: str) -> tuple[bool, str]:
        name = name.strip()
        if not PLAYER_NAME_RE.fullmatch(name) or name != " ".join(name.split()):
            return False, "Invalid player name"
        commands = {
            "whitelist_add": f"whitelist add {name}",
            "whitelist_remove": f"whitelist remove {name}",
            "op": f"op {name}",
            "deop": f"deop {name}",
            "ban": f"ban {name}",
            "pardon": f"unban {name}",
            "kick": f"kick {name}",
        }
        if action not in commands:
            return False, "Unknown player action"
        running = self.process is not None and self.process.poll() is None
        if running:
            ok, message = self.send_command(commands[action])
            if not ok:
                return False, message
            if action == "kick":
                self._players.discard(name)
            return True, f"{action.replace('_', ' ')} sent for {name}"
        if action == "kick":
            return False, "Server is not running, so the player cannot be kicked"
        if action == "whitelist_add":
            self._update_name_file("white-list.txt", name, True)
        elif action == "whitelist_remove":
            self._update_name_file("white-list.txt", name, False)
        elif action == "op":
            self._update_name_file("ops.txt", name, True)
        elif action == "deop":
            self._update_name_file("ops.txt", name, False)
        elif action == "ban":
            self._update_name_file("banned-players.txt", name, True)
        elif action == "pardon":
            self._update_name_file("banned-players.txt", name, False)
        return True, f"{action.replace('_', ' ')} applied to {name}"

    def _safe_path(self, relative: str, must_exist: bool = False) -> Path:
        relative = (relative or "").replace("\\", "/").lstrip("/")
        target = (SERVER_DIR / relative).resolve()
        base = SERVER_DIR.resolve()
        if target != base and base not in target.parents:
            raise ValueError("Path is outside the server directory")
        if must_exist and not target.exists():
            raise FileNotFoundError(relative)
        return target

    def list_files(self, relative: str = "") -> tuple[list[dict], str]:
        target = self._safe_path(relative)
        if target.is_file():
            target = target.parent
        items = []
        if target.is_dir():
            for path in sorted(target.iterdir(), key=lambda item: (not item.is_dir(), item.name.lower())):
                try:
                    stat = path.stat()
                    size = stat.st_size if path.is_file() else 0
                except OSError:
                    size = 0
                items.append(
                    {
                        "name": path.name,
                        "is_dir": path.is_dir(),
                        "is_link": path.is_symlink(),
                        "size": size,
                        "size_h": human_size(size),
                        "path": path.relative_to(SERVER_DIR).as_posix(),
                    }
                )
        current = target.relative_to(SERVER_DIR).as_posix()
        return items, "" if current == "." else current

    def read_file(self, relative: str, max_size: int = 1_000_000) -> tuple[bool, str]:
        try:
            path = self._safe_path(relative, must_exist=True)
            if not path.is_file():
                return False, "Not a file"
            if path.stat().st_size > max_size:
                return False, f"File is too large to edit (limit {human_size(max_size)})"
            data = path.read_bytes()
            if b"\x00" in data[:8192]:
                return False, "Binary files cannot be edited here"
            return True, data.decode("utf-8", errors="replace")
        except (OSError, ValueError) as error:
            return False, str(error)

    def write_file(self, relative: str, content: str) -> tuple[bool, str]:
        try:
            path = self._safe_path(relative)
            if path == SERVER_DIR.resolve() or path.is_dir():
                return False, "Cannot write to this path"
            path.parent.mkdir(parents=True, exist_ok=True)
            temporary = path.with_name(f".{path.name}.tmp")
            temporary.write_text(content, encoding="utf-8")
            temporary.replace(path)
            return True, f"Saved {relative}"
        except (OSError, ValueError) as error:
            return False, f"Could not save file: {error}"

    def upload_file(self, directory: str, filename: str, data: bytes) -> tuple[bool, str]:
        safe_name = Path(filename).name
        if not safe_name or safe_name in {".", ".."}:
            return False, "Invalid filename"
        try:
            target_dir = self._safe_path(directory)
            if not target_dir.is_dir():
                return False, "Upload target is not a directory"
            target = self._safe_path((Path(directory) / safe_name).as_posix())
            if target.is_dir():
                return False, "A directory with this name already exists"
            target.write_bytes(data)
            return True, f"Uploaded {safe_name}"
        except (OSError, ValueError) as error:
            return False, f"Upload failed: {error}"

    def save_uploaded_file(self, directory: str, filename: str, source_path: Path) -> tuple[bool, str]:
        safe_name = Path(filename).name
        if not safe_name or safe_name in {".", ".."}:
            return False, "Invalid filename"
        try:
            target_dir = self._safe_path(directory)
            if not target_dir.is_dir():
                return False, "Upload target is not a directory"
            target = self._safe_path((Path(directory) / safe_name).as_posix())
            if target.is_dir():
                return False, "A directory with this name already exists"
            shutil.move(str(source_path), str(target))
            return True, f"Uploaded {safe_name}"
        except (OSError, ValueError) as error:
            return False, f"Upload failed: {error}"

    def delete_path(self, relative: str) -> tuple[bool, str]:
        try:
            path = self._safe_path(relative, must_exist=True)
            if path == SERVER_DIR.resolve():
                return False, "Cannot delete the server directory"
            if path.is_dir():
                shutil.rmtree(path)
            else:
                path.unlink()
            return True, f"Deleted {relative}"
        except (OSError, ValueError) as error:
            return False, f"Delete failed: {error}"

    def download_path(self, relative: str) -> Path | None:
        try:
            path = self._safe_path(relative, must_exist=True)
            return path if path.is_file() else None
        except (OSError, ValueError):
            return None

    def start_background_tasks(self) -> None:
        if self._auto_started:
            return
        self._auto_started = True
        threading.Thread(target=self._auto_backup_loop, daemon=True, name="daily-backup").start()

    def _auto_backup_loop(self) -> None:
        while not self._auto_stop.wait(25):
            try:
                hour = int(os.getenv("BACKUP_HOUR", "3"))
                now = dt.datetime.now()
                today = now.strftime("%Y-%m-%d")
                if now.hour == hour and self._last_daily_backup != today:
                    self._last_daily_backup = today
                    ok, message = self.create_backup("daily")
                    self._append_log(f"[panel] Daily backup: {'ok' if ok else 'failed'} - {message}")
            except Exception as error:
                self._append_log(f"[panel] Daily backup error: {error}")


manager = ServerManager()
manager.start_background_tasks()
