#!/usr/bin/env node
/**
 * bmo relay [--private] [--stop]
 *
 * --private  Start relay + cloudflared but don't register with BFP Registry
 * --stop     Stop relay and deregister from BFP Registry
 */

'use strict';

const { spawn, execSync, spawnSync } = require('child_process');
const net   = require('net');
const https = require('https');
const http  = require('http');
const fs    = require('fs');
const path  = require('path');
const os    = require('os');

const BMO_HOME   = process.env.BMO_HOME || path.join(os.homedir(), '.bmo');
const BMO_BIN    = path.join(BMO_HOME, 'bin');
const PKG_DIR    = path.join(__dirname, '..');
const TASKS_FILE = path.join(BMO_HOME, 'data', 'background_tasks.json');
const RELAY_PORT = parseInt(process.env.BFP_RELAY_PORT || '9753');
const REGISTRY_URL = process.env.BFP_REGISTRY_URL || 'https://bfp-registry.aliwey.workers.dev';

const isPrivate  = process.argv.includes('--private');
const isStop     = process.argv.includes('--stop');

function sleep(ms) { return new Promise(r => setTimeout(r, ms)); }

function loadTasks() {
  try { return JSON.parse(fs.readFileSync(TASKS_FILE, 'utf8')); } catch { return {}; }
}

function saveTasks(tasks) {
  fs.mkdirSync(path.dirname(TASKS_FILE), { recursive: true });
  fs.writeFileSync(TASKS_FILE, JSON.stringify(tasks, null, 2));
}

async function isPortOpen(port) {
  return new Promise(resolve => {
    const s = net.createConnection({ port, host: '127.0.0.1' });
    s.setTimeout(500);
    s.on('connect', () => { s.destroy(); resolve(true); });
    s.on('timeout', () => { s.destroy(); resolve(false); });
    s.on('error', () => resolve(false));
  });
}

function getCloudflaredExe() {
  const embedded = process.platform === 'win32'
    ? path.join(BMO_BIN, 'cloudflared.exe')
    : path.join(BMO_BIN, 'cloudflared');
  if (fs.existsSync(embedded)) return embedded;
  try { execSync('cloudflared --version', { stdio: 'ignore' }); return 'cloudflared'; } catch {}
  console.error('❌ cloudflared not found. Run: bmo init');
  process.exit(1);
}

function getPython() {
  const embeddedWin  = path.join(BMO_HOME, 'python', 'python.exe');
  const embeddedUnix = path.join(BMO_HOME, 'python', 'bin', 'python3');
  if (fs.existsSync(embeddedWin))  return embeddedWin;
  if (fs.existsSync(embeddedUnix)) return embeddedUnix;
  for (const cmd of ['python3', 'python']) {
    try { execSync(`${cmd} --version`, { stdio: 'ignore' }); return cmd; } catch {}
  }
  return 'python3';
}

/** Read DID from bfp identity file */
function getDID() {
  const idFile = path.join(BMO_HOME, 'data', 'bfp_identity.json');
  if (!fs.existsSync(idFile)) {
    // Try project-local data dir (dev mode)
    const devFile = path.join(PKG_DIR, 'data', 'bfp_identity.json');
    if (fs.existsSync(devFile)) return JSON.parse(fs.readFileSync(devFile)).did;
    return null;
  }
  return JSON.parse(fs.readFileSync(idFile)).did;
}

/** POST to BFP Registry */
function registryPost(endpoint, body) {
  return new Promise((resolve, reject) => {
    const url = new URL(REGISTRY_URL + endpoint);
    const data = JSON.stringify(body);
    const opts = {
      hostname: url.hostname,
      path: url.pathname + url.search,
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'Content-Length': Buffer.byteLength(data) },
    };
    const lib = url.protocol === 'https:' ? https : http;
    const req = lib.request(opts, res => {
      let body = '';
      res.on('data', d => body += d);
      res.on('end', () => resolve({ status: res.statusCode, body }));
    });
    req.on('error', reject);
    req.write(data);
    req.end();
  });
}

/** Parse cloudflared output for ws:// or wss:// tunnel URL */
function waitForTunnelUrl(proc) {
  return new Promise((resolve, reject) => {
    const timeout = setTimeout(() => reject(new Error('Tunnel URL timeout')), 30000);
    const handler = data => {
      const text = data.toString();
      // cloudflared prints the https:// URL; BFP needs ws:// equivalent
      const m = text.match(/https?:\/\/[a-zA-Z0-9\-]+\.trycloudflare\.com/);
      if (m) {
        clearTimeout(timeout);
        const wsUrl = m[0].replace('https://', 'wss://').replace('http://', 'ws://');
        resolve(wsUrl);
      }
    };
    proc.stdout && proc.stdout.on('data', handler);
    proc.stderr && proc.stderr.on('data', handler);
  });
}

// ── Stop handler ─────────────────────────────────────────────────────────────

async function stopRelay() {
  console.log('🛑 Stopping BFP relay...');
  const tasks = loadTasks();

  // Kill processes
  for (const key of ['bfp_relay_pid', 'bfp_cf_pid']) {
    const pid = tasks[key];
    if (pid) {
      try { process.kill(pid, 'SIGTERM'); } catch {}
    }
  }

  // Deregister from registry
  const did = tasks.bfp_did || getDID();
  if (did && !isPrivate) {
    try {
      await registryPost('/unregister', { did });
      console.log('✓ Deregistered from BFP Registry');
    } catch (e) {
      console.warn('⚠️  Could not deregister:', e.message);
    }
  }

  delete tasks.bfp_relay_pid;
  delete tasks.bfp_cf_pid;
  delete tasks.bfp_tunnel_url;
  delete tasks.bfp_did;
  saveTasks(tasks);

  console.log('✅ BFP relay stopped\n');
  process.exit(0);
}

// ── Main ─────────────────────────────────────────────────────────────────────

(async () => {
  if (isStop) { await stopRelay(); return; }

  // ── Start BFP relay (Python) ────────────────────────────────────────────────
  if (await isPortOpen(RELAY_PORT)) {
    const tasks = loadTasks();
    if (tasks.bfp_tunnel_url) {
      console.log(`\n🌐 BFP relay already running!`);
      console.log(`   DID:    ${tasks.bfp_did || 'unknown'}`);
      console.log(`   Relay:  ${tasks.bfp_tunnel_url}\n`);
      process.exit(0);
    }
  }

  console.log('⏳ Starting BFP relay server...');
  const python = getPython();
  const relay = spawn(python, ['-m', 'tools.bfp_relay', '--port', String(RELAY_PORT)], {
    cwd: PKG_DIR,
    stdio: ['ignore', 'pipe', 'pipe'],
    detached: true,
    env: { ...process.env, BMO_HOME, PYTHONUNBUFFERED: '1' },
  });
  relay.unref();

  // Wait for relay to bind
  for (let i = 0; i < 20; i++) {
    await sleep(500);
    if (await isPortOpen(RELAY_PORT)) break;
    if (i === 19) { console.error('❌ BFP relay failed to start'); process.exit(1); }
  }
  console.log(`✓ BFP relay running on port ${RELAY_PORT}`);

  // ── Start cloudflared tunnel ────────────────────────────────────────────────
  console.log('⏳ Starting cloudflared tunnel...');
  const cfExe = getCloudflaredExe();
  const cf = spawn(cfExe, ['tunnel', '--url', `ws://localhost:${RELAY_PORT}`], {
    stdio: ['ignore', 'pipe', 'pipe'],
    detached: true,
  });
  cf.unref();

  let tunnelUrl;
  try {
    tunnelUrl = await waitForTunnelUrl(cf);
  } catch {
    console.error('❌ Could not get tunnel URL from cloudflared');
    relay.kill();
    process.exit(1);
  }
  console.log(`✓ Tunnel active: ${tunnelUrl}`);

  // ── Get DID ─────────────────────────────────────────────────────────────────
  // Run the bfp_identity module to get (or create) the DID
  const didResult = spawnSync(python, ['-c',
    `import sys; sys.path.insert(0, '${PKG_DIR.replace(/\\/g, '\\\\')}'); from core.bfp_identity import get_did; print(get_did())`
  ], { encoding: 'utf8', env: { ...process.env, BMO_HOME } });
  const did = (didResult.stdout || '').trim() || getDID();

  // ── Register with BFP Registry ───────────────────────────────────────────────
  const caps = ['code', 'research', 'files', 'web', 'terminal', 'memory'];
  if (!isPrivate && did) {
    try {
      await registryPost('/register', { did, endpoint: tunnelUrl, caps });
      console.log(`✓ Registered with BFP Registry (${REGISTRY_URL})`);
    } catch (e) {
      console.warn(`⚠️  Registry registration failed: ${e.message}`);
    }
  } else if (isPrivate) {
    console.log('ℹ️  Private mode — skipping registry registration');
  }

  // Save to task registry
  const tasks = loadTasks();
  tasks.bfp_relay_pid = relay.pid;
  tasks.bfp_cf_pid    = cf.pid;
  tasks.bfp_tunnel_url = tunnelUrl;
  tasks.bfp_did       = did;
  saveTasks(tasks);

  // ── Periodic re-registration (every 30 min) ──────────────────────────────────
  let refreshTimer;
  if (!isPrivate && did) {
    refreshTimer = setInterval(async () => {
      try {
        await registryPost('/register', { did, endpoint: tunnelUrl, caps });
      } catch {}
    }, 30 * 60 * 1000);
  }

  console.log('\n╭───────────────────────────────────────────────────────╮');
  console.log('│  🌐 BMO BFP Relay is ONLINE                           │');
  console.log(`│  DID:    ${(did || 'unknown').substring(0, 43).padEnd(43)} │`);
  console.log(`│  Relay:  ${tunnelUrl.padEnd(43)} │`);
  console.log(`│  Mode:   ${(isPrivate ? 'Private (not in registry)' : 'Public (discoverable)').padEnd(43)} │`);
  console.log('│                                                       │');
  console.log('│  Press Ctrl+C to stop and go offline                  │');
  console.log('╰───────────────────────────────────────────────────────╯\n');

  // Keep process alive, handle graceful shutdown
  process.stdin.resume();
  process.on('SIGINT', async () => {
    if (refreshTimer) clearInterval(refreshTimer);
    console.log('\n🛑 Shutting down BFP relay...');
    if (!isPrivate && did) {
      try { await registryPost('/unregister', { did }); console.log('✓ Deregistered'); } catch {}
    }
    try { relay.kill(); } catch {}
    try { cf.kill(); } catch {}
    const t = loadTasks();
    delete t.bfp_relay_pid; delete t.bfp_cf_pid;
    delete t.bfp_tunnel_url; delete t.bfp_did;
    saveTasks(t);
    process.exit(0);
  });
})();
