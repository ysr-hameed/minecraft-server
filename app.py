from __future__ import annotations

import atexit
import hmac
import os
import posixpath
import time
from functools import wraps
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().with_name(".env"))

from flask import (
    Flask,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    send_file,
    session,
    url_for,
)
from werkzeug.utils import secure_filename

from mc_core import SERVER_DIR, UPLOAD_DIR, get_lan_ip, manager

app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", "slyxpanel-bedrock-secret")
app.config["MAX_CONTENT_LENGTH"] = int(os.getenv("MC_MAX_UPLOAD_MB", "1024")) * 1024 * 1024
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"

ADMIN_PASSWORD = os.getenv("MC_PANEL_PASSWORD", "admin123")
FIELD_GROUPS = [
    (
        "Network",
        [
            ("server-port", "Server port (UDP)", "number"),
            ("server-portv6", "IPv6 port (UDP)", "number"),
            ("enable-ipv6", "Enable IPv6", "bool"),
            ("server-ip", "Bind IPv4 address", "text"),
            ("server-ipv6", "Bind IPv6 address", "text"),
            ("enable-query", "Enable query", "bool"),
            ("query.port", "Query port", "number"),
        ],
    ),
    (
        "Server",
        [
            ("motd", "Server name / MOTD", "text"),
            ("max-players", "Max players", "number"),
            ("white-list", "Whitelist enabled", "bool"),
            ("xbox-auth", "Xbox Live authentication", "bool"),
            ("language", "Language", "text"),
        ],
    ),
    (
        "Gameplay",
        [
            ("gamemode", "Default game mode", "gamemode"),
            ("difficulty", "Difficulty", "difficulty"),
            ("force-gamemode", "Force game mode", "bool"),
            ("hardcore", "Hardcore", "bool"),
            ("pvp", "PvP", "bool"),
            ("level-name", "Active world", "text"),
            ("level-seed", "World seed", "text"),
            ("level-type", "World type", "leveltype"),
            ("view-distance", "View distance", "number"),
            ("auto-save", "Auto save", "bool"),
        ],
    ),
]
GROUPED_KEYS = {key for _, fields in FIELD_GROUPS for key, _, _ in fields}


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not session.get("logged_in"):
            return redirect(url_for("login", next=request.path))
        return view(*args, **kwargs)

    return wrapped


def flash_result(result, success_text: str | None = None) -> None:
    ok, message = result
    flash(success_text or message if ok else message, "success" if ok else "error")


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        password = request.form.get("password", "")
        if hmac.compare_digest(password, ADMIN_PASSWORD):
            session.clear()
            session["logged_in"] = True
            session.permanent = True
            target = request.args.get("next", "")
            if target.startswith("/") and not target.startswith("//"):
                return redirect(target)
            return redirect(url_for("dashboard"))
        flash("Wrong password", "error")
    return render_template("login.html")


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.route("/")
@login_required
def dashboard():
    stats = manager.status()
    properties = manager.get_properties()
    backups = manager.list_backups()[:5]
    runtime = manager.runtime_status()
    host_ip = request.host.split(":")[0]
    lan_ip = get_lan_ip()
    hosts = []
    for host in (lan_ip, host_ip, "127.0.0.1"):
        if host and host not in hosts:
            hosts.append(host)
    return render_template(
        "dashboard.html",
        stats=stats,
        props=properties,
        backups=backups,
        runtime=runtime,
        hosts=hosts,
        server_type=manager.server_type,
        version=manager.version,
    )


@app.route("/api/stats")
@login_required
def api_stats():
    return jsonify(manager.status())


@app.route("/healthz")
def healthz():
    return jsonify({"status": "ok", "server": manager.server_type})


@app.route("/console", methods=["GET", "POST"])
@login_required
def console():
    if request.method == "POST":
        command = request.form.get("command", "")
        flash_result(manager.send_command(command))
        return redirect(url_for("console"))
    status = manager.status()
    return render_template(
        "console.html",
        logs=manager.logs_snapshot(300),
        running=status["running"],
        status=status,
        stats=status,
    )


@app.route("/api/logs")
@login_required
def api_logs():
    status = manager.status()
    return jsonify(
        {
            "logs": manager.logs_snapshot(300),
            "running": status["running"],
            "player_count": status["player_count"],
            "players": status["players_online"],
            "version": status["version"],
            "port": status["port"],
            "uptime": status["uptime"],
        }
    )


@app.route("/server/<action>", methods=["POST"])
@login_required
def server_action(action):
    actions = {
        "start": manager.start,
        "stop": manager.stop,
        "restart": manager.restart,
    }
    handler = actions.get(action)
    if not handler:
        flash("Unknown server action", "error")
    else:
        flash_result(handler())
    return redirect(request.referrer or url_for("dashboard"))


@app.route("/server/download-pocketmine", methods=["POST"])
@login_required
def download_pocketmine():
    flash_result(manager.ensure_pocketmine(force=True))
    return redirect(request.referrer or url_for("settings"))


@app.route("/server/version", methods=["POST"])
@login_required
def switch_server_version():
    flash_result(manager.switch_pocketmine_version(request.form.get("version", "")))
    return redirect(request.referrer or url_for("settings"))


@app.route("/server/version/auto", methods=["POST"])
@login_required
def toggle_version_auto():
    enabled = request.form.get("enabled") in {"on", "true", "1", "yes"}
    flash_result(manager.set_pocketmine_auto_update(enabled))
    return redirect(request.referrer or url_for("settings"))


@app.route("/worlds", methods=["GET", "POST"])
@login_required
def worlds():
    if request.method == "POST":
        action = request.form.get("action", "")
        if action == "create":
            result = manager.create_world(
                request.form.get("name", ""),
                request.form.get("seed", ""),
                request.form.get("gamemode", "survival"),
                request.form.get("difficulty", "normal"),
                request.form.get("level_type", "DEFAULT"),
            )
            flash_result(result)
        elif action == "backup":
            ok, message = manager.create_backup("manual")
            flash(f"Backup created: {message}" if ok else message, "success" if ok else "error")
        elif action == "restore":
            flash_result(manager.restore_backup(request.form.get("backup", "")))
        elif action == "delete_backup":
            flash_result(manager.delete_backup(request.form.get("backup", "")))
        elif action == "delete_world":
            flash_result(manager.delete_world(request.form.get("world", "")))
        elif action == "activate_world":
            flash_result(manager.activate_world(request.form.get("world", "")))
        else:
            flash("Unknown world action", "error")
        return redirect(url_for("worlds"))
    return render_template(
        "worlds.html",
        worlds=manager.list_worlds(),
        backups=manager.list_backups(),
    )


@app.route("/world/upload", methods=["POST"])
@login_required
def upload_world():
    uploaded = request.files.get("world_zip")
    if not uploaded or not uploaded.filename:
        flash("Choose a .zip world file", "error")
        return redirect(url_for("worlds"))
    original = Path(uploaded.filename).name
    if not original.lower().endswith(".zip"):
        flash("World uploads must be .zip files", "error")
        return redirect(url_for("worlds"))
    world_name = request.form.get("world_name", "").strip() or Path(original).stem
    replace = request.form.get("replace") == "on"
    temporary = UPLOAD_DIR / f"world_upload_{int(time.time())}_{secure_filename(original) or 'world.zip'}"
    try:
        uploaded.save(temporary)
        flash_result(manager.install_world_zip(temporary, world_name, replace))
    finally:
        temporary.unlink(missing_ok=True)
    return redirect(url_for("worlds"))


@app.route("/world/download/<name>")
@login_required
def download_world(name):
    archive = manager.create_world_zip(name)
    if not archive:
        flash("World not found", "error")
        return redirect(url_for("worlds"))

    response = send_file(archive, as_attachment=True, download_name=f"{name}.zip")
    response.call_on_close(lambda: archive.unlink(missing_ok=True))
    return response


@app.route("/backup/download/<name>")
@login_required
def download_backup(name):
    path = manager.backup_path(name)
    if not path:
        flash("Backup not found", "error")
        return redirect(url_for("backups_page"))
    return send_file(path, as_attachment=True, download_name=path.name)


@app.route("/players", methods=["GET", "POST"])
@login_required
def players():
    if request.method == "POST":
        flash_result(
            manager.player_action(
                request.form.get("player", ""),
                request.form.get("action", ""),
            )
        )
        return redirect(url_for("players"))
    query = request.args.get("q", "").strip().lower()
    selected_filter = request.args.get("filter", "all")
    all_players = manager.get_players()
    if query:
        all_players = [player for player in all_players if query in player["name"].lower()]
    if selected_filter == "op":
        all_players = [player for player in all_players if player["op"]]
    elif selected_filter == "banned":
        all_players = [player for player in all_players if player["banned"]]
    elif selected_filter == "whitelisted":
        all_players = [player for player in all_players if player["whitelisted"]]
    elif selected_filter == "online":
        all_players = [player for player in all_players if player["online"]]
    status = manager.status()
    return render_template(
        "players.html",
        players=all_players,
        query=query,
        selected_filter=selected_filter,
        online_count=status["player_count"],
    )


@app.route("/settings", methods=["GET", "POST"])
@login_required
def settings():
    if request.method == "POST":
        updates = {}
        for key, value in request.form.items():
            if key.startswith("prop__"):
                updates[key[6:]] = value
        numeric_limits = {
            "server-port": (1, 65535),
            "server-portv6": (1, 65535),
            "query.port": (1, 65535),
            "rcon.port": (1, 65535),
            "max-players": (1, 500),
            "view-distance": (4, 32),
        }
        for key, (minimum, maximum) in numeric_limits.items():
            if key not in updates:
                continue
            try:
                number = int(updates[key])
            except (TypeError, ValueError):
                flash(f"{key} must be a number", "error")
                return redirect(url_for("settings"))
            if not minimum <= number <= maximum:
                flash(f"{key} must be between {minimum} and {maximum}", "error")
                return redirect(url_for("settings"))
            updates[key] = str(number)
        if "motd" in updates:
            updates["motd"] = updates["motd"].strip()[:128] or "SlyxCraft Bedrock Server"
        if not updates:
            flash("No settings supplied", "error")
            return redirect(url_for("settings"))
        flash_result(manager.save_properties(updates))
        return redirect(url_for("settings"))
    properties = manager.get_properties()
    extra_properties = {key: value for key, value in properties.items() if key not in GROUPED_KEYS}
    return render_template(
        "settings.html",
        props=properties,
        field_groups=FIELD_GROUPS,
        extra_properties=extra_properties,
        runtime=manager.runtime_status(),
        versions=manager.version_options(),
        selected_version=manager.selected_version,
        auto_update=manager.auto_update_enabled(),
        server_dir=str(SERVER_DIR),
    )


@app.route("/files")
@login_required
def files():
    relative = request.args.get("path", "")
    edit_path = request.args.get("edit", "")
    try:
        items, current = manager.list_files(relative)
    except (OSError, ValueError) as error:
        flash(f"Cannot open folder: {error}", "error")
        return redirect(url_for("files"))
    content = ""
    if edit_path:
        ok, content = manager.read_file(edit_path)
        if not ok:
            flash(content, "error")
            content = ""
            edit_path = ""
    parent = posixpath.dirname(current)
    return render_template(
        "files.html",
        items=items,
        current=current,
        parent=parent,
        edit_path=edit_path,
        content=content,
    )


@app.route("/files/save", methods=["POST"])
@login_required
def files_save():
    relative = request.form.get("path", "")
    flash_result(manager.write_file(relative, request.form.get("content", "")))
    return redirect(url_for("files", path=posixpath.dirname(relative), edit=relative))


@app.route("/files/upload", methods=["POST"])
@login_required
def files_upload():
    uploaded = request.files.get("file")
    directory = request.form.get("path", "")
    if not uploaded or not uploaded.filename:
        flash("Choose a file to upload", "error")
        return redirect(url_for("files", path=directory))
    temporary = UPLOAD_DIR / f"file_upload_{int(time.time())}_{secure_filename(uploaded.filename) or 'upload.bin'}"
    try:
        uploaded.save(temporary)
        flash_result(manager.save_uploaded_file(directory, uploaded.filename, temporary))
    finally:
        temporary.unlink(missing_ok=True)
    return redirect(url_for("files", path=directory))


@app.route("/files/delete", methods=["POST"])
@login_required
def files_delete():
    relative = request.form.get("path", "")
    parent = posixpath.dirname(relative)
    flash_result(manager.delete_path(relative))
    return redirect(url_for("files", path=parent))


@app.route("/files/download")
@login_required
def files_download():
    relative = request.args.get("path", "")
    path = manager.download_path(relative)
    if not path:
        flash("File not found", "error")
        return redirect(url_for("files", path=posixpath.dirname(relative)))
    return send_file(path, as_attachment=True, download_name=path.name)


@app.route("/backups")
@login_required
def backups_page():
    return render_template(
        "backups.html",
        backups=manager.list_backups(),
        backup_hour=os.getenv("BACKUP_HOUR", "3"),
    )


@app.route("/api/host")
@login_required
def api_host():
    return jsonify({"host": request.host.split(":")[0], "lan": get_lan_ip()})


@app.errorhandler(413)
def too_large(_error):
    flash("Upload is larger than the configured limit", "error")
    return redirect(request.referrer or url_for("dashboard"))


if __name__ == "__main__":
    atexit.register(manager.stop)
    host = os.getenv("MC_PANEL_HOST", "0.0.0.0")
    port = int(os.getenv("MC_PANEL_PORT", "5000"))
    print(f"Panel password: {ADMIN_PASSWORD}")
    print(f"Server directory: {SERVER_DIR}")
    print(f"Open http://localhost:{port} and connect from Minecraft Bedrock on UDP 19132")
    app.run(host=host, port=port, debug=False, threaded=True, use_reloader=False)
