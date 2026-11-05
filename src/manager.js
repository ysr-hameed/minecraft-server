const EventEmitter = require('events');
const fs = require('fs');
const fsp = require('fs/promises');
const path = require('path');
const { spawn } = require('child_process');
const { Readable } = require('stream');
const { pipeline } = require('stream/promises');

const config = require('./config');
const stats = require('./stats');
const s3 = require('./s3');
const {
  run,
  formatBytes,
  formatDuration,
  timestamp,
  safeName,
  resolveInside,
  relativeTo,
  pathExists,
  dirSize,
  zipDirectory,
  unzipTo,
  tailLines,
  parseProperties,
  updateProperties,
} = require('./util');

const BINARY = 'bedrock_server';
const PRESERVE_ON_UPDATE = new Set([
  'worlds',
  'server.properties',
  'allowlist.json',
  'permissions.json',
  'banned-players.json',
  'banned-ips.json',
  'ops.json',
  'whitelist.json',
]);

const DEFAULT_PROPERTIES = () => ({
  'server-name': config.serverName,
  gamemode: config.gamemode,
  'force-gamemode': 'false',
  difficulty: config.difficulty,
  'allow-cheats': 'false',
  'max-players': String(config.maxPlayers),
  'online-mode': 'true',
  'allow-list': 'false',
  'server-port': String(config.serverPort),
  'server-portv6': String(config.serverPortV6),
  transport: config.transport,
  'server-udp-ports': config.udpPorts || String(config.serverPort),
  'enable-lan-visibility': 'true',
  'view-distance': String(config.viewDistance),
  'tick-distance': String(config.tickDistance),
  'player-idle-timeout': '30',
  'max-threads': String(config.maxThreads),
  'level-name': config.levelName,
  'level-seed': '',
  'default-player-permission-level': 'member',
  'texturepack-required': 'false',
  'content-log-file-enabled': 'false',
  'content-log-console-output-enabled': 'false',
  'compression-threshold': '1',
  'compression-algorithm': 'zlib',
});

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

class BedrockManager extends EventEmitter {
  constructor() {
    super();
    this.child = null;
    this.running = false;
    this.startedAt = null;
    this.stopping = false;
    this.logs = [];
    this.logStream = null;
    this.online = new Map();
    this.xuidMap = new Map();
    this.latestCache = null;
    this.installPromise = null;
    this.unavailable = null;
    this.liveVersion = null;
    this.protocol = null;
    this._readyResolve = null;
    this._exitResolve = null;
    this._dailyTimer = null;
    this._updateTimer = null;
    this._loadState();
    this._loadXuids();
  }

  _statePath() {
    return path.join(config.runtimeDir, 'state.json');
  }

  _versionPath() {
    return path.join(config.runtimeDir, 'bds-version.json');
  }

  _xuidPath() {
    return path.join(config.runtimeDir, 'players.json');
  }

  _loadState() {
    try {
      this.state = JSON.parse(fs.readFileSync(this._statePath(), 'utf8'));
    } catch {
      this.state = {};
    }
  }

  _saveState() {
    try {
      fs.writeFileSync(this._statePath(), JSON.stringify(this.state, null, 2));
    } catch {
      /* ignore */
    }
  }

  _loadXuids() {
    try {
      const data = JSON.parse(fs.readFileSync(this._xuidPath(), 'utf8'));
      for (const [name, xuid] of Object.entries(data)) this.xuidMap.set(name, String(xuid));
    } catch {
      /* ignore */
    }
  }

  _saveXuids() {
    try {
      fs.writeFileSync(this._xuidPath(), JSON.stringify(Object.fromEntries(this.xuidMap), null, 2));
    } catch {
      /* ignore */
    }
  }

  _appendLog(line) {
    const entry = String(line);
    this.logs.push(entry);
    if (this.logs.length > config.consoleLines) this.logs.splice(0, this.logs.length - config.consoleLines);
    if (this.logStream) {
      try {
        this.logStream.write(`${entry}\n`);
      } catch {
        /* ignore */
      }
    }
    this.emit('line', entry);
  }

  _openLogStream() {
    try {
      const file = path.join(config.logDir, 'console.log');
      this.logStream = fs.createWriteStream(file, { flags: 'a' });
    } catch {
      this.logStream = null;
    }
  }

  installedVersion() {
    try {
      const data = JSON.parse(fs.readFileSync(this._versionPath(), 'utf8'));
      return data.version || '';
    } catch {
      return '';
    }
  }

  binaryPath() {
    return path.join(config.serverDir, BINARY);
  }

  hasBinary() {
    return fs.existsSync(this.binaryPath());
  }

  async ensureDirs() {
    for (const dir of [config.serverDir, config.backupDir, config.uploadDir, config.runtimeDir, config.logDir]) {
      await fsp.mkdir(dir, { recursive: true });
    }
  }

  async latestRelease(force = false) {
    const now = Date.now();
    if (!force && this.latestCache && now - this.latestCache.fetchedAt < config.versionCacheTtl) {
      return this.latestCache;
    }
    const response = await fetch(config.officialLinksApi, {
      headers: { 'User-Agent': 'SlyxPanel/2.0 (+https://github.com/ysr-hameed/minecraft-server)' },
    });
    if (!response.ok) throw new Error(`Mojang download API returned HTTP ${response.status}`);
    const data = await response.json();
    const links = data?.result?.links || [];
    const pick = (type) => {
      const item = links.find((link) => link.downloadType === type);
      if (!item) return null;
      const match = String(item.downloadUrl).match(/bedrock-server-([\d.]+)\.zip$/);
      return { version: match ? match[1] : 'unknown', url: item.downloadUrl };
    };
    const release = {
      latest: pick('serverBedrockLinux'),
      preview: pick('serverBedrockPreviewLinux'),
      fetchedAt: now,
    };
    this.latestCache = release;
    try {
      fs.writeFileSync(path.join(config.runtimeDir, 'versions.json'), JSON.stringify(release, null, 2));
    } catch {
      /* ignore */
    }
    return release;
  }

  async downloadFile(url, destination) {
    const response = await fetch(url, {
      redirect: 'follow',
      headers: { 'User-Agent': 'Mozilla/5.0 SlyxPanel/2.0' },
    });
    if (!response.ok || !response.body) throw new Error(`Download failed: HTTP ${response.status}`);
    await pipeline(Readable.fromWeb(response.body), fs.createWriteStream(destination));
    const stat = await fsp.stat(destination);
    if (stat.size < 10 * 1024 * 1024) throw new Error('Downloaded archive is unexpectedly small');
    return stat.size;
  }

  async _findExtractedRoot(dir) {
    if (await pathExists(path.join(dir, BINARY))) return dir;
    const entries = await fsp.readdir(dir, { withFileTypes: true });
    for (const entry of entries) {
      if (!entry.isDirectory()) continue;
      const candidate = path.join(dir, entry.name);
      if (await pathExists(path.join(candidate, BINARY))) return candidate;
    }
    throw new Error('The archive did not contain a bedrock_server binary');
  }

  async install(release, { force = false } = {}) {
    if (!release?.url || !release?.version) throw new Error('No release information available');
    if (!force && this.installedVersion() === release.version && this.hasBinary()) {
      return { ok: true, version: release.version, message: `Bedrock ${release.version} is already installed` };
    }
    if (this.installPromise) return this.installPromise;

    this.installPromise = (async () => {
      const wasRunning = this.running;
      if (wasRunning) await this.stop({ reason: 'update' });
      const zipPath = path.join(config.uploadDir, `bedrock-server-${release.version}-${Date.now()}.zip`);
      const staging = path.join(config.uploadDir, `bds-staging-${Date.now()}`);
      try {
        this._appendLog(`[panel] Downloading Bedrock Dedicated Server ${release.version}...`);
        await this.downloadFile(release.url, zipPath);
        this._appendLog(`[panel] Download complete (${formatBytes((await fsp.stat(zipPath)).size)}), extracting...`);
        await unzipTo(zipPath, staging);
        const root = await this._findExtractedRoot(staging);
        await fsp.mkdir(config.serverDir, { recursive: true });
        await fsp.rm(path.join(config.uploadDir, '.staging-copy'), { recursive: true, force: true });
        const entries = await fsp.readdir(root, { withFileTypes: true });
        for (const entry of entries) {
          if (PRESERVE_ON_UPDATE.has(entry.name)) continue;
          const source = path.join(root, entry.name);
          const target = path.join(config.serverDir, entry.name);
          await fsp.cp(source, target, { recursive: true, force: true, errorOnExist: false });
        }
        await fsp.chmod(this.binaryPath(), 0o755);
        await this._ensureServerFiles();
        fs.writeFileSync(
          this._versionPath(),
          JSON.stringify({ version: release.version, url: release.url, installedAt: new Date().toISOString() }, null, 2)
        );
        this._appendLog(`[panel] Bedrock Dedicated Server ${release.version} installed`);
        this.emit('installed', release.version);
        if (wasRunning) await this.start();
        return { ok: true, version: release.version, message: `Installed Bedrock ${release.version}` };
      } finally {
        await fsp.rm(zipPath, { force: true }).catch(() => {});
        await fsp.rm(staging, { recursive: true, force: true }).catch(() => {});
        this.installPromise = null;
      }
    })();
    return this.installPromise;
  }

  async ensureBinary() {
    if (this.hasBinary() && this.installedVersion()) return { ok: true, version: this.installedVersion() };
    const releases = await this.latestRelease(true);
    if (!releases.latest) throw new Error('Could not find the official Bedrock Linux download');
    await this._ensureServerFiles();
    return this.install(releases.latest, { force: true });
  }

  async _ensureServerFiles() {
    await fsp.mkdir(path.join(config.serverDir, 'worlds'), { recursive: true });
    const propsPath = path.join(config.serverDir, 'server.properties');
    if (!(await pathExists(propsPath))) {
      const defaults = DEFAULT_PROPERTIES();
      const content = Object.entries(defaults)
        .map(([key, value]) => `${key}=${value}`)
        .join('\n');
      await fsp.writeFile(propsPath, `${content}\n`);
    } else {
      const original = await fsp.readFile(propsPath, 'utf8');
      const current = parseProperties(original);
      const updates = {};
      if (!current.transport) updates.transport = config.transport;
      if (!current['server-port']) updates['server-port'] = String(config.serverPort);
      if (!current['server-portv6']) updates['server-portv6'] = String(config.serverPortV6);
      if (config.transport === 'nethernet') {
        const desiredUdp = config.udpPorts || String(config.serverPort);
        if (!current['server-udp-ports']) updates['server-udp-ports'] = desiredUdp;
        else if (config.udpPorts && current['server-udp-ports'] !== config.udpPorts) {
          updates['server-udp-ports'] = config.udpPorts;
        }
      }
      if (Object.keys(updates).length) await fsp.writeFile(propsPath, updateProperties(original, updates));
    }
    const allowlist = path.join(config.serverDir, 'allowlist.json');
    if (!(await pathExists(allowlist))) await fsp.writeFile(allowlist, '[]\n');
    const permissions = path.join(config.serverDir, 'permissions.json');
    if (!(await pathExists(permissions))) await fsp.writeFile(permissions, '[]\n');
  }

  async start() {
    if (this.running || this.stopping) return { ok: true, message: 'Server is already running' };
    try {
      await this.ensureBinary();
    } catch (error) {
      this.unavailable = error.message;
      return { ok: false, message: `Cannot start: ${error.message}` };
    }
    await this._ensureServerFiles();
    this.unavailable = null;
    this._openLogStream();
    this._appendLog(`[panel] Starting Bedrock Dedicated Server (${this.installedVersion()})...`);

    const child = spawn(this.binaryPath(), [], {
      cwd: config.serverDir,
      env: { ...process.env, LD_LIBRARY_PATH: config.serverDir },
      stdio: ['pipe', 'pipe', 'pipe'],
    });
    this.child = child;
    this.running = true;
    this.stopping = false;
    this.startedAt = new Date();
    this.liveVersion = null;
    this.protocol = null;
    this.online.clear();

    let buffer = '';
    const handleChunk = (chunk) => {
      buffer += chunk.toString('utf8');
      let index;
      while ((index = buffer.indexOf('\n')) !== -1) {
        const line = buffer.slice(0, index).replace(/\r$/, '');
        buffer = buffer.slice(index + 1);
        this._handleLine(line);
      }
    };
    child.stdout.on('data', handleChunk);
    child.stderr.on('data', handleChunk);

    const ready = new Promise((resolve) => {
      this._readyResolve = resolve;
    });
    const exited = new Promise((resolve) => {
      this._exitResolve = resolve;
    });
    this._exited = exited;

    child.on('error', (error) => {
      this._appendLog(`[panel] Failed to launch server: ${error.message}`);
      this.running = false;
      if (this._readyResolve) this._readyResolve(false);
      if (this._exitResolve) this._exitResolve();
    });

    child.on('exit', (code, signal) => {
      this._appendLog(`[panel] Server process exited (code ${code ?? 'null'}, signal ${signal ?? 'none'})`);
      this.running = false;
      this.stopping = false;
      this.child = null;
      this.startedAt = null;
      this.online.clear();
      if (this._readyResolve) this._readyResolve(false);
      if (this._exitResolve) this._exitResolve();
      this.emit('exit', { code, signal });
    });

    const started = await Promise.race([ready, sleep(config.startTimeout * 1000).then(() => 'timeout')]);
    if (started === 'timeout' && this.running) {
      this._appendLog('[panel] Server did not print the ready line in time, but the process is running.');
      return { ok: true, message: 'Server is starting (slow first startup)' };
    }
    if (!this.running) return { ok: false, message: 'Server exited during startup. Check the console.' };
    return { ok: true, message: 'Server started' };
  }

  async stop({ reason = 'manual' } = {}) {
    if (!this.running || !this.child) return { ok: true, message: 'Server is already stopped' };
    this.stopping = true;
    this._appendLog(`[panel] Stopping server (${reason})...`);
    try {
      this.write('stop');
    } catch {
      /* ignore */
    }
    const exited = this._exited || Promise.resolve();
    const finished = await Promise.race([exited.then(() => true), sleep(config.stopTimeout * 1000).then(() => false)]);
    if (!finished && this.child) {
      this._appendLog('[panel] Graceful stop timed out, sending SIGTERM');
      try {
        this.child.kill('SIGTERM');
      } catch {
        /* ignore */
      }
      const term = await Promise.race([exited.then(() => true), sleep(8000).then(() => false)]);
      if (!term && this.child) {
        this._appendLog('[panel] SIGTERM timed out, sending SIGKILL');
        try {
          this.child.kill('SIGKILL');
        } catch {
          /* ignore */
        }
      }
    }
    this.stopping = false;
    return { ok: true, message: 'Server stopped' };
  }

  async restart() {
    await this.stop({ reason: 'restart' });
    await sleep(1000);
    return this.start();
  }

  write(command) {
    if (!this.running || !this.child || !this.child.stdin.writable) {
      throw new Error('Server is not running');
    }
    const clean = String(command).replace(/[\r\n]+/g, ' ').trim();
    if (!clean) throw new Error('Command is empty');
    this.child.stdin.write(`${clean}\n`);
    this._appendLog(`[console] ${clean}`);
  }

  sendCommand(command) {
    try {
      this.write(command);
      return { ok: true, message: `Sent: ${command}` };
    } catch (error) {
      return { ok: false, message: error.message };
    }
  }

  _handleLine(line) {
    this._appendLog(line);
    if (!line) return;
    const versionMatch = line.match(/Version:\s*([\d.]+)/i);
    if (versionMatch) this.liveVersion = versionMatch[1];
    const protocolMatch = line.match(/protocol(?: version)?[:\s]+(\d{3,5})/i);
    if (protocolMatch) this.protocol = Number(protocolMatch[1]);

    if (/Server started\./i.test(line)) {
      if (this._readyResolve) this._readyResolve(true);
      return;
    }

    const connected = line.match(/Player connected:\s*(.+?),\s*xuid:\s*(\d+)/i);
    if (connected) {
      const name = connected[1].trim();
      const xuid = connected[2];
      this.online.set(name, { name, xuid, since: Date.now() });
      this.xuidMap.set(name, xuid);
      this._saveXuids();
      return;
    }
    const spawned = line.match(/Player Spawned:\s*(.+?)\s+xuid:\s*(\d+)/i);
    if (spawned) {
      const name = spawned[1].trim();
      const xuid = spawned[2];
      if (!this.online.has(name)) this.online.set(name, { name, xuid, since: Date.now() });
      this.xuidMap.set(name, xuid);
      this._saveXuids();
      return;
    }
    const disconnected = line.match(/Player disconnected:\s*(.+?),\s*xuid:\s*(\d+)/i);
    if (disconnected) {
      this.online.delete(disconnected[1].trim());
    }
  }

  logsSnapshot(limit = config.logLines) {
    return this.logs.slice(-limit);
  }

  async status() {
    const stat = await stats.snapshot(config.serverDir, this.child?.pid);
    const props = await this.getProperties();
    return {
      running: this.running,
      pid: this.child?.pid || null,
      uptime: this.startedAt ? formatDuration(Date.now() - this.startedAt.getTime()) : '—',
      version: this.liveVersion || this.installedVersion() || 'unknown',
      installed_version: this.installedVersion() || 'unknown',
      bedrock: this.liveVersion || this.installedVersion() || 'unknown',
      protocol: this.protocol,
      port: Number(props['server-port'] || config.serverPort),
      player_count: this.online.size,
      players_online: [...this.online.keys()],
      ...stat,
    };
  }

  async runtimeStatus() {
    const installed = this.installedVersion();
    let latest = null;
    let preview = null;
    let error = null;
    try {
      const releases = await this.latestRelease();
      latest = releases.latest;
      preview = releases.preview;
    } catch (err) {
      error = err.message;
    }
    let binarySize = 0;
    try {
      binarySize = (await fsp.stat(this.binaryPath())).size;
    } catch {
      /* ignore */
    }
    return {
      server_dir: config.serverDir,
      backup_dir: config.backupDir,
      installed_version: installed || 'not installed',
      installed,
      latest_version: latest?.version || 'unknown',
      latest,
      preview,
      update_available: Boolean(latest && installed && latest.version !== installed),
      binary: this.hasBinary(),
      binary_size: formatBytes(binarySize),
      platform: `${process.platform} ${process.arch}`,
      transport: config.transport,
      s3_enabled: s3.enabled(),
      s3_bucket: config.aws.bucket,
      error,
    };
  }

  async getProperties() {
    const file = path.join(config.serverDir, 'server.properties');
    try {
      return parseProperties(await fsp.readFile(file, 'utf8'));
    } catch {
      return DEFAULT_PROPERTIES();
    }
  }

  async saveProperties(updates) {
    const file = path.join(config.serverDir, 'server.properties');
    let content = '';
    try {
      content = await fsp.readFile(file, 'utf8');
    } catch {
      content = Object.entries(DEFAULT_PROPERTIES())
        .map(([key, value]) => `${key}=${value}`)
        .join('\n');
    }
    const clean = {};
    for (const [key, value] of Object.entries(updates)) {
      if (!/^[A-Za-z0-9._-]+$/.test(key)) continue;
      clean[key] = String(value).replace(/[\r\n]+/g, ' ').slice(0, 512);
    }
    await fsp.writeFile(file, updateProperties(content, clean));
    return { ok: true, message: 'Settings saved. Restart the server to apply them.' };
  }

  async versions() {
    const installed = this.installedVersion();
    let releases = { latest: null, preview: null, fetchedAt: 0 };
    try {
      releases = await this.latestRelease();
    } catch {
      /* ignore */
    }
    const list = [];
    if (releases.latest) {
      list.push({ version: releases.latest.version, label: 'Official stable', url: releases.latest.url, selected: installed === releases.latest.version });
    }
    if (releases.preview) {
      list.push({ version: releases.preview.version, label: 'Official preview', url: releases.preview.url, selected: installed === releases.preview.version });
    }
    if (installed && !list.some((item) => item.version === installed)) {
      list.unshift({ version: installed, label: 'Installed', url: null, selected: true });
    }
    return list;
  }

  autoUpdateEnabled() {
    if (typeof this.state.autoUpdate === 'boolean') return this.state.autoUpdate;
    return config.autoUpdate;
  }

  setAutoUpdate(enabled) {
    this.state.autoUpdate = Boolean(enabled);
    this._saveState();
    return { ok: true, message: `Automatic updates ${enabled ? 'enabled' : 'disabled'}` };
  }

  async autoUpdateIfNeeded() {
    if (!this.autoUpdateEnabled()) return;
    try {
      const releases = await this.latestRelease(true);
      const installed = this.installedVersion();
      if (!this.hasBinary() || (releases.latest && installed && installed !== releases.latest.version)) {
        if (this.running) {
          this._appendLog('[panel] A newer Bedrock release is available. Update from Settings when the server is idle.');
          return;
        }
        this._appendLog(`[panel] Updating Bedrock from ${installed || 'nothing'} to ${releases.latest.version}...`);
        await this.install(releases.latest, { force: true });
      }
    } catch (error) {
      this._appendLog(`[panel] Automatic update check failed: ${error.message}`);
    }
  }

  async createBackup(type = 'manual') {
    await this.ensureDirs();
    if (!(await pathExists(config.serverDir))) return { ok: false, message: 'Server directory is missing' };
    const existing = await fsp.readdir(config.backupDir).catch(() => []);
    const index = existing.filter((name) => name.endsWith('.zip')).length + 1;
    const name = `backup_${timestamp()}_#${index}_${safeName(type, 'manual')}.zip`;
    const finalPath = path.join(config.backupDir, name);
    const tempPath = path.join(config.uploadDir, `.backup-${Date.now()}.zip`);

    let held = false;
    if (this.running) {
      try {
        this.write('save hold');
        held = true;
        await sleep(1200);
      } catch {
        held = false;
      }
    }
    try {
      await zipDirectory(config.serverDir, tempPath, [
        'bedrock_server',
        'bedrock_server_how_to.html',
        'profanity_filter.wlist',
        'release-notes.txt',
        '*.log',
        'logs/*',
        'crashdumps/*',
        '*.zip',
        'backups/*',
      ]);
      await fsp.rename(tempPath, finalPath);
    } finally {
      if (held) {
        try {
          this.write('save resume');
        } catch {
          /* ignore */
        }
      }
      await fsp.rm(tempPath, { force: true }).catch(() => {});
    }
    const stat = await fsp.stat(finalPath);
    Promise.resolve()
      .then(() => s3.upload(finalPath))
      .then((result) => {
        if (result.ok) this._appendLog(`[panel] ${result.message}`);
        else if (!result.skipped) this._appendLog(`[panel] ${result.message}`);
      })
      .catch(() => {});
    return {
      ok: true,
      name,
      size: formatBytes(stat.size),
      message: `${name} (${formatBytes(stat.size)})`,
    };
  }

  async listBackups() {
    let entries = [];
    try {
      entries = await fsp.readdir(config.backupDir, { withFileTypes: true });
    } catch {
      return [];
    }
    const backups = [];
    for (const entry of entries) {
      if (!entry.isFile() || !entry.name.endsWith('.zip')) continue;
      const full = path.join(config.backupDir, entry.name);
      const stat = await fsp.stat(full).catch(() => null);
      if (!stat) continue;
      const match = entry.name.match(/^backup_(\d{4}-\d{2}-\d{2})_(\d{2})-(\d{2})_#\d+_(.+)\.zip$/);
      backups.push({
        name: entry.name,
        date: match ? `${match[1]} ${match[2]}:${match[3]}` : stat.mtime.toISOString().slice(0, 16).replace('T', ' '),
        type: match ? match[4] : 'manual',
        size: stat.size,
        size_h: formatBytes(stat.size),
        mtime: stat.mtimeMs,
      });
    }
    backups.sort((a, b) => b.mtime - a.mtime);
    return backups;
  }

  backupPath(name) {
    const clean = safeName(name, '');
    if (!clean || !clean.endsWith('.zip')) return null;
    const full = path.resolve(config.backupDir, clean);
    if (!full.startsWith(path.resolve(config.backupDir) + path.sep)) return null;
    return fs.existsSync(full) ? full : null;
  }

  async deleteBackup(name) {
    const target = this.backupPath(name);
    if (!target) return { ok: false, message: 'Backup not found' };
    await fsp.rm(target, { force: true });
    return { ok: true, message: `Deleted ${name}` };
  }

  async restoreBackup(name) {
    const target = this.backupPath(name);
    if (!target) return { ok: false, message: 'Backup not found' };
    if (this.running) await this.stop({ reason: 'restore' });
    await this.createBackup('pre-restore');
    const staging = path.join(config.uploadDir, `restore-${Date.now()}`);
    try {
      await unzipTo(target, staging);
      const root = await this._findExtractedRoot(staging).catch(async () => {
        if (await pathExists(path.join(staging, 'worlds'))) return staging;
        throw new Error('Backup does not contain a server worlds folder');
      });
      const copy = ['worlds', 'server.properties', 'allowlist.json', 'permissions.json', 'behavior_packs', 'resource_packs'];
      for (const item of copy) {
        const source = path.join(root, item);
        if (!(await pathExists(source))) continue;
        const destination = path.join(config.serverDir, item);
        await fsp.rm(destination, { recursive: true, force: true });
        await fsp.cp(source, destination, { recursive: true, force: true });
      }
      return { ok: true, message: `Restored ${name}. The server is stopped so you can review it.` };
    } catch (error) {
      return { ok: false, message: `Restore failed: ${error.message}` };
    } finally {
      await fsp.rm(staging, { recursive: true, force: true }).catch(() => {});
    }
  }

  async listWorlds() {
    const worldsDir = path.join(config.serverDir, 'worlds');
    const props = await this.getProperties();
    const active = props['level-name'] || config.levelName;
    let entries = [];
    try {
      entries = await fsp.readdir(worldsDir, { withFileTypes: true });
    } catch {
      return { worlds: [], active };
    }
    const worlds = [];
    for (const entry of entries) {
      if (!entry.isDirectory()) continue;
      const full = path.join(worldsDir, entry.name);
      const { bytes, files } = await dirSize(full);
      worlds.push({
        name: entry.name,
        size: bytes,
        size_h: formatBytes(bytes),
        files,
        active: entry.name === active,
      });
    }
    worlds.sort((a, b) => (a.active === b.active ? a.name.localeCompare(b.name) : a.active ? -1 : 1));
    return { worlds, active };
  }

  async createWorld(name, seed, gamemode, difficulty, levelType) {
    const clean = String(name || '').trim();
    if (!/^[A-Za-z0-9_-]{1,32}$/.test(clean)) return { ok: false, message: 'World name must be 1-32 letters, numbers, _ or -' };
    const worldDir = path.join(config.serverDir, 'worlds', clean);
    if (fs.existsSync(worldDir)) return { ok: false, message: 'A world with that name already exists' };
    await this.saveProperties({
      'level-name': clean,
      'level-seed': String(seed || ''),
      gamemode: String(gamemode || 'survival'),
      difficulty: String(difficulty || 'normal'),
      'level-type': String(levelType || 'DEFAULT'),
    });
    if (this.running) {
      const result = await this.restart();
      return { ok: result.ok, message: result.ok ? `Created and activated ${clean}` : result.message };
    }
    return { ok: true, message: `World ${clean} will be generated on the next start` };
  }

  async activateWorld(name) {
    const clean = String(name || '').trim();
    const worldDir = path.join(config.serverDir, 'worlds', clean);
    if (!clean || !fs.existsSync(worldDir)) return { ok: false, message: 'World not found' };
    await this.saveProperties({ 'level-name': clean });
    if (this.running) {
      const result = await this.restart();
      return { ok: result.ok, message: result.ok ? `Activated ${clean}` : result.message };
    }
    return { ok: true, message: `Active world set to ${clean}` };
  }

  async deleteWorld(name) {
    const clean = String(name || '').trim();
    const props = await this.getProperties();
    if (clean === (props['level-name'] || config.levelName)) return { ok: false, message: 'Cannot delete the active world' };
    const worldDir = path.join(config.serverDir, 'worlds', clean);
    if (!clean || !fs.existsSync(worldDir)) return { ok: false, message: 'World not found' };
    if (this.running) return { ok: false, message: 'Stop the server before deleting a world' };
    await this.createBackup('pre-delete-world');
    await fsp.rm(worldDir, { recursive: true, force: true });
    return { ok: true, message: `Deleted world ${clean}` };
  }

  async createWorldZip(name) {
    const clean = String(name || '').trim();
    const worldDir = path.join(config.serverDir, 'worlds', clean);
    if (!clean || !fs.existsSync(worldDir)) return null;
    const target = path.join(config.uploadDir, `${safeName(clean, 'world')}-${Date.now()}.zip`);
    await zipDirectory(worldDir, target);
    return target;
  }

  async installWorldZip(zipPath, name, replace = false) {
    const props = await this.getProperties();
    const active = props['level-name'] || config.levelName;
    const clean = String(name || '').trim() || path.basename(zipPath, '.zip');
    if (!/^[A-Za-z0-9_-]{1,32}$/.test(clean)) return { ok: false, message: 'World name must be 1-32 letters, numbers, _ or -' };
    const destination = path.join(config.serverDir, 'worlds', clean);
    if (fs.existsSync(destination) && !replace) return { ok: false, message: 'That world already exists (tick replace to overwrite)' };
    if (fs.existsSync(destination) && replace) {
      if (clean === active && this.running) await this.stop({ reason: 'replace-world' });
      await this.createBackup('pre-replace-world');
    }
    const staging = path.join(config.uploadDir, `world-${Date.now()}`);
    try {
      await unzipTo(zipPath, staging);
      let root = staging;
      const entries = await fsp.readdir(staging, { withFileTypes: true });
      if (!(await pathExists(path.join(staging, 'level.dat'))) && !(await pathExists(path.join(staging, 'db')))) {
        const dirs = entries.filter((entry) => entry.isDirectory());
        if (dirs.length === 1) {
          const candidate = path.join(staging, dirs[0].name);
          if ((await pathExists(path.join(candidate, 'level.dat'))) || (await pathExists(path.join(candidate, 'db')))) root = candidate;
        }
      }
      if (!(await pathExists(path.join(root, 'level.dat'))) && !(await pathExists(path.join(root, 'db')))) {
        return { ok: false, message: 'This ZIP does not look like a Bedrock world (no level.dat or db folder)' };
      }
      await fsp.rm(destination, { recursive: true, force: true });
      await fsp.mkdir(path.join(config.serverDir, 'worlds'), { recursive: true });
      await fsp.cp(root, destination, { recursive: true, force: true });
      return { ok: true, message: `Installed world ${clean}` };
    } catch (error) {
      return { ok: false, message: `World upload failed: ${error.message}` };
    } finally {
      await fsp.rm(staging, { recursive: true, force: true }).catch(() => {});
    }
  }

  _readJson(file, fallback) {
    try {
      const value = JSON.parse(fs.readFileSync(file, 'utf8'));
      return Array.isArray(value) ? value : fallback;
    } catch {
      return fallback;
    }
  }

  _writeJson(file, value) {
    fs.writeFileSync(file, `${JSON.stringify(value, null, 2)}\n`);
  }

  async getPlayers() {
    const allowlist = this._readJson(path.join(config.serverDir, 'allowlist.json'), []).map((item) => item.name).filter(Boolean);
    const permissions = this._readJson(path.join(config.serverDir, 'permissions.json'), []);
    const banned = this._readJson(path.join(config.serverDir, 'banned-players.json'), []).map((item) => item.name).filter(Boolean);
    const opXuids = new Set(
      permissions.filter((item) => String(item.permission).toLowerCase() === 'operator').map((item) => String(item.xuid))
    );
    const allowSet = new Set(allowlist);
    const banSet = new Set(banned);
    const names = new Set([...allowlist, ...banned, ...this.online.keys()]);
    for (const [name, xuid] of this.xuidMap) {
      if (opXuids.has(xuid)) names.add(name);
    }
    const players = [];
    for (const name of names) {
      const xuid = this.online.get(name)?.xuid || this.xuidMap.get(name) || '';
      players.push({
        name,
        xuid,
        online: this.online.has(name),
        op: xuid ? opXuids.has(xuid) : false,
        whitelisted: allowSet.has(name),
        banned: banSet.has(name),
      });
    }
    players.sort((a, b) => (a.online === b.online ? a.name.localeCompare(b.name) : a.online ? -1 : 1));
    return players;
  }

  _commandTarget(name) {
    return `"${String(name).replace(/["\r\n]/g, '').trim()}"`;
  }

  async playerAction(name, action) {
    const clean = String(name || '').replace(/[\r\n"]/g, '').trim();
    if (!clean || clean.length > 32) return { ok: false, message: 'Enter a valid player name' };
    const allowlistPath = path.join(config.serverDir, 'allowlist.json');
    const permissionsPath = path.join(config.serverDir, 'permissions.json');
    const bannedPath = path.join(config.serverDir, 'banned-players.json');

    if (action === 'whitelist_add' || action === 'whitelist_remove') {
      if (this.running) {
        const command = action === 'whitelist_add' ? `allowlist add ${this._commandTarget(clean)}` : `allowlist remove ${this._commandTarget(clean)}`;
        return this.sendCommand(command);
      }
      const list = this._readJson(allowlistPath, []);
      if (action === 'whitelist_add' && !list.some((item) => item.name === clean)) {
        list.push({ name: clean, ignoresPlayerLimit: false });
      }
      if (action === 'whitelist_remove') {
        const filtered = list.filter((item) => item.name !== clean);
        if (filtered.length !== list.length) this._writeJson(allowlistPath, filtered);
      } else {
        this._writeJson(allowlistPath, list);
      }
      return { ok: true, message: `${clean} ${action === 'whitelist_add' ? 'added to' : 'removed from'} the allowlist` };
    }

    if (action === 'op' || action === 'deop') {
      if (this.running) return this.sendCommand(`${action} ${this._commandTarget(clean)}`);
      const xuid = this.xuidMap.get(clean);
      if (!xuid) return { ok: false, message: 'Start the server and let this player join once so the panel can learn their XUID' };
      const list = this._readJson(permissionsPath, []);
      const filtered = list.filter((item) => String(item.xuid) !== String(xuid));
      if (action === 'op') filtered.push({ permission: 'operator', xuid: String(xuid) });
      this._writeJson(permissionsPath, filtered);
      return { ok: true, message: `${clean} ${action === 'op' ? 'is now' : 'is no longer'} an operator` };
    }

    if (action === 'ban' || action === 'pardon') {
      if (this.running) return this.sendCommand(`${action} ${this._commandTarget(clean)}`);
      if (action === 'pardon') {
        const list = this._readJson(bannedPath, []).filter((item) => item.name !== clean);
        this._writeJson(bannedPath, list);
        return { ok: true, message: `${clean} pardoned` };
      }
      return { ok: false, message: 'Start the server to ban a player (the official server records bans itself)' };
    }

    if (action === 'kick') {
      if (!this.online.has(clean)) return { ok: false, message: `${clean} is not online` };
      return this.sendCommand(`kick ${this._commandTarget(clean)}`);
    }

    return { ok: false, message: 'Unknown player action' };
  }

  async listFiles(relative = '') {
    const current = resolveInside(config.serverDir, relative);
    const entries = await fsp.readdir(current, { withFileTypes: true });
    const items = [];
    for (const entry of entries) {
      if (entry.name === 'bedrock_server' && entry.isFile()) continue;
      const full = path.join(current, entry.name);
      const stat = await fsp.stat(full).catch(() => null);
      if (!stat) continue;
      items.push({
        name: entry.name,
        path: relativeTo(config.serverDir, full),
        is_dir: entry.isDirectory(),
        size: stat.size,
        size_h: entry.isDirectory() ? '—' : formatBytes(stat.size),
        mtime: stat.mtimeMs,
      });
    }
    items.sort((a, b) => (a.is_dir === b.is_dir ? a.name.localeCompare(b.name) : a.is_dir ? -1 : 1));
    return { items, current: relativeTo(config.serverDir, current) };
  }

  async readFile(relative) {
    const target = resolveInside(config.serverDir, relative);
    const stat = await fsp.stat(target);
    if (!stat.isFile()) throw new Error('Not a file');
    if (stat.size > 2 * 1024 * 1024) throw new Error('File is too large to edit (2 MB limit)');
    const buffer = await fsp.readFile(target);
    if (buffer.includes(0)) throw new Error('Binary files cannot be edited');
    return buffer.toString('utf8');
  }

  async writeFile(relative, content) {
    const target = resolveInside(config.serverDir, relative);
    if (!(await pathExists(target))) return { ok: false, message: 'File not found' };
    await fsp.writeFile(target, String(content), 'utf8');
    return { ok: true, message: `Saved ${relativeTo(config.serverDir, target)}` };
  }

  async deletePath(relative) {
    if (!String(relative || '').trim()) return { ok: false, message: 'Refusing to delete the server root' };
    const target = resolveInside(config.serverDir, relative);
    if (!(await pathExists(target))) return { ok: false, message: 'Path not found' };
    await fsp.rm(target, { recursive: true, force: true });
    return { ok: true, message: `Deleted ${relativeTo(config.serverDir, target)}` };
  }

  async saveUploadedFile(directory, originalName, tempPath) {
    const dir = resolveInside(config.serverDir, directory);
    const stat = await fsp.stat(dir).catch(() => null);
    if (!stat || !stat.isDirectory()) return { ok: false, message: 'Upload target is not a folder' };
    const name = safeName(originalName, 'upload.bin');
    const target = path.join(dir, name);
    await fsp.copyFile(tempPath, target);
    return { ok: true, message: `Uploaded ${name}` };
  }

  downloadPath(relative) {
    try {
      const target = resolveInside(config.serverDir, relative);
      return fs.existsSync(target) && fs.statSync(target).isFile() ? target : null;
    } catch {
      return null;
    }
  }

  startDailyBackupScheduler() {
    if (this._dailyTimer) return;
    const check = async () => {
      const now = new Date();
      const today = now.toISOString().slice(0, 10);
      if (now.getHours() < config.backupHour) return;
      if (this.state.lastDaily === today) return;
      this.state.lastDaily = today;
      this._saveState();
      const result = await this.createBackup('daily').catch((error) => ({ ok: false, message: error.message }));
      this._appendLog(`[panel] Daily backup ${result.ok ? `created: ${result.message}` : `failed: ${result.message}`}`);
    };
    const runIfDue = () => check().catch(() => {});
    setTimeout(runIfDue, 30 * 1000);
    this._dailyTimer = setInterval(runIfDue, 5 * 60 * 1000);
  }

  startUpdateScheduler() {
    if (this._updateTimer) return;
    this._updateTimer = setInterval(() => {
      this.latestRelease(true)
        .then((releases) => {
          const installed = this.installedVersion();
          if (releases.latest && installed && releases.latest.version !== installed) {
            this._appendLog(`[panel] Update available: Bedrock ${releases.latest.version} (installed ${installed})`);
          }
        })
        .catch(() => {});
    }, config.versionCacheTtl);
  }

  async shutdown() {
    clearInterval(this._dailyTimer);
    clearInterval(this._updateTimer);
    if (this.running) await this.stop({ reason: 'panel shutdown' });
    if (this.logStream) {
      try {
        this.logStream.end();
      } catch {
        /* ignore */
      }
    }
  }
}

module.exports = new BedrockManager();
