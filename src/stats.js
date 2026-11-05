const fs = require('fs');
const fsp = require('fs/promises');
const path = require('path');
const os = require('os');

let previousCpu = readCpu();

function readCpu() {
  try {
    const line = fs.readFileSync('/proc/stat', 'utf8').split('\n')[0];
    const parts = line.trim().split(/\s+/).slice(1).map(Number);
    const idle = (parts[3] || 0) + (parts[4] || 0);
    const total = parts.reduce((sum, value) => sum + (Number.isFinite(value) ? value : 0), 0);
    return { idle, total };
  } catch {
    return null;
  }
}

function cpuPercent() {
  const current = readCpu();
  if (!current) {
    const load = os.loadavg()[0];
    const cores = Math.max(os.cpus().length, 1);
    return Math.min(100, Math.round((load / cores) * 100));
  }
  if (!previousCpu) {
    previousCpu = current;
    return 0;
  }
  const idleDelta = current.idle - previousCpu.idle;
  const totalDelta = current.total - previousCpu.total;
  previousCpu = current;
  if (totalDelta <= 0) return 0;
  return Math.max(0, Math.min(100, Math.round((1 - idleDelta / totalDelta) * 100)));
}

function memory() {
  try {
    const info = {};
    for (const line of fs.readFileSync('/proc/meminfo', 'utf8').split('\n')) {
      const match = line.match(/^(\w+):\s+(\d+)\s*kB/);
      if (match) info[match[1]] = Number(match[2]) * 1024;
    }
    const total = info.MemTotal || os.totalmem();
    const available = info.MemAvailable || info.MemFree || os.freemem();
    const used = Math.max(0, total - available);
    return {
      totalMb: Math.round(total / 1024 / 1024),
      usedMb: Math.round(used / 1024 / 1024),
      freeMb: Math.round(available / 1024 / 1024),
      percent: total ? Math.round((used / total) * 100) : 0,
    };
  } catch {
    const total = os.totalmem();
    const used = total - os.freemem();
    return {
      totalMb: Math.round(total / 1024 / 1024),
      usedMb: Math.round(used / 1024 / 1024),
      freeMb: Math.round(os.freemem() / 1024 / 1024),
      percent: total ? Math.round((used / total) * 100) : 0,
    };
  }
}

async function disk(target) {
  try {
    const info = await fsp.statfs(target);
    const total = info.blocks * info.bsize;
    const free = info.bavail * info.bsize;
    const used = total - free;
    return {
      totalGb: Number((total / 1024 / 1024 / 1024).toFixed(1)),
      usedGb: Number((used / 1024 / 1024 / 1024).toFixed(1)),
      freeGb: Number((free / 1024 / 1024 / 1024).toFixed(1)),
      percent: total ? Math.round((used / total) * 100) : 0,
    };
  } catch {
    return { totalGb: 0, usedGb: 0, freeGb: 0, percent: 0 };
  }
}

function processMemoryMb(pid) {
  if (!pid) return 0;
  try {
    const status = fs.readFileSync(path.join('/proc', String(pid), 'status'), 'utf8');
    const match = status.match(/^VmRSS:\s+(\d+)\s+kB/m);
    return match ? Math.round(Number(match[1]) / 1024) : 0;
  } catch {
    return 0;
  }
}

async function snapshot(target, pid) {
  const memoryInfo = memory();
  const diskInfo = await disk(target);
  return {
    cpu: cpuPercent(),
    ram_used: memoryInfo.usedMb,
    ram_total: memoryInfo.totalMb,
    ram_free: memoryInfo.freeMb,
    ram_percent: memoryInfo.percent,
    disk_used: diskInfo.usedGb,
    disk_total: diskInfo.totalGb,
    disk_free: diskInfo.freeGb,
    disk_percent: diskInfo.percent,
    proc_mem_mb: processMemoryMb(pid),
    platform: `${os.platform()} ${os.arch()} · Node ${process.version}`,
    cores: os.cpus().length,
  };
}

module.exports = { snapshot };
