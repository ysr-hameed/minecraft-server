const crypto = require('crypto');
const fs = require('fs');
const os = require('os');
const path = require('path');
const express = require('express');
const session = require('cookie-session');
const multer = require('multer');

const config = require('./src/config');
const manager = require('./src/manager');
const s3 = require('./src/s3');
const { formatBytes, safeName } = require('./src/util');

const app = express();
app.disable('x-powered-by');
app.set('view engine', 'ejs');
app.set('views', config.viewsDir);
app.use('/static', express.static(config.publicDir, { maxAge: '1h' }));
app.use(express.urlencoded({ extended: true, limit: '4mb' }));
app.use(express.json({ limit: '4mb' }));
app.use(
  session({
    name: 'slyx.sid',
    keys: [config.secretKey],
    httpOnly: true,
    sameSite: 'lax',
    secure: false,
    maxAge: 7 * 24 * 60 * 60 * 1000,
  })
);

const upload = multer({
  dest: config.uploadDir,
  limits: { fileSize: config.maxUploadMb * 1024 * 1024 },
});

const FIELD_GROUPS = [
  {
    name: 'Network',
    fields: [
      ['server-port', 'Server port (UDP)', 'number'],
      ['server-portv6', 'IPv6 port (UDP)', 'number'],
      ['server-ip', 'Bind address', 'text'],
      ['transport', 'Network transport', 'transport'],
      ['enable-lan-visibility', 'LAN discovery', 'bool'],
      ['server-udp-ports', 'NetherNet UDP ports', 'text'],
    ],
  },
  {
    name: 'Server',
    fields: [
      ['server-name', 'Server name / MOTD', 'text'],
      ['max-players', 'Max players', 'number'],
      ['allow-list', 'Allow list (whitelist)', 'bool'],
      ['online-mode', 'Xbox Live authentication', 'bool'],
      ['default-player-permission-level', 'Default permission', 'permission'],
      ['texturepack-required', 'Require resource pack', 'bool'],
    ],
  },
  {
    name: 'Gameplay',
    fields: [
      ['gamemode', 'Default game mode', 'gamemode'],
      ['difficulty', 'Difficulty', 'difficulty'],
      ['force-gamemode', 'Force game mode', 'bool'],
      ['allow-cheats', 'Allow cheats', 'bool'],
      ['level-name', 'Active world', 'text'],
      ['level-seed', 'World seed', 'text'],
      ['view-distance', 'View distance', 'number'],
      ['tick-distance', 'Tick distance', 'number'],
      ['player-idle-timeout', 'Player idle timeout (minutes)', 'number'],
      ['max-threads', 'Max threads', 'number'],
    ],
  },
];
const GROUPED_KEYS = new Set(FIELD_GROUPS.flatMap((group) => group.fields.map(([key]) => key)));

const LOGIN_ATTEMPTS = new Map();

function wrap(handler) {
  return (req, res, next) => Promise.resolve(handler(req, res, next)).catch(next);
}

function passwordMatches(candidate) {
  const expected = Buffer.from(String(config.panelPassword));
  const supplied = Buffer.from(String(candidate || ''));
  if (expected.length !== supplied.length) return false;
  return crypto.timingSafeEqual(expected, supplied);
}

function flash(req, type, message) {
  if (!req.session.flash) req.session.flash = [];
  req.session.flash.push({ type, message });
}

function setFlashResult(req, result, successMessage) {
  if (result && result.ok) flash(req, 'success', successMessage || result.message || 'Done');
  else flash(req, 'error', (result && result.message) || 'Operation failed');
}

function loginRequired(req, res, next) {
  if (!req.session.loggedIn) {
    const target = req.path && req.path.startsWith('/') ? req.path : '/';
    return res.redirect(`/login?next=${encodeURIComponent(target)}`);
  }
  next();
}

function localAddresses() {
  const values = [];
  for (const entries of Object.values(os.networkInterfaces())) {
    for (const entry of entries || []) {
      if (entry.family === 'IPv4' && !entry.internal) values.push(entry.address);
    }
  }
  return values;
}

app.use((req, res, next) => {
  res.locals.flash = req.session.flash || [];
  delete req.session.flash;
  res.locals.formatBytes = formatBytes;
  res.locals.currentPath = req.path;
  res.locals.version = require('./package.json').version;
  next();
});

app.get('/login', (req, res) => {
  if (req.session.loggedIn) return res.redirect('/');
  res.render('login', { error: null, next: req.query.next || '/' });
});

app.post('/login', (req, res) => {
  const key = req.ip || 'unknown';
  const now = Date.now();
  const record = LOGIN_ATTEMPTS.get(key) || { count: 0, first: now };
  if (now - record.first > 15 * 60 * 1000) {
    record.count = 0;
    record.first = now;
  }
  if (record.count >= 10) {
    return res.status(429).render('login', { error: 'Too many attempts. Try again in 15 minutes.', next: req.body.next || '/' });
  }
  if (passwordMatches(req.body.password)) {
    LOGIN_ATTEMPTS.delete(key);
    req.session.loggedIn = true;
    const target = String(req.body.next || '/');
    res.redirect(target.startsWith('/') && !target.startsWith('//') ? target : '/');
    return;
  }
  record.count += 1;
  LOGIN_ATTEMPTS.set(key, record);
  res.status(401).render('login', { error: 'Wrong password', next: req.body.next || '/' });
});

app.get('/logout', (req, res) => {
  req.session = null;
  res.redirect('/login');
});

app.get('/healthz', (req, res) => {
  res.json({ status: 'ok', server: 'Bedrock Dedicated Server', running: manager.running });
});

app.use(loginRequired);

app.get(
  '/',
  wrap(async (req, res) => {
    const [stats, props, backups, runtime] = await Promise.all([
      manager.status(),
      manager.getProperties(),
      manager.listBackups(),
      manager.runtimeStatus(),
    ]);
    const hostIp = String(req.headers.host || '').split(':')[0];
    const hosts = [];
    for (const value of [hostIp, ...localAddresses(), '127.0.0.1']) {
      if (value && !hosts.includes(value)) hosts.push(value);
    }
    res.render('dashboard', {
      active: 'dashboard',
      stats,
      props,
      backups: backups.slice(0, 5),
      runtime,
      hosts,
      autoUpdate: manager.autoUpdateEnabled(),
    });
  })
);

app.get(
  '/api/stats',
  wrap(async (req, res) => {
    res.json(await manager.status());
  })
);

app.get('/api/logs', (req, res) => {
  const stats = { running: manager.running, player_count: manager.online.size };
  res.json({
    logs: manager.logsSnapshot(400),
    running: manager.running,
    player_count: stats.player_count,
    players: [...manager.online.keys()],
    version: manager.liveVersion || manager.installedVersion(),
    port: config.serverPort,
    uptime: manager.startedAt ? Math.floor((Date.now() - manager.startedAt.getTime()) / 1000) : 0,
  });
});

app.get('/api/host', (req, res) => {
  res.json({ host: String(req.headers.host || '').split(':')[0], lan: localAddresses()[0] || '127.0.0.1' });
});

app.post(
  '/server/:action',
  wrap(async (req, res) => {
    const action = req.params.action;
    let result;
    if (action === 'start') result = await manager.start();
    else if (action === 'stop') result = await manager.stop({ reason: 'panel' });
    else if (action === 'restart') result = await manager.restart();
    else result = { ok: false, message: 'Unknown server action' };
    setFlashResult(req, result);
    res.redirect(req.get('referer') || '/');
  })
);

app.post('/server/install', (req, res) => {
  manager
    .latestRelease(true)
    .then((releases) => {
      if (!releases.latest) throw new Error('No official release found');
      return manager.install(releases.latest, { force: true });
    })
    .then((result) => manager._appendLog(`[panel] ${result.message}`))
    .catch((error) => manager._appendLog(`[panel] Install failed: ${error.message}`));
  flash(req, 'success', 'Download started. Watch the console for progress.');
  res.redirect(req.get('referer') || '/settings');
});

app.post(
  '/server/version',
  wrap(async (req, res) => {
    const requested = String(req.body.version || '');
    const versions = await manager.versions();
    const match = versions.find((item) => item.version === requested);
    if (!match || !match.url) {
      flash(req, 'error', 'That version is not available from the official download API');
      return res.redirect('/settings');
    }
    const result = await manager.install({ version: match.version, url: match.url }, { force: true });
    setFlashResult(req, result);
    res.redirect('/settings');
  })
);

app.post('/server/auto-update', (req, res) => {
  const enabled = ['on', 'true', '1', 'yes'].includes(String(req.body.enabled || '').toLowerCase());
  manager.setAutoUpdate(enabled);
  flash(req, 'success', `Automatic updates ${enabled ? 'enabled' : 'disabled'}`);
  res.redirect('/settings');
});

app.get(
  '/console',
  wrap(async (req, res) => {
    const status = await manager.status();
    res.render('console', {
      active: 'console',
      logs: manager.logsSnapshot(400),
      running: manager.running,
      stats: status,
    });
  })
);

app.post('/console', (req, res) => {
  setFlashResult(req, manager.sendCommand(req.body.command || ''));
  res.redirect('/console');
});

app.get(
  '/worlds',
  wrap(async (req, res) => {
    const [worldData, backups] = await Promise.all([manager.listWorlds(), manager.listBackups()]);
    res.render('worlds', {
      active: 'worlds',
      worlds: worldData.worlds,
      activeWorld: worldData.active,
      backups,
    });
  })
);

app.post(
  '/worlds',
  wrap(async (req, res) => {
    const action = req.body.action;
    let result;
    if (action === 'create') {
      result = await manager.createWorld(req.body.name, req.body.seed, req.body.gamemode, req.body.difficulty, req.body.level_type);
    } else if (action === 'backup') {
      result = await manager.createBackup('manual');
    } else if (action === 'restore') {
      result = await manager.restoreBackup(req.body.backup);
    } else if (action === 'delete_backup') {
      result = await manager.deleteBackup(req.body.backup);
    } else if (action === 'delete_world') {
      result = await manager.deleteWorld(req.body.world);
    } else if (action === 'activate_world') {
      result = await manager.activateWorld(req.body.world);
    } else {
      result = { ok: false, message: 'Unknown world action' };
    }
    setFlashResult(req, result);
    res.redirect('/worlds');
  })
);

app.post(
  '/world/upload',
  upload.single('world_zip'),
  wrap(async (req, res) => {
    if (!req.file) {
      flash(req, 'error', 'Choose a .zip world file');
      return res.redirect('/worlds');
    }
    const original = req.file.originalname || 'world.zip';
    if (!original.toLowerCase().endsWith('.zip')) {
      fs.rmSync(req.file.path, { force: true });
      flash(req, 'error', 'World uploads must be .zip files');
      return res.redirect('/worlds');
    }
    const requested = String(req.body.world_name || '').trim() || path.basename(original, path.extname(original));
    const name = safeName(requested, 'world');
    const result = await manager.installWorldZip(req.file.path, name, req.body.replace === 'on');
    fs.rmSync(req.file.path, { force: true });
    setFlashResult(req, result);
    res.redirect('/worlds');
  })
);

app.get(
  '/world/download/:name',
  wrap(async (req, res) => {
    const archive = await manager.createWorldZip(req.params.name);
    if (!archive) {
      flash(req, 'error', 'World not found');
      return res.redirect('/worlds');
    }
    res.download(archive, `${safeName(req.params.name, 'world')}.zip`, () => {
      fs.rm(archive, { force: true }, () => {});
    });
  })
);

app.get('/backup/download/:name', (req, res) => {
  const target = manager.backupPath(req.params.name);
  if (!target) {
    flash(req, 'error', 'Backup not found');
    return res.redirect('/backups');
  }
  res.download(target, path.basename(target));
});

app.get(
  '/players',
  wrap(async (req, res) => {
    const query = String(req.query.q || '').trim().toLowerCase();
    const filter = String(req.query.filter || 'all');
    let players = await manager.getPlayers();
    if (query) players = players.filter((player) => player.name.toLowerCase().includes(query));
    if (filter === 'online') players = players.filter((player) => player.online);
    else if (filter === 'op') players = players.filter((player) => player.op);
    else if (filter === 'banned') players = players.filter((player) => player.banned);
    else if (filter === 'whitelisted') players = players.filter((player) => player.whitelisted);
    res.render('players', {
      active: 'players',
      players,
      query,
      selectedFilter: filter,
      onlineCount: manager.online.size,
    });
  })
);

app.post(
  '/players',
  wrap(async (req, res) => {
    setFlashResult(req, await manager.playerAction(req.body.player, req.body.action));
    res.redirect('/players');
  })
);

app.get(
  '/settings',
  wrap(async (req, res) => {
    const [props, runtime, versions] = await Promise.all([manager.getProperties(), manager.runtimeStatus(), manager.versions()]);
    const extraProperties = Object.fromEntries(Object.entries(props).filter(([key]) => !GROUPED_KEYS.has(key)));
    res.render('settings', {
      active: 'settings',
      props,
      fieldGroups: FIELD_GROUPS,
      extraProperties,
      runtime,
      versions,
      autoUpdate: manager.autoUpdateEnabled(),
      serverDir: config.serverDir,
    });
  })
);

app.post(
  '/settings',
  wrap(async (req, res) => {
    const updates = {};
    for (const [key, value] of Object.entries(req.body)) {
      if (key.startsWith('prop__')) updates[key.slice(6)] = value;
    }
    const limits = {
      'server-port': [1, 65535],
      'server-portv6': [1, 65535],
      'max-players': [1, 500],
      'view-distance': [5, 64],
      'tick-distance': [4, 12],
      'player-idle-timeout': [0, 1440],
      'max-threads': [0, 64],
    };
    for (const [key, [minimum, maximum]] of Object.entries(limits)) {
      if (!(key in updates)) continue;
      const number = Number.parseInt(updates[key], 10);
      if (!Number.isFinite(number)) {
        flash(req, 'error', `${key} must be a number`);
        return res.redirect('/settings');
      }
      if (number < minimum || number > maximum) {
        flash(req, 'error', `${key} must be between ${minimum} and ${maximum}`);
        return res.redirect('/settings');
      }
      updates[key] = String(number);
    }
    if ('server-name' in updates) updates['server-name'] = updates['server-name'].trim().slice(0, 128) || 'SlyxCraft Bedrock Server';
    if (!Object.keys(updates).length) {
      flash(req, 'error', 'No settings supplied');
      return res.redirect('/settings');
    }
    setFlashResult(req, await manager.saveProperties(updates));
    res.redirect('/settings');
  })
);

app.get(
  '/files',
  wrap(async (req, res) => {
    const relative = String(req.query.path || '');
    let listing;
    try {
      listing = await manager.listFiles(relative);
    } catch (error) {
      flash(req, 'error', `Cannot open folder: ${error.message}`);
      return res.redirect('/files');
    }
    let editPath = String(req.query.edit || '');
    let content = '';
    if (editPath) {
      try {
        content = await manager.readFile(editPath);
      } catch (error) {
        flash(req, 'error', error.message);
        editPath = '';
      }
    }
    res.render('files', {
      active: 'files',
      items: listing.items,
      current: listing.current,
      parent: path.posix.dirname(listing.current) === '.' ? '' : path.posix.dirname(listing.current),
      editPath,
      content,
    });
  })
);

app.post(
  '/files/save',
  wrap(async (req, res) => {
    const relative = String(req.body.path || '');
    setFlashResult(req, await manager.writeFile(relative, req.body.content || ''));
    res.redirect(`/files?path=${encodeURIComponent(path.posix.dirname(relative) === '.' ? '' : path.posix.dirname(relative))}&edit=${encodeURIComponent(relative)}`);
  })
);

app.post(
  '/files/upload',
  upload.single('file'),
  wrap(async (req, res) => {
    const directory = String(req.body.path || '');
    if (!req.file) {
      flash(req, 'error', 'Choose a file to upload');
      return res.redirect(`/files?path=${encodeURIComponent(directory)}`);
    }
    const result = await manager.saveUploadedFile(directory, req.file.originalname, req.file.path);
    fs.rmSync(req.file.path, { force: true });
    setFlashResult(req, result);
    res.redirect(`/files?path=${encodeURIComponent(directory)}`);
  })
);

app.post(
  '/files/delete',
  wrap(async (req, res) => {
    const relative = String(req.body.path || '');
    const result = await manager.deletePath(relative);
    setFlashResult(req, result);
    res.redirect(`/files?path=${encodeURIComponent(path.posix.dirname(relative) === '.' ? '' : path.posix.dirname(relative))}`);
  })
);

app.get('/files/download', (req, res) => {
  const relative = String(req.query.path || '');
  const target = manager.downloadPath(relative);
  if (!target) {
    flash(req, 'error', 'File not found');
    return res.redirect(`/files?path=${encodeURIComponent(path.posix.dirname(relative) === '.' ? '' : path.posix.dirname(relative))}`);
  }
  res.download(target, path.basename(target));
});

app.get(
  '/backups',
  wrap(async (req, res) => {
    res.render('backups', {
      active: 'backups',
      backups: await manager.listBackups(),
      backupHour: config.backupHour,
      s3Enabled: s3.enabled(),
      s3Bucket: config.aws.bucket,
    });
  })
);

app.use((req, res) => {
  res.status(404).send('Not found');
});

app.use((error, req, res, next) => {
  if (res.headersSent) return next(error);
  console.error(error);
  flash(req, 'error', error.message || 'Unexpected error');
  res.redirect(req.get('referer') || '/');
});

const server = app.listen(config.panelPort, config.panelHost, async () => {
  console.log(`SlyxPanel ${require('./package.json').version}`);
  console.log(`Panel: http://${config.panelHost}:${config.panelPort}`);
  console.log(`Server directory: ${config.serverDir}`);
  console.log(`Transport: ${config.transport} · UDP ${config.serverPort}/${config.serverPortV6}`);
  if (config.panelPassword === 'admin123') {
    console.log('WARNING: Using the default panel password. Set MC_PANEL_PASSWORD in .env.');
  }
  manager.startDailyBackupScheduler();
  manager.startUpdateScheduler();
  if (config.autoUpdate) {
    manager.autoUpdateIfNeeded().catch((error) => console.error('Auto update failed:', error.message));
  }
  if (config.autoStart) {
    setTimeout(() => {
      manager
        .start()
        .then((result) => console.log(`Auto start: ${result.message}`))
        .catch((error) => console.error('Auto start failed:', error.message));
    }, 1500);
  }
});

let shuttingDown = false;
async function shutdown(signal) {
  if (shuttingDown) return;
  shuttingDown = true;
  console.log(`Received ${signal}, stopping...`);
  try {
    await manager.shutdown();
  } catch (error) {
    console.error('Shutdown error:', error.message);
  }
  server.close(() => process.exit(0));
  setTimeout(() => process.exit(0), 5000).unref();
}

process.on('SIGTERM', () => shutdown('SIGTERM'));
process.on('SIGINT', () => shutdown('SIGINT'));
