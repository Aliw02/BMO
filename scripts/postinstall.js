#!/usr/bin/env node
/**
 * BMO postinstall — self-contained setup
 * Runs automatically after: npm install -g @aliwey/bmo
 *
 * Steps:
 *   1. Download & extract embedded Python 3.13 (minimal, to ~/.bmo/python/)
 *   2. pip install -r requirements.txt into embedded Python
 *   3. Ensure opencode-ai is installed
 *   4. Download cloudflared binary to ~/.bmo/bin/
 *   5. npm install webchat dependencies
 */

'use strict';

const { execSync, spawnSync } = require('child_process');
const https = require('https');
const fs = require('fs');
const path = require('path');
const os = require('os');
const { createWriteStream } = require('fs');

const BMO_HOME = process.env.BMO_HOME || path.join(os.homedir(), '.bmo');
const BMO_BIN  = path.join(BMO_HOME, 'bin');
const BMO_PY   = path.join(BMO_HOME, 'python');
const PKG_DIR  = path.join(__dirname, '..');

// ── Python version ───────────────────────────────────────────────────────────
const PY_VERSION = '3.13.3';
const PY_URLS = {
  win32: {
    x64:  `https://www.python.org/ftp/python/${PY_VERSION}/python-${PY_VERSION}-embed-amd64.zip`,
    arm64:`https://www.python.org/ftp/python/${PY_VERSION}/python-${PY_VERSION}-embed-arm64.zip`,
  },
  darwin: {
    any: `https://github.com/indygreg/python-build-standalone/releases/download/20250311/cpython-${PY_VERSION}+20250311-aarch64-apple-darwin-install_only_stripped.tar.gz`,
    x64: `https://github.com/indygreg/python-build-standalone/releases/download/20250311/cpython-${PY_VERSION}+20250311-x86_64-apple-darwin-install_only_stripped.tar.gz`,
  },
  linux: {
    x64:  `https://github.com/indygreg/python-build-standalone/releases/download/20250311/cpython-${PY_VERSION}+20250311-x86_64-unknown-linux-gnu-install_only_stripped.tar.gz`,
    arm64:`https://github.com/indygreg/python-build-standalone/releases/download/20250311/cpython-${PY_VERSION}+20250311-aarch64-unknown-linux-gnu-install_only_stripped.tar.gz`,
  }
};

// ── Cloudflared version ──────────────────────────────────────────────────────
const CF_VERSION = '2025.4.2';
const CF_URLS = {
  win32:  `https://github.com/cloudflare/cloudflared/releases/download/${CF_VERSION}/cloudflared-windows-amd64.exe`,
  darwin: `https://github.com/cloudflare/cloudflared/releases/download/${CF_VERSION}/cloudflared-darwin-amd64`,
  linux:  `https://github.com/cloudflare/cloudflared/releases/download/${CF_VERSION}/cloudflared-linux-amd64`,
};

// ── Helpers ──────────────────────────────────────────────────────────────────

const step = (n, msg) => console.log(`\n[${n}/5] ${msg}`);
const ok   = msg => console.log(`  [OK] ${msg}`);
const warn = msg => console.log(`  [Warning] ${msg}`);
const err  = msg => console.error(`  [Error] ${msg}`);

function mkdirp(p) { fs.mkdirSync(p, { recursive: true }); }

function download(url, dest) {
  return new Promise((resolve, reject) => {
    const file = createWriteStream(dest);
    const get = (u) => https.get(u, res => {
      if (res.statusCode === 301 || res.statusCode === 302) return get(res.headers.location);
      if (res.statusCode !== 200) return reject(new Error(`HTTP ${res.statusCode} from ${u}`));
      res.pipe(file);
      file.on('finish', () => file.close(resolve));
    }).on('error', reject);
    get(url);
  });
}

function run(cmd, opts = {}) {
  const r = spawnSync(cmd, { shell: true, stdio: 'inherit', ...opts });
  if (r.status !== 0) throw new Error(`Command failed: ${cmd}`);
}

function hasPython() {
  for (const py of ['python3', 'python']) {
    try {
      const r = spawnSync(py, ['--version'], { encoding: 'utf8', stdio: 'pipe' });
      if (r.status === 0) {
        const ver = (r.stdout || r.stderr || '').match(/(\d+)\.(\d+)/);
        if (ver && (parseInt(ver[1]) > 3 || (parseInt(ver[1]) === 3 && parseInt(ver[2]) >= 11))) {
          return py;
        }
      }
    } catch {}
  }
  return null;
}

// ── Step 1: Python ───────────────────────────────────────────────────────────

async function installPython() {
  step(1, `Setting up Python ${PY_VERSION} (embedded, minimal)...`);

  // Check if already installed in BMO_HOME
  const embeddedExe = process.platform === 'win32'
    ? path.join(BMO_PY, 'python.exe')
    : path.join(BMO_PY, 'bin', 'python3');

  if (fs.existsSync(embeddedExe)) {
    ok(`Embedded Python already at ${BMO_PY}`);
    return embeddedExe;
  }

  // Check system Python (3.11+)
  const sysPy = hasPython();
  if (sysPy) {
    ok(`Using system Python: ${sysPy}`);
    return sysPy;
  }

  // Download embedded Python
  mkdirp(BMO_PY);
  const plat = process.platform;
  const arch = process.arch;
  let url;

  if (plat === 'win32') {
    url = arch === 'arm64' ? PY_URLS.win32.arm64 : PY_URLS.win32.x64;
  } else if (plat === 'darwin') {
    url = arch === 'arm64' ? PY_URLS.darwin.any : PY_URLS.darwin.x64;
  } else {
    url = arch === 'arm64' ? PY_URLS.linux.arm64 : PY_URLS.linux.x64;
  }

  console.log(`  Downloading Python from: ${url}`);
  const archiveName = url.split('/').pop();
  const archivePath = path.join(BMO_HOME, archiveName);
  await download(url, archivePath);

  // Extract
  if (archiveName.endsWith('.zip')) {
    run(`powershell -Command "Expand-Archive -Path '${archivePath}' -DestinationPath '${BMO_PY}' -Force"`);
  } else {
    run(`tar -xzf "${archivePath}" -C "${BMO_PY}" --strip-components=1`);
  }
  fs.unlinkSync(archivePath);

  ok(`Python ${PY_VERSION} installed at ${BMO_PY}`);
  return embeddedExe;
}

// ── Step 2: pip install ───────────────────────────────────────────────────────

function installPipDeps(pythonExe) {
  step(2, 'Installing Python dependencies (pip)...');
  const reqFile = path.join(PKG_DIR, 'requirements.txt');
  try {
    // Ensure pip is available
    run(`"${pythonExe}" -m ensurepip --upgrade`);
    run(`"${pythonExe}" -m pip install --upgrade pip --quiet`);
    run(`"${pythonExe}" -m pip install -r "${reqFile}" --quiet`);
    ok('Python dependencies installed');
  } catch (e) {
    warn(`pip install failed: ${e.message}`);
    warn('You may need to run: pip install -r requirements.txt manually');
  }
}

// ── Step 3: opencode-ai ──────────────────────────────────────────────────────

function ensureOpenCode() {
  step(3, 'Checking opencode-ai...');
  try {
    execSync('opencode --version', { stdio: 'ignore' });
    ok('opencode-ai already installed');
  } catch {
    console.log('  Installing opencode-ai...');
    try {
      run('npm install -g opencode-ai');
      ok('opencode-ai installed');
    } catch {
      warn('Could not install opencode-ai automatically.');
      warn('Run manually: npm install -g opencode-ai');
    }
  }
}

// ── Step 4: cloudflared ──────────────────────────────────────────────────────

async function installCloudflared() {
  step(4, 'Setting up cloudflared...');
  mkdirp(BMO_BIN);

  const plat = process.platform;
  const cfExe = plat === 'win32'
    ? path.join(BMO_BIN, 'cloudflared.exe')
    : path.join(BMO_BIN, 'cloudflared');

  if (fs.existsSync(cfExe)) {
    ok('cloudflared already at ' + cfExe);
    return;
  }

  // Check system cloudflared
  try {
    execSync('cloudflared --version', { stdio: 'ignore' });
    ok('Using system cloudflared');
    return;
  } catch {}

  const url = CF_URLS[plat] || CF_URLS.linux;
  console.log(`  Downloading cloudflared from: ${url}`);
  try {
    await download(url, cfExe);
    if (plat !== 'win32') fs.chmodSync(cfExe, 0o755);
    ok('cloudflared installed at ' + cfExe);
  } catch (e) {
    warn(`cloudflared download failed: ${e.message}`);
    warn('Install manually: https://developers.cloudflare.com/cloudflared/downloads/');
  }
}

// ── Step 5: webchat node_modules ─────────────────────────────────────────────

function installWebchatDeps() {
  step(5, 'Installing webchat dependencies...');
  const webchatDir = path.join(PKG_DIR, 'webchat');
  if (!fs.existsSync(path.join(webchatDir, 'package.json'))) {
    warn('webchat/package.json not found, skipping');
    return;
  }
  try {
    run(`npm install --prefix "${webchatDir}" --quiet`);
    ok('webchat dependencies installed');
  } catch (e) {
    warn(`webchat npm install failed: ${e.message}`);
  }
}

// ── Main ─────────────────────────────────────────────────────────────────────

(async () => {
  console.log('\n╭──────────────────────────────────────────╮');
  console.log('│  BMO Installer — @aliwey/bmo              │');
  console.log('╰──────────────────────────────────────────╯\n');

  mkdirp(BMO_HOME);
  mkdirp(BMO_BIN);

  try {
    const pythonExe = await installPython();
    installPipDeps(pythonExe);
    ensureOpenCode();
    await installCloudflared();
    installWebchatDeps();

    console.log('\n╭───────────────────────────────────────────────────╮');
    console.log('│  [OK] BMO installed successfully!                 │');
    console.log('│                                                   │');
    console.log('│  IMPORTANT: You must run the setup wizard first:  │');
    console.log('│    bmo --init                                     │');
    console.log('│                                                   │');
    console.log('│  Then start BMO:                                  │');
    console.log('│    bmo                                            │');
    console.log('╰───────────────────────────────────────────────────╯\n');
  } catch (e) {
    err(`Installation failed: ${e.message}`);
    console.log('\nRun `bmo --init` to retry configuration, or check the docs.');
    process.exit(1);
  }
})();
