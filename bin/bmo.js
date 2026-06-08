#!/usr/bin/env node
/**
 * BMO CLI entry point — @aliwey/bmo
 * Routes subcommands and manages OpenCode lifecycle.
 */

'use strict';

const { spawn, execSync } = require('child_process');
const net = require('net');
const path = require('path');
const fs = require('fs');
const os = require('os');

const PKG_DIR = path.join(__dirname, '..');
const BMO_HOME = process.env.BMO_HOME || path.join(os.homedir(), '.bmo');
const VERSION = require('../package.json').version;

// Load config from ~/.bmo/.env into process.env if it exists
const envPath = path.join(BMO_HOME, '.env');
if (fs.existsSync(envPath)) {
  try {
    const content = fs.readFileSync(envPath, 'utf8');
    const lines = content.split(/\r?\n/);
    for (const line of lines) {
      const trimmed = line.trim();
      if (!trimmed || trimmed.startsWith('#')) continue;
      const match = trimmed.match(/^([^=]+)=(.*)$/);
      if (match) {
        const key = match[1].trim();
        let val = match[2].trim();
        if ((val.startsWith('"') && val.endsWith('"')) || (val.startsWith("'") && val.endsWith("'"))) {
          val = val.slice(1, -1);
        }
        if (!process.env[key]) {
          process.env[key] = val;
        }
      }
    }
  } catch (e) {
    console.error(`Warning: Failed to load .env from ${envPath}: ${e.message}`);
  }
}

// ── Helpers ─────────────────────────────────────────────────────────────────

function sleep(ms) {
  return new Promise(resolve => setTimeout(resolve, ms));
}

async function isPortOpen(port, host = '127.0.0.1') {
  return new Promise(resolve => {
    const s = net.createConnection({ port, host });
    s.setTimeout(500);
    s.on('connect', () => { s.destroy(); resolve(true); });
    s.on('timeout', () => { s.destroy(); resolve(false); });
    s.on('error', () => resolve(false));
  });
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

/** Resolve the embedded or system Python executable */
function getPython() {
  const embeddedWin = path.join(BMO_HOME, 'python', 'python.exe');
  const embeddedUnix = path.join(BMO_HOME, 'python', 'bin', 'python3');
  if (fs.existsSync(embeddedWin)) return embeddedWin;
  if (fs.existsSync(embeddedUnix)) return embeddedUnix;
  // Fall back to system Python
  for (const cmd of ['python3', 'python']) {
    try { execSync(`${cmd} --version`, { stdio: 'ignore' }); return cmd; } catch {}
  }
  console.error('❌ Python not found. Run: bmo init');
  process.exit(1);
}

/** Start OpenCode if not already running. Returns the child if we own it. */
async function ensureOpenCode() {
  const port = parseInt(process.env.OPENCODE_PORT || '4800');
  if (await isPortOpen(port)) {
    process.stdout.write(`✓ OpenCode already running on port ${port}\n`);
    return null; // don't own it
  }

  // Check opencode is installed
  try { execSync('opencode --version', { stdio: 'ignore' }); }
  catch {
    console.error('❌ opencode not found. Installing...');
    try { execSync('npm install -g opencode-ai', { stdio: 'inherit' }); }
    catch { console.error('❌ Could not install opencode-ai. Please run: npm install -g opencode-ai'); process.exit(1); }
  }

  process.stdout.write(`⏳ Starting OpenCode server on port ${port}...\n`);
  // Use BMO_HOME as cwd so the process survives npm uninstall (PKG_DIR gets deleted)
  const child = spawn('opencode', ['serve', '--port', String(port)], {
    detached: false,
    stdio: 'ignore',
    cwd: BMO_HOME,
    shell: true,
  });

  child.on('error', err => {
    console.error(`❌ Failed to start OpenCode: ${err.message}`);
    process.exit(1);
  });

  // Wait up to 15s for port to open
  for (let i = 0; i < 30; i++) {
    await sleep(500);
    if (await isPortOpen(port)) {
      process.stdout.write(`✓ OpenCode server ready\n`);
      return child;
    }
  }
  console.error('❌ OpenCode server did not start in time.');
  child.kill();
  process.exit(1);
}

// ── Command routing ──────────────────────────────────────────────────────────

const [,, cmd, ...args] = process.argv;

(async () => {
  // ── bmo --version ─────────────────────────────────────────────────────
  if (cmd === '--version' || cmd === '-v') {
    console.log(`@aliwey/bmo v${VERSION}`);
    process.exit(0);
  }

  // ── bmo --help ───────────────────────────────────────────────────────
  if (cmd === '--help' || cmd === '-h' || cmd === 'help') {
    const PKG_JSON = require('../package.json');
    const BMO_HOME_DISPLAY = process.env.BMO_HOME || path.join(os.homedir(), '.bmo');
    console.log(`
@aliwey/bmo v${PKG_JSON.version} — AI coding assistant with Telegram, CLI & Web sync

Usage:
  bmo                  Start the BMO CLI (auto-starts OpenCode if needed)
  bmo init             Run the setup wizard (creates ~/.bmo/.env)
  bmo web              Start webchat + Cloudflare tunnel, print public URL
  bmo relay            Start BFP relay + register with discovery registry
  bmo relay --stop     Stop relay + deregister
  bmo relay --private  Start relay without registering (private mode)
  bmo --version        Show version
  bmo --update         Update to latest version
  bmo --update <ver>   Update to a specific version
  bmo --help           Show this help

Data lives in: ${BMO_HOME_DISPLAY}
`);
    process.exit(0);
  }

  // ── bmo --update [version] ────────────────────────────────────────────────
  if (cmd === '--update' || cmd === '-update' || cmd === 'update') {
    let targetVer = args[0];
    if (!targetVer) {
      console.log('Checking for updates...');
      try {
        const latest = execSync('npm show @aliwey/bmo version', { encoding: 'utf8' }).trim();
        if (latest === VERSION) {
          console.log(`[Info] BMO is already up to date (version ${VERSION}).`);
          process.exit(0);
        }
        targetVer = latest;
      } catch (e) {
        targetVer = 'latest';
      }
    }

    const displayVer = targetVer.replace(/^@/, '');
    console.log(`  Current: v${VERSION}`);
    console.log(`  Latest:  v${displayVer}`);
    console.log('');

    const ver = targetVer.startsWith('@') ? targetVer : `@${targetVer}`;

    console.log(`Updating @aliwey/bmo from v${VERSION} to v${displayVer}...`);

    // Kill sibling BMO processes (exclude current PID to avoid self-immolation)
    const myPid = process.pid;
    if (os.platform() === 'win32') {
      try {
        execSync(`powershell -Command "Get-CimInstance Win32_Process | Where-Object { ` +
          `($_.ProcessId -ne ${myPid}) -and ` +
          `(($_.Name -eq 'python.exe' -or $_.Name -eq 'node.exe' -or $_.Name -eq 'cloudflared.exe') -and ` +
          `($_.CommandLine -like '*main.py*' -or $_.CommandLine -like '*cli.py*' -or $_.CommandLine -like '*bmo*' -or ` +
          `$_.CommandLine -like '*cloudflared*' -or $_.CommandLine -like '*server.js*' -or ` +
          `$_.CommandLine -like '*opencode*')) } | ` +
          `ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }"`, { stdio: 'ignore' });
      } catch (e) {}
      // Free ports
      try { execSync(`for /f "tokens=5" %p in ('netstat -ano ^| findstr :3456 ^| findstr LISTENING') do @taskkill /F /PID %p 2>nul`, { stdio: 'ignore' }); } catch (e) {}
      try { execSync(`for /f "tokens=5" %p in ('netstat -ano ^| findstr :4097 ^| findstr LISTENING') do @taskkill /F /PID %p 2>nul`, { stdio: 'ignore' }); } catch (e) {}
      try { execSync(`for /f "tokens=5" %p in ('netstat -ano ^| findstr :4800 ^| findstr LISTENING') do @taskkill /F /PID %p 2>nul`, { stdio: 'ignore' }); } catch (e) {}
    } else {
      try { execSync(`pkill -f "opencode serve" || true`, { stdio: 'ignore' }); } catch (e) {}
      try { execSync(`pkill -f "main.py|cli.py|cloudflared|server.js" || true`, { stdio: 'ignore' }); } catch (e) {}
    }

    try {
      execSync(`npm install -g @aliwey/bmo${ver}`, { stdio: 'inherit' });
      console.log(`[OK] BMO updated from v${VERSION} to v${displayVer}!`);
    } catch {
      console.log('npm install failed — retrying once after 5s...');
      // Countdown inline
      for (let i = 5; i >= 0; i--) {
        console.log(`  ${i}...`);
        await sleep(1000);
      }
      try {
        execSync(`npm install -g @aliwey/bmo${ver}`, { stdio: 'inherit' });
        console.log(`[OK] BMO updated from v${VERSION} to v${displayVer}!`);
      } catch {
        console.error('Update failed. Try: npm install -g @aliwey/bmo');
        process.exit(1);
      }
    }
    process.exit(0);
  }

  // ── bmo init ──────────────────────────────────────────────────────────────
  if (cmd === 'init' || cmd === '-init' || cmd === '--init') {
    require('../scripts/bmo_init.js');
    return;
  }

  // ── bmo web ───────────────────────────────────────────────────────────────
  if (cmd === 'web' || cmd === '-web' || cmd === '--web') {
    require('../scripts/web_cmd.js');
    return;
  }

  // ── bmo relay [--private|--stop] ──────────────────────────────────────────
  if (cmd === 'relay' || cmd === '-relay' || cmd === '--relay') {
    process.argv = [process.argv[0], process.argv[1], ...args]; // pass flags
    require('../scripts/relay_cmd.js');
    return;
  }

  // ── bmo (default: CLI) ────────────────────────────────────────────────────
  const openCodeChild = await ensureOpenCode();

  // Clean up owned OpenCode on exit
  if (openCodeChild) {
    const cleanup = () => { try { openCodeChild.kill(); } catch {} };
    process.on('exit', cleanup);
    process.on('SIGINT', () => { cleanup(); process.exit(0); });
    process.on('SIGTERM', () => { cleanup(); process.exit(0); });
  }

  // Copy generic memory.md template if it does not exist
  const packageMemoryPath = path.join(PKG_DIR, 'memory.md');
  const userMemoryPath = path.join(BMO_HOME, 'data', 'memory.md');
  if (fs.existsSync(packageMemoryPath) && !fs.existsSync(userMemoryPath)) {
    try {
      fs.mkdirSync(path.dirname(userMemoryPath), { recursive: true });
      fs.copyFileSync(packageMemoryPath, userMemoryPath);
    } catch (e) {
      console.error(`Warning: Failed to copy memory.md template: ${e.message}`);
    }
  }

  // Launch BMO Python CLI — exactly like running `python cli.py`
  const python = getPython();
  const cliScript = path.join(PKG_DIR, 'cli.py');

  const bmo = spawn(python, [cliScript], {
    stdio: 'inherit',
    cwd: PKG_DIR,
    env: {
      ...process.env,
      BMO_HOME,
      PYTHONUNBUFFERED: '1',
    },
  });

  bmo.on('error', err => {
    console.error(`❌ Failed to start BMO: ${err.message}`);
    process.exit(1);
  });

  bmo.on('exit', code => process.exit(code ?? 0));
})();
