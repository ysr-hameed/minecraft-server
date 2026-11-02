
import os, json, zipfile, shutil, pathlib
from flask import Flask, render_template, request, redirect, url_for, session, jsonify, send_file, flash
from dotenv import load_dotenv
load_dotenv()
from mc_core import manager, SERVER_DIR, BACKUP_DIR, UPLOAD_DIR, BASE_DIR
# psutil removed for Termux

app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY","slyxpanel-secret-2026")
app.config['MAX_CONTENT_LENGTH'] = 1024*1024*1024  # 1GB upload

ADMIN_PASSWORD = os.getenv("MC_PANEL_PASSWORD","admin123")

def login_required(f):
    from functools import wraps
    @wraps(f)
    def wrap(*a, **kw):
        if not session.get("logged_in"):
            return redirect(url_for("login"))
        return f(*a, **kw)
    return wrap

@app.route("/login", methods=["GET","POST"])
def login():
    if request.method=="POST":
        pw=request.form.get("password","")
        if pw==ADMIN_PASSWORD:
            session["logged_in"]=True
            return redirect(url_for("dashboard"))
        flash("Wrong password")
    return render_template("login.html")

@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))

@app.route("/")
@login_required
def dashboard():
    stats=manager.status()
    props=manager.get_properties()
    backups=manager.list_backups()[:5]
    # public IP guess
    host_ip=request.host.split(":")[0]
    return render_template("dashboard.html", stats=stats, props=props, backups=backups, host_ip=host_ip, server_type=manager.server_type, version=manager.version)

@app.route("/api/stats")
@login_required
def api_stats():
    return jsonify(manager.status())

# Console
@app.route("/console", methods=["GET","POST"])
@login_required
def console():
    if request.method=="POST":
        cmd=request.form.get("command","").strip()
        if cmd:
            manager.send_command(cmd)
        return redirect(url_for("console"))
    logs=manager.log_lines[-300:]
    return render_template("console.html", logs=logs, running=manager.status()["running"])

@app.route("/api/logs")
@login_required
def api_logs():
    return jsonify({"logs": manager.log_lines[-300:], "running": manager.status()["running"]})

@app.route("/server/<action>", methods=["POST"])
@login_required
def server_action(action):
    if action=="start":
        ok,msg=manager.start()
    elif action=="stop":
        ok,msg=manager.stop()
    elif action=="restart":
        ok,msg=manager.restart()
    else:
        ok,msg=False,"Unknown"
    flash(msg)
    return redirect(url_for("dashboard"))

# Worlds
@app.route("/worlds", methods=["GET","POST"])
@login_required
def worlds():
    if request.method=="POST":
        action=request.form.get("action")
        if action=="create":
            name=request.form.get("name","world2").strip()
            seed=request.form.get("seed","")
            gm=request.form.get("gamemode","survival")
            diff=request.form.get("difficulty","normal")
            manager.create_world(name, seed, gm, diff)
            flash(f"World '{name}' set as active. Restart server to generate.")
        elif action=="backup":
            fname=manager.create_backup("manual")
            flash(f"Backup created: {fname}")
        elif action=="restore":
            fname=request.form.get("backup")
            # restore logic
            path=BACKUP_DIR/fname
            if path.exists():
                # unzip to server dir
                with zipfile.ZipFile(path) as z: z.extractall(SERVER_DIR)
                flash(f"Restored {fname}")
        elif action=="delete_backup":
            fname=request.form.get("backup")
            (BACKUP_DIR/fname).unlink(missing_ok=True)
            flash("Backup deleted")
    worlds_list=manager.list_worlds()
    backups=manager.list_backups()
    return render_template("worlds.html", worlds=worlds_list, backups=backups)

@app.route("/world/upload", methods=["POST"])
@login_required
def upload_world():
    f=request.files.get("world_zip")
    if f and f.filename.endswith(".zip"):
        path=UPLOAD_DIR/f.filename
        f.save(path)
        # unzip to server dir as new world folder
        world_name=request.form.get("world_name") or pathlib.Path(f.filename).stem
        target=SERVER_DIR/world_name
        if target.exists(): shutil.rmtree(target)
        with zipfile.ZipFile(path) as z: z.extractall(target)
        flash(f"World uploaded: {world_name}")
    return redirect(url_for("worlds"))

@app.route("/world/download/<name>")
@login_required
def download_world(name):
    world_path=SERVER_DIR/name
    if not world_path.exists():
        return "Not found",404
    tmp=BASE_DIR/f"temp_{name}.zip"
    with zipfile.ZipFile(tmp,"w",zipfile.ZIP_DEFLATED) as z:
        for p in world_path.rglob("*"):
            if p.is_file():
                z.write(p, p.relative_to(world_path))
    return send_file(tmp, as_attachment=True, download_name=f"{name}.zip")

@app.route("/backup/download/<name>")
@login_required
def download_backup(name):
    path=BACKUP_DIR/name
    if not path.exists(): return "Not found",404
    return send_file(path, as_attachment=True)

# Players
@app.route("/players", methods=["GET","POST"])
@login_required
def players():
    if request.method=="POST":
        name=request.form.get("player","").strip()
        action=request.form.get("action")
        if name and action:
            manager.player_action(name, action)
            flash(f"{action} -> {name}")
    q=request.args.get("q","").lower()
    flt=request.args.get("filter","all")
    allp=manager.get_players()
    if q: allp=[p for p in allp if q in p["name"].lower()]
    if flt=="op": allp=[p for p in allp if p.get("op")]
    if flt=="banned": allp=[p for p in allp if p.get("banned")]
    if flt=="whitelisted": allp=[p for p in allp if p.get("whitelisted")]
    return render_template("players.html", players=allp, q=q, filter=flt)

# Settings
@app.route("/settings", methods=["GET","POST"])
@login_required
def settings():
    if request.method=="POST":
        data={k:v for k,v in request.form.items() if k not in ["version","server_type"]}
        # props
        props={k:v for k,v in data.items()}
        manager.save_properties(props)
        if "version" in request.form:
            manager.version=request.form["version"]
            manager.server_type=request.form.get("server_type","java")
            # try download new version
            manager.download_java_server()
        flash("Settings saved. Restart to apply version/world changes.")
        return redirect(url_for("settings"))
    props=manager.get_properties()
    return render_template("settings.html", props=props, version=manager.version, server_type=manager.server_type)

# Files
@app.route("/files")
@login_required
def files():
    rel=request.args.get("path","")
    items, cur = manager.list_files(rel)
    content=""
    edit_path=request.args.get("edit","")
    if edit_path:
        fp=SERVER_DIR/edit_path
        if fp.exists() and fp.is_file() and fp.stat().st_size<500000:
            try: content=fp.read_text(errors="ignore")
            except: content=""
    return render_template("files.html", items=items, cur=cur, edit_path=edit_path, content=content)

@app.route("/files/save", methods=["POST"])
@login_required
def files_save():
    rel=request.form.get("path","")
    content=request.form.get("content","")
    fp=(SERVER_DIR/rel).resolve()
    if str(fp).startswith(str(SERVER_DIR.resolve())):
        fp.write_text(content)
        flash(f"Saved {rel}")
    return redirect(url_for("files", edit=rel))

@app.route("/files/upload", methods=["POST"])
@login_required
def files_upload():
    f=request.files.get("file")
    rel=request.form.get("path","")
    if f:
        (SERVER_DIR/rel/f.filename).write_bytes(f.read())
        flash(f"Uploaded {f.filename}")
    return redirect(url_for("files", path=rel))

@app.route("/files/delete", methods=["POST"])
@login_required
def files_delete():
    rel=request.form.get("path","")
    target=SERVER_DIR/rel
    if target.exists():
        if target.is_dir(): shutil.rmtree(target)
        else: target.unlink()
        flash(f"Deleted {rel}")
    return redirect(url_for("files"))

# Backups page separate also reuses worlds
@app.route("/backups")
@login_required
def backups_page():
    return render_template("backups.html", backups=manager.list_backups())

@app.route("/api/host")
@login_required
def api_host():
    return jsonify({"host": request.host})

if __name__=="__main__":
    print(f"Panel password: {ADMIN_PASSWORD}")
    print("Open http://0.0.0.0:5000 - Java 25565, Bedrock 19132 UDP")
    app.run(host="0.0.0.0", port=5000, debug=False, threaded=True)
