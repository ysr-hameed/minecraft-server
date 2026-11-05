const fs = require('fs');
const fsp = require('fs/promises');
const path = require('path');
const { execFile } = require('child_process');

function run(file, args, options = {}) {
  return new Promise((resolve, reject) => {
    execFile(file, args, { timeout: 0, maxBuffer: 1024 * 1024 * 32, ...options }, (error, stdout, stderr) => {
      if (error) {
        error.stdout = stdout;
        error.stderr = stderr;
        reject(error);
      } else {
        resolve({ stdout, stderr });
      }
    });
  });
}

function formatBytes(bytes) {
  if (!Number.isFinite(bytes) || bytes <= 0) return '0 B';
  const units = ['B', 'KB', 'MB', 'GB', 'TB'];
  const index = Math.min(Math.floor(Math.log(bytes) / Math.log(1024)), units.length - 1);
  const value = bytes / Math.pow(1024, index);
  return `${value >= 100 ? value.toFixed(0) : value.toFixed(1)} ${units[index]}`;
}

function formatDuration(ms) {
  if (!ms || ms < 0) return '—';
  const total = Math.floor(ms / 1000);
  const days = Math.floor(total / 86400);
  const hours = Math.floor((total % 86400) / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  const seconds = total % 60;
  if (days) return `${days}d ${hours}h`;
  if (hours) return `${hours}h ${minutes}m`;
  if (minutes) return `${minutes}m ${seconds}s`;
  return `${seconds}s`;
}

function timestamp(date = new Date()) {
  const pad = (n) => String(n).padStart(2, '0');
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}_${pad(date.getHours())}-${pad(date.getMinutes())}`;
}

function safeName(value, fallback = 'file') {
  const base = path.basename(String(value || '')).replace(/[^A-Za-z0-9._ -]/g, '_').trim();
  return base && base !== '.' && base !== '..' ? base : fallback;
}

function resolveInside(base, relative = '') {
  const clean = String(relative || '').replace(/\\/g, '/').replace(/^\/+/, '');
  const target = path.resolve(base, clean);
  const root = path.resolve(base);
  if (target !== root && !target.startsWith(root + path.sep)) {
    throw new Error('Path escapes the server directory');
  }
  return target;
}

function relativeTo(base, target) {
  return path.relative(base, target).split(path.sep).join('/');
}

async function pathExists(target) {
  try {
    await fsp.access(target);
    return true;
  } catch {
    return false;
  }
}

async function dirSize(dir) {
  let total = 0;
  let files = 0;
  const stack = [dir];
  while (stack.length) {
    const current = stack.pop();
    let entries;
    try {
      entries = await fsp.readdir(current, { withFileTypes: true });
    } catch {
      continue;
    }
    for (const entry of entries) {
      const full = path.join(current, entry.name);
      if (entry.isSymbolicLink()) continue;
      if (entry.isDirectory()) stack.push(full);
      else if (entry.isFile()) {
        files += 1;
        try {
          total += (await fsp.stat(full)).size;
        } catch {
          /* ignore */
        }
      }
    }
  }
  return { bytes: total, files };
}

function zipDirectory(sourceDir, zipPath, excludes = []) {
  const args = ['-r', '-q', '-y', zipPath, '.'];
  if (excludes.length) args.push('-x', ...excludes);
  return run('zip', args, { cwd: sourceDir });
}

function unzipTo(zipPath, destination) {
  fs.mkdirSync(destination, { recursive: true });
  return run('unzip', ['-o', '-q', zipPath, '-d', destination]);
}

function tailLines(text, limit) {
  if (!text) return [];
  const lines = text.split(/\r?\n/);
  if (lines[lines.length - 1] === '') lines.pop();
  return lines.slice(-limit);
}

function parseProperties(content) {
  const values = {};
  const lines = content.split(/\r?\n/);
  for (const line of lines) {
    const trimmed = line.trim();
    if (!trimmed || trimmed.startsWith('#') || trimmed.startsWith('!')) continue;
    const index = trimmed.indexOf('=');
    if (index === -1) continue;
    const key = trimmed.slice(0, index).trim();
    let value = trimmed.slice(index + 1).trim();
    if ((value.startsWith('"') && value.endsWith('"')) || (value.startsWith("'") && value.endsWith("'"))) {
      value = value.slice(1, -1);
    }
    values[key] = value;
  }
  return values;
}

function updateProperties(content, updates) {
  const pending = new Map(Object.entries(updates).map(([key, value]) => [key, String(value)]));
  const output = content.split(/\r?\n/).map((line) => {
    const match = line.match(/^(\s*)([^#!\s][^=]*?)(\s*)=(.*)$/);
    if (!match) return line;
    const key = match[2].trim();
    if (!pending.has(key)) return line;
    const value = pending.get(key);
    pending.delete(key);
    return `${match[1]}${key}${match[3]}=${value}`;
  });
  if (pending.size) {
    if (output.length && output[output.length - 1] === '') output.pop();
    for (const [key, value] of pending) output.push(`${key}=${value}`);
    output.push('');
  }
  return output.join('\n');
}

module.exports = {
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
};
