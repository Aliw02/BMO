#!/usr/bin/env node
/**
 * bmo web — Start webchat + cloudflared tunnel, return public URL.
 * If already running, return existing URL from task registry.
 */

'use strict';

const { spawn, execSync } = require('child_process');
const net  = require('net');
const fs   = require('fs');
const path = require('path');
const os   = require('os');
const https = require('https');

const BMO_HOME  = process.env.BMO_HOME || path.join(os.homedir(), '.bmo');
const BMO_BIN   = path.join(BMO_HOME, 'bin');
const PKG_DIR   = path.join(__dirname, '..');
const TASKS_FILE = path.join(BMO_HOME, 'data', 'background_tasks.json');
const WEBCHAT_PORT = parseInt(process.env.PORT || '3456');

function sleep(ms) { return new Promise(r => setTimeout(r, ms)); }

async function isPortOpen(port) {
  return new Promise(resolve => {
    const s = net.createConnection({ port, host: '127.0.0.1' });
    s.setTimeout(500);
    s.on('connect', () => { s.destroy(); resolve(true); });
    s.on('timeout', () => { s.destroy(); resolve(false); });
    s.on('error', () => resolve(false));
  });
}

const API_PORT = parseInt(process.env.BMO_API_PORT || '4098');
const API_URL = `http://127.0.0.1:${API_PORT}`;

function findPython() {
  // Check embedded Python first
  const embedded = process.platform === 'win32'
    ? path.join(BMO_HOME, 'python', 'python.exe')
    : path.join(BMO_HOME, 'python', 'bin', 'python3');
  if (fs.existsSync(embedded)) return embedded;
  // Check system Python
  for (const py of ['python3', 'python']) {
    try { execSync(`${py} --version`, { stdio: 'pipe' }); return py; } catch {}
  }
  return null;
}

function getCloudflaredExe() {
  const embedded = process.platform === 'win32'
    ? path.join(BMO_BIN, 'cloudflared.exe')
    : path.join(BMO_BIN, 'cloudflared');
  if (fs.existsSync(embedded)) return embedded;
  try { execSync('cloudflared --version', { stdio: 'ignore' }); return 'cloudflared'; } catch {}
  console.error('[Error] cloudflared not found. Run: bmo init');
  process.exit(1);
}

function killProcessByPort(port) {
  try {
    if (os.platform() === 'win32') {
      const output = execSync(`netstat -ano`, { encoding: 'utf8' });
      const lines = output.split('\n');
      for (const line of lines) {
        if (line.includes(`:${port}`) && line.includes('LISTENING')) {
          const parts = line.trim().split(/\s+/);
          const pid = parts[parts.length - 1];
          if (pid && pid !== '0') {
            try {
              execSync(`taskkill /F /PID ${pid}`, { stdio: 'ignore' });
            } catch (e) {}
          }
        }
      }
    } else {
      try {
        execSync(`fuser -k ${port}/tcp`, { stdio: 'ignore' });
      } catch {
        try {
          execSync(`kill -9 $(lsof -t -i:${port})`, { stdio: 'ignore' });
        } catch {}
      }
    }
  } catch (e) {}
}

function loadTasks() {
  try { return JSON.parse(fs.readFileSync(TASKS_FILE, 'utf8')); } catch { return {}; }
}

function saveTasks(tasks) {
  fs.mkdirSync(path.dirname(TASKS_FILE), { recursive: true });
  fs.writeFileSync(TASKS_FILE, JSON.stringify(tasks, null, 2));
}

/** Parse cloudflared stdout/stderr for the public URL */
function waitForTunnelUrl(proc) {
  return new Promise((resolve, reject) => {
    const timeout = setTimeout(() => reject(new Error('Tunnel URL timeout')), 30000);
    const handler = data => {
      const text = data.toString();
      const m = text.match(/https?:\/\/[a-zA-Z0-9\-]+\.trycloudflare\.com/);
      if (m) { clearTimeout(timeout); resolve(m[0]); }
    };
    proc.stdout && proc.stdout.on('data', handler);
    proc.stderr && proc.stderr.on('data', handler);
  });
}

(async () => {
  // Check if webchat is already running
  if (await isPortOpen(WEBCHAT_PORT)) {
    const tasks = loadTasks();
    const existing = tasks.webchat_tunnel_url;
    if (existing) {
      console.log(`\n[Info] Webchat already running!`);
      console.log(`   Local:  http://127.0.0.1:${WEBCHAT_PORT}`);
      console.log(`   Public: ${existing}\n`);
      process.exit(0);
    } else {
      console.log(`[Info] Port ${WEBCHAT_PORT} is in use by an unrecognized process. Freeing port...`);
      killProcessByPort(WEBCHAT_PORT);
      await sleep(1000); // Wait for the port to release
    }
  }

  // Ensure webchat dependencies are installed
  const webchatDir = path.join(PKG_DIR, 'webchat');
  const nodeModulesPath = path.join(webchatDir, 'node_modules');
  if (!fs.existsSync(nodeModulesPath) || !fs.existsSync(path.join(nodeModulesPath, 'express'))) {
    console.log('[Info] Webchat dependencies not found. Installing them now...');
    try {
      execSync('npm install --quiet', { cwd: webchatDir, stdio: 'inherit' });
      console.log('[OK] Dependencies installed successfully!');
    } catch (err) {
      console.error('[Error] Failed to install webchat dependencies automatically.');
      console.error('Please try running: npm install inside the BMO webchat folder.');
      process.exit(1);
    }
  }

  // Start Python API server (single source of truth for DB access)
  console.log('[Info] Starting DB API server...');
  const pythonExe = findPython();
  if (!pythonExe) {
    console.error('[Error] Python 3.11+ not found. Run: bmo init');
    process.exit(1);
  }
  const apiServerPath = path.join(PKG_DIR, 'core', 'webchat_api.py');
  const logDir = path.join(BMO_HOME, 'logs');
  fs.mkdirSync(logDir, { recursive: true });
  const apiLogPath = path.join(logDir, 'webchat_api.log');
  const apiLogStream = fs.openSync(apiLogPath, 'a');

  const apiServer = spawn(pythonExe, [apiServerPath, '--port', String(API_PORT)], {
    stdio: ['ignore', apiLogStream, apiLogStream],
    detached: true,
  });
  apiServer.unref();

  // Wait for API server to be ready
  let apiReady = false;
  for (let i = 0; i < 20; i++) {
    await sleep(500);
    if (await isPortOpen(API_PORT)) { apiReady = true; break; }
  }
  if (!apiReady) {
    console.error('[Error] DB API server failed to start on port', API_PORT);
    try {
      const logContent = fs.readFileSync(apiLogPath, 'utf8').trim();
      const lines = logContent.split('\n');
      console.error('\nLast API log output:');
      console.error(lines.slice(-10).join('\n'));
    } catch (_) {}
    process.exit(1);
  }
  console.log('[OK] DB API server running on', API_URL);

  // Start webchat server
  console.log('Starting webchat server...');
  const webchatLogPath = path.join(logDir, 'webchat.log');
  const webchatLogStream = fs.openSync(webchatLogPath, 'a');

  const webchat = spawn('node', ['server.js'], {
    cwd: webchatDir,
    stdio: ['ignore', webchatLogStream, webchatLogStream],
    detached: true,
    env: {
      ...process.env,
      PORT: String(WEBCHAT_PORT),
      BMO_API_URL: API_URL,
      BMO_HOME,
      BMO_DB_PATH: path.join(BMO_HOME, 'data', 'bot.db'),
    }
  });
  webchat.unref();

  // Wait for webchat to be ready
  let started = false;
  for (let i = 0; i < 20; i++) {
    await sleep(500);
    if (await isPortOpen(WEBCHAT_PORT)) {
      started = true;
      break;
    }
  }

  if (!started) {
    console.error('[Error] Webchat failed to start');
    try {
      const logContent = fs.readFileSync(webchatLogPath, 'utf8').trim();
      const lines = logContent.split('\n');
      const lastLines = lines.slice(-15).join('\n');
      console.error('\nLast log output:');
      console.error(lastLines);
    } catch (e) {
      console.error('Could not read webchat log file.');
    }
    process.exit(1);
  }
  console.log('[OK] Webchat server running on port', WEBCHAT_PORT);

  // Start cloudflared tunnel
  console.log('Starting cloudflared tunnel...');
  const cfExe = getCloudflaredExe();
  const cf = spawn(cfExe, ['tunnel', '--url', `http://localhost:${WEBCHAT_PORT}`], {
    stdio: ['ignore', 'pipe', 'pipe'],
    detached: true,
  });
  cf.unref();

  let tunnelUrl;
  try {
    tunnelUrl = await waitForTunnelUrl(cf);
  } catch {
    console.error('[Error] Could not get tunnel URL from cloudflared');
    process.exit(1);
  }

  // Save to task registry
  const tasks = loadTasks();
  tasks.webchat_pid = webchat.pid;
  tasks.webchat_api_pid = apiServer.pid;
  tasks.webchat_cf_pid = cf.pid;
  tasks.webchat_tunnel_url = tunnelUrl;
  tasks.webchat_local_url = `http://127.0.0.1:${WEBCHAT_PORT}`;
  saveTasks(tasks);

  console.log('\n╭────────────────────────────────────────────╮');
  console.log('│  [OK] BMO Webchat is live!                 │');
  console.log(`│  Local:  http://127.0.0.1:${WEBCHAT_PORT}             │`);
  console.log(`│  Public: ${tunnelUrl.padEnd(34)} │`);
  console.log('╰────────────────────────────────────────────╯\n');

  process.exit(0);
})();
