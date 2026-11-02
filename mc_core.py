
import os, json, zipfile, shutil, subprocess, threading, time, re, requests, datetime, pathlib
try:
    # psutil disabled for Termux
    HAS_PSUTIL=False
except:
    psutil=None
    HAS_PSUTIL=False

BASE_DIR = pathlib.Path(__file__).parent
SERVER_DIR = BASE_DIR / "minecraft_server"
BACKUP_DIR = BASE_DIR / "backups"
UPLOAD_DIR = BASE_DIR / "uploads"
for d in [SERVER_DIR, BACKUP_DIR, UPLOAD_DIR]:
    d.mkdir(exist_ok=True)

class ServerManager:
    def __init__(self):
        self.process = None
        self.log_lines = []
        self.server_type = os.getenv("MC_SERVER_TYPE","java")
        self.version = os.getenv("MC_VERSION","1.21.1")
        self.lock = threading.Lock()

    def status(self):
        running = self.process is not None and self.process.poll() is None
        # Termux safe - no psutil, no /proc/stat
        try:
            with open("/proc/meminfo") as f:
                lines = f.readlines()
                mem_total = int([l for l in lines if "MemTotal" in l][0].split()[1]) // 1024
                mem_free = int([l for l in lines if "MemAvailable" in l][0].split()[1]) // 1024
                mem_used = mem_total - mem_free
        except:
            mem_total, mem_used = 4096, 512
        import shutil as _sh
        try:
            du = _sh.disk_usage(str(BASE_DIR))
            disk_used = round(du.used/1024/1024/1024,1)
            disk_total = round(du.total/1024/1024/1024,1)
            disk_pct = round(du.used/du.total*100,1)
        except:
            disk_used, disk_total, disk_pct = 0, 10, 0
        proc_mem = 0
        proc_cpu = 0
        players = []
        for line in self.log_lines[-200:]:
            import re as _re
            m = _re.search(r"(\w+) joined the game", line)
            if m and m.group(1) not in players:
                players.append(m.group(1))
        return {
            "running": running,
            "cpu": 0,
            "ram_used": mem_used,
            "ram_total": mem_total,
            "ram_percent": round(mem_used/mem_total*100,1) if mem_total else 0,
            "disk_used": disk_used,
            "disk_total": disk_total,
            "disk_percent": disk_pct,
            "proc_mem_mb": round(proc_mem,1),
            "proc_cpu": round(proc_cpu,1),
            "players_online": players,
            "player_count": len(players),
            "type": self.server_type,
            "version": self.version,
            "uptime": self._uptime()
        }

    def _uptime(self):
        if self.process and self.process.poll() is None:
            return "Running"
        return "Stopped"

    def _log_reader(self):
        if not self.process or not self.process.stdout:
            return
        for line in iter(self.process.stdout.readline, ''):
            if line:
                self.log_lines.append(line.rstrip())
                if len(self.log_lines)>2000:
                    self.log_lines=self.log_lines[-2000:]
            else:
                break

    def start(self):
        if self.process and self.process.poll() is None:
            return False, "Already running"
        # Ensure eula
        (SERVER_DIR/"eula.txt").write_text("eula=true\n")
        # Ensure server.properties exists
        if not (SERVER_DIR/"server.properties").exists():
            self.create_default_properties()
        # ensure jar / bedrock binary
        if self.server_type=="java":
            jar = SERVER_DIR/"server.jar"
            if not jar.exists():
                # Create placeholder and try download paper
                ok, msg = self.download_java_server()
                if not ok:
                    return False, msg
            cmd = ["java", f"-Xms{os.getenv('MC_MEMORY_MIN','512M')}", f"-Xmx{os.getenv('MC_MEMORY_MAX','1024M')}", "-jar", "server.jar", "nogui"]
        else:
            exe = SERVER_DIR/"bedrock_server"
            if not exe.exists():
                ok, msg = self.download_bedrock_server()
                if not ok:
                    return False, msg
            cmd = ["./bedrock_server"]
        try:
            self.process = subprocess.Popen(cmd, cwd=str(SERVER_DIR), stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)
            threading.Thread(target=self._log_reader, daemon=True).start()
            self.log_lines.append(f"[{datetime.datetime.now()}] Starting {self.server_type} server v{self.version} ...")
            return True, "Started"
        except Exception as e:
            return False, str(e)

    def stop(self):
        if self.process and self.process.poll() is None:
            try:
                self.send_command("stop")
                time.sleep(2)
                self.process.terminate()
                self.process.wait(timeout=5)
            except: 
                try: self.process.kill()
                except: pass
            self.log_lines.append(f"[{datetime.datetime.now()}] Server stopped")
            return True, "Stopped"
        return False, "Not running"

    def restart(self):
        self.stop()
        time.sleep(1)
        return self.start()

    def send_command(self, cmd):
        if self.process and self.process.poll() is None and self.process.stdin:
            try:
                self.process.stdin.write(cmd+"\n")
                self.process.stdin.flush()
                self.log_lines.append(f"> {cmd}")
                return True
            except Exception as e:
                self.log_lines.append(f"Command error: {e}")
                return False
        return False

    def create_default_properties(self):
        props = f"""#Minecraft server properties
server-port={os.getenv('MC_PORT','25565')}
max-players={os.getenv('MC_MAX_PLAYERS','20')}
motd={os.getenv('MC_MOTD','A Minecraft Server')}
online-mode=true
gamemode=survival
difficulty=normal
level-seed=
level-name=world
enable-command-block=true
white-list=false
spawn-protection=16
view-distance=10
allow-nether=true
pvp=true
"""
        (SERVER_DIR/"server.properties").write_text(props)

    def download_java_server(self):
        try:
            # Try Paper API
            v = self.version
            r = requests.get(f"https://api.papermc.io/v2/projects/paper/versions/{v}/builds", timeout=15)
            if r.status_code==200:
                builds = r.json().get("builds",[])
                if builds:
                    latest = builds[-1]["build"]
                    url = f"https://api.papermc.io/v2/projects/paper/versions/{v}/builds/{latest}/downloads/paper-{v}-{latest}.jar"
                    resp = requests.get(url, timeout=60)
                    (SERVER_DIR/"server.jar").write_bytes(resp.content)
                    self.log_lines.append(f"Downloaded Paper {v} build {latest}")
                    return True, "Downloaded Paper"
            # Fallback vanilla
            self.log_lines.append("Paper download failed, creating placeholder - place server.jar manually")
            (SERVER_DIR/"server.jar").write_text("placeholder")
            return False, "Could not download Paper automatically. Place server.jar manually in minecraft_server/"
        except Exception as e:
            return False, str(e)

    def download_bedrock_server(self):
        try:
            # Known BDS linux URL pattern - user can replace zip manually
            url = "https://www.minecraft.net/en-us/download/server/bedrock"
            self.log_lines.append("Bedrock download requires manual zip - place bedrock_server binary in minecraft_server/ . See README for link.")
            return False, "Place bedrock_server binary manually (download from minecraft.net bedrock server)"
        except Exception as e:
            return False, str(e)

    def get_properties(self):
        p = SERVER_DIR/"server.properties"
        if not p.exists(): self.create_default_properties()
        d={}
        for line in p.read_text().splitlines():
            if "=" in line and not line.startswith("#"):
                k,v=line.split("=",1)
                d[k.strip()]=v.strip()
        return d

    def save_properties(self, data:dict):
        lines=[]
        p = SERVER_DIR/"server.properties"
        existing = p.read_text() if p.exists() else ""
        # Keep comments, replace values
        for line in existing.splitlines():
            if "=" in line and not line.startswith("#"):
                k=line.split("=",1)[0].strip()
                if k in data:
                    lines.append(f"{k}={data[k]}")
                    continue
            lines.append(line)
        # Add new keys
        keys=set([l.split("=",1)[0].strip() for l in lines if "=" in l and not l.startswith("#")])
        for k,v in data.items():
            if k not in keys:
                lines.append(f"{k}={v}")
        p.write_text("\n".join(lines)+"\n")
        # also update version/memory env logic
        if "version" in data:
            self.version=data["version"]

    # Worlds
    def list_worlds(self):
        worlds=[]
        for item in SERVER_DIR.iterdir():
            if item.is_dir() and (item/"level.dat").exists() or (item.is_dir() and item.name in ["world","world_nether","world_the_end"]):
                size = sum(f.stat().st_size for f in item.rglob("*") if f.is_file())/1024/1024
                worlds.append({"name":item.name, "size_mb":round(size,1), "files": len(list(item.rglob("*")) )})
            elif item.is_dir() and item.name.startswith("world"):
                worlds.append({"name":item.name, "size_mb":0, "files":0})
        # Also scan uploads
        return worlds

    def create_world(self, name, seed="", gamemode="survival", difficulty="normal"):
        props=self.get_properties()
        props["level-name"]=name
        props["level-seed"]=seed
        props["gamemode"]=gamemode
        props["difficulty"]=difficulty
        self.save_properties(props)
        # Remove old if exists? Keep backup
        return True

    # Backups
    def create_backup(self, label="manual"):
        ts=datetime.datetime.now()
        count=len(list(BACKUP_DIR.glob("*.zip")))+1
        fname=f"backup_{ts.strftime('%Y-%m-%d_%H-%M')}_#{count}_{label}.zip"
        path=BACKUP_DIR/fname
        with zipfile.ZipFile(path,"w",zipfile.ZIP_DEFLATED) as z:
            for root,dirs,files in os.walk(SERVER_DIR):
                # skip jar cache
                for f in files:
                    fp=pathlib.Path(root)/f
                    if fp.suffix in [".jar"]: continue
                    z.write(fp, fp.relative_to(SERVER_DIR))
        # Try S3 / GDrive upload if configured
        self._upload_cloud(path)
        return fname

    def _upload_cloud(self, path):
        bucket=os.getenv("AWS_BUCKET_NAME","")
        if bucket:
            try:
                import boto3
                s3=boto3.client("s3", aws_access_key_id=os.getenv("AWS_ACCESS_KEY_ID"), aws_secret_access_key=os.getenv("AWS_SECRET_ACCESS_KEY"), region_name=os.getenv("AWS_REGION","ap-south-1"))
                s3.upload_file(str(path), bucket, f"minecraft-backups/{path.name}")
                self.log_lines.append(f"Uploaded backup to S3: {path.name}")
            except Exception as e:
                self.log_lines.append(f"S3 upload failed: {e}")
        if os.getenv("GDRIVE_ENABLED","false").lower()=="true":
            self.log_lines.append(f"Google Drive backup queued: {path.name} - configure rclone or gdrive folder {os.getenv('GDRIVE_FOLDER_ID')}")

    def list_backups(self):
        out=[]
        for f in sorted(BACKUP_DIR.glob("*.zip"), key=lambda x: x.stat().st_mtime, reverse=True):
            out.append({"name":f.name, "size_mb":round(f.stat().st_size/1024/1024,2), "date": datetime.datetime.fromtimestamp(f.stat().st_mtime).strftime("%Y-%m-%d %H:%M"), "path":str(f)})
        return out

    # Players
    def get_players(self):
        def load_json(name):
            p=SERVER_DIR/name
            if p.exists():
                try: return json.loads(p.read_text())
                except: return []
            return []
        whitelist=load_json("whitelist.json")
        ops=load_json("ops.json")
        banned=load_json("banned-players.json")
        # Merge
        players={}
        for w in whitelist:
            players[w.get("name","")]={"name":w.get("name"), "uuid":w.get("uuid"), "whitelisted":True, "op":False, "banned":False}
        for o in ops:
            n=o.get("name")
            if n not in players: players[n]={"name":n, "uuid":o.get("uuid"), "whitelisted":False, "op":True, "banned":False}
            else: players[n]["op"]=True
        for b in banned:
            n=b.get("name")
            if n not in players: players[n]={"name":n, "uuid":b.get("uuid"), "whitelisted":False, "op":False, "banned":True}
            else: players[n]["banned"]=True
        return list(players.values())

    def player_action(self, name, action):
        # action: whitelist_add, whitelist_remove, op, deop, ban, pardon, kick
        cmds={
            "whitelist_add": f"whitelist add {name}",
            "whitelist_remove": f"whitelist remove {name}",
            "op": f"op {name}",
            "deop": f"deop {name}",
            "ban": f"ban {name}",
            "pardon": f"pardon {name}",
            "kick": f"kick {name}",
        }
        if action in cmds:
            if self.process and self.process.poll() is None:
                self.send_command(cmds[action])
            # Also update json files manually for offline
            if action=="whitelist_add":
                p=SERVER_DIR/"whitelist.json"
                data=json.loads(p.read_text()) if p.exists() else []
                if not any(x.get("name")==name for x in data):
                    data.append({"uuid":"00000000-0000-0000-0000-000000000000","name":name})
                    p.write_text(json.dumps(data, indent=2))
            if action=="ban":
                p=SERVER_DIR/"banned-players.json"
                data=json.loads(p.read_text()) if p.exists() else []
                if not any(x.get("name")==name for x in data):
                    data.append({"uuid":"00000000-0000-0000-0000-000000000000","name":name,"created":"2026-01-01","reason":"Banned by panel"})
                    p.write_text(json.dumps(data, indent=2))
            return True
        return False

    def list_files(self, rel=""):
        target = (SERVER_DIR/rel).resolve()
        if not str(target).startswith(str(SERVER_DIR.resolve())):
            target=SERVER_DIR
        items=[]
        if target.is_dir():
            for f in sorted(target.iterdir(), key=lambda x: (not x.is_dir(), x.name.lower())):
                items.append({"name":f.name, "is_dir":f.is_dir(), "size": f.stat().st_size if f.is_file() else 0, "path": str(f.relative_to(SERVER_DIR))})
        return items, str(target.relative_to(SERVER_DIR)) if target!=SERVER_DIR else ""

manager = ServerManager()

def auto_backup_loop():
    while True:
        try:
            now=datetime.datetime.now()
            hour=int(os.getenv("BACKUP_HOUR","3"))
            if now.hour==hour and now.minute==0:
                manager.create_backup("daily")
                time.sleep(60)
            time.sleep(30)
        except:
            time.sleep(60)

threading.Thread(target=auto_backup_loop, daemon=True).start()