const path = require('path');
const fs = require('fs');

const ROOT = path.resolve(__dirname, '..');
require('dotenv').config({ path: path.join(ROOT, '.env') });

function env(name, fallback) {
  const value = process.env[name];
  return value === undefined || value === '' ? fallback : value;
}

function bool(name, fallback) {
  const value = process.env[name];
  if (value === undefined || value === '') return fallback;
  return ['1', 'true', 'yes', 'on'].includes(String(value).toLowerCase());
}

function int(name, fallback) {
  const parsed = Number.parseInt(process.env[name], 10);
  return Number.isFinite(parsed) ? parsed : fallback;
}

const SERVER_DIR = path.resolve(env('MC_SERVER_DIR', path.join(ROOT, 'bedrock_server')));
const BACKUP_DIR = path.resolve(env('MC_BACKUP_DIR', path.join(ROOT, 'backups')));
const UPLOAD_DIR = path.resolve(env('MC_UPLOAD_DIR', path.join(ROOT, 'uploads')));
const RUNTIME_DIR = path.resolve(env('MC_RUNTIME_DIR', path.join(ROOT, 'runtime')));
const LOG_DIR = path.resolve(env('MC_LOG_DIR', path.join(ROOT, 'logs')));
const PUBLIC_DIR = path.join(ROOT, 'public');

const config = {
  root: ROOT,
  serverDir: SERVER_DIR,
  backupDir: BACKUP_DIR,
  uploadDir: UPLOAD_DIR,
  runtimeDir: RUNTIME_DIR,
  logDir: LOG_DIR,
  publicDir: PUBLIC_DIR,
  viewsDir: path.join(ROOT, 'views'),
  panelHost: env('MC_PANEL_HOST', '0.0.0.0'),
  panelPort: int('MC_PANEL_PORT', 5000),
  panelPassword: env('MC_PANEL_PASSWORD', 'admin123'),
  secretKey: env('SECRET_KEY', 'slyxpanel-change-this-secret'),
  maxUploadMb: int('MC_MAX_UPLOAD_MB', 1024),
  stopTimeout: int('MC_STOP_TIMEOUT', 30),
  startTimeout: int('MC_START_TIMEOUT', 90),
  backupHour: int('BACKUP_HOUR', 3),
  autoUpdate: bool('MC_AUTO_UPDATE', true),
  autoStart: bool('MC_AUTO_START', true),
  versionCacheTtl: int('MC_VERSION_CACHE_TTL', 21600) * 1000,
  consoleLines: int('MC_CONSOLE_LINES', 2000),
  logLines: int('MC_LOG_LINES', 400),
  serverPort: int('MC_SERVER_PORT', int('MC_BEDROCK_PORT', 19132)),
  serverPortV6: int('MC_SERVER_PORT_V6', int('MC_BEDROCK_PORT_V6', 19133)),
  udpPorts: env('MC_UDP_PORTS', ''),
  serverName: env('MC_MOTD', 'SlyxCraft Bedrock Server'),
  maxPlayers: int('MC_MAX_PLAYERS', 20),
  gamemode: env('MC_GAMEMODE', 'survival'),
  difficulty: env('MC_DIFFICULTY', 'normal'),
  levelName: env('MC_LEVEL_NAME', 'Bedrock level'),
  viewDistance: int('MC_VIEW_DISTANCE', 10),
  tickDistance: int('MC_TICK_DISTANCE', 4),
  maxThreads: int('MC_MAX_THREADS', 4),
  transport: env('MC_TRANSPORT', 'nethernet'),
  aws: {
    bucket: env('AWS_BUCKET_NAME', '').trim(),
    endpoint: env('AWS_ENDPOINT_URL_S3', env('AWS_ENDPOINT_URL', '')).trim(),
    region: env('AWS_REGION', 'us-east-1'),
    accessKeyId: env('AWS_ACCESS_KEY_ID', ''),
    secretAccessKey: env('AWS_SECRET_ACCESS_KEY', ''),
    prefix: env('AWS_BACKUP_PREFIX', 'minecraft-backups'),
  },
  officialLinksApi: 'https://net-secondary.web.minecraft-services.net/api/v1.0/download/links',
};

for (const dir of [config.serverDir, config.backupDir, config.uploadDir, config.runtimeDir, config.logDir]) {
  fs.mkdirSync(dir, { recursive: true });
}

module.exports = config;
