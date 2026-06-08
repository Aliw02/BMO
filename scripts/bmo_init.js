#!/usr/bin/env node
/**
 * BMO Setup Wizard — bmo init
 * Generates ~/.bmo/.env interactively on first run.
 */

'use strict';

const readline = require('readline');
const fs = require('fs');
const path = require('path');
const os = require('os');

const BMO_HOME = process.env.BMO_HOME || path.join(os.homedir(), '.bmo');
const ENV_PATH = path.join(BMO_HOME, '.env');
const PKG_DIR = path.join(__dirname, '..');

function mkdirp(p) { fs.mkdirSync(p, { recursive: true }); }

function ask(rl, question, defaultValue) {
  return new Promise(resolve => {
    const hint = defaultValue ? ` [${defaultValue}]` : '';
    rl.question(`  ${question}${hint}: `, answer => {
      resolve(answer.trim() || defaultValue || '');
    });
  });
}

(async () => {
  console.log('\n╭───────────────────────────────────────────╮');
  console.log('│            BMO Setup Wizard                       │');
  console.log('╰───────────────────────────────────────────╯\n');

  // Load existing .env values as defaults
  let existing = {};
  if (fs.existsSync(ENV_PATH)) {
    const lines = fs.readFileSync(ENV_PATH, 'utf8').split('\n');
    for (const line of lines) {
      const m = line.match(/^([^#=]+)=(.*)$/);
      if (m) existing[m[1].trim()] = m[2].trim();
    }
    console.log(`  [Info] Existing config found at ${ENV_PATH} — press Enter to keep values.\n`);
  }

  // Load existing memory.md values if available
  let existing_name = '';
  let existing_role = '';
  let existing_preference = '';
  const userMemoryPath = path.join(BMO_HOME, 'data', 'memory.md');
  if (fs.existsSync(userMemoryPath)) {
    try {
      const memoryContent = fs.readFileSync(userMemoryPath, 'utf8');
      const nameMatch = memoryContent.match(/-\s*Name:\s*(.*)/i);
      const roleMatch = memoryContent.match(/-\s*Role:\s*(.*)/i);
      const prefMatch = memoryContent.match(/-\s*Preference:\s*(.*)/i);
      if (nameMatch && nameMatch[1].trim() !== '[Enter Name]') existing_name = nameMatch[1].trim();
      if (roleMatch && roleMatch[1].trim() !== '[Enter Role]') existing_role = roleMatch[1].trim();
      if (prefMatch && prefMatch[1].trim() !== '[Enter Preference]') existing_preference = prefMatch[1].trim();
    } catch (e) {}
  }

  const rl = readline.createInterface({ input: process.stdin, output: process.stdout });

  // ── Telegram ────────────────────────────────────────────────────────────────
  console.log('── Telegram ────────────────────────────────────────');
  console.log('  Get your token from @BotFather on Telegram.');
  const token = await ask(rl, 'Bot Token', existing.TELEGRAM_TOKEN || existing.TELEGRAM_BOT_TOKEN);

  console.log('\n  Your Telegram User ID (used for admin access):');
  console.log('  → How to get it: message @userinfobot on Telegram');
  console.log('    It will reply with your ID number.');
  
  let userId = '';
  while (true) {
    userId = await ask(rl, 'Your Telegram User ID', existing.ALLOWED_USER_IDS || '');
    if (!userId) {
      break; // allowed to be empty
    }
    if (/^[0-9,\s]+$/.test(userId)) {
      break;
    }
    console.log('  [Warning] Telegram User ID must be a numeric ID (e.g. 732356803).');
    console.log('  Please message @userinfobot on Telegram to get your ID number.\n');
  }

  // ── OpenCode ─────────────────────────────────────────────────────────────────
  console.log('\n── OpenCode ─────────────────────────────────────────');
  const ocPort = await ask(rl, 'OpenCode server port', existing.OPENCODE_PORT || '4800');

  // ── Model ────────────────────────────────────────────────────────────────────
  console.log('\n── AI Model ─────────────────────────────────────────');
  console.log('  This is the default model used for conversations.');
  const model = await ask(rl, 'Default model', existing.OPENCODE_MODEL || 'deepseek-v4-flash-free');
  const provider = await ask(rl, 'Default provider', existing.OPENCODE_PROVIDER || 'opencode');

  // ── BFP Registry ─────────────────────────────────────────────────────────────
  console.log('\n── BFP Agent Discovery ──────────────────────────────');
  console.log('  The registry lets other BMO agents discover yours.');
  console.log('  Leave blank to use the default public registry.');
  const bfpRegistry = await ask(
    rl,
    'BFP Registry URL',
    existing.BFP_REGISTRY_URL || 'https://bfp-registry.aliwey.workers.dev'
  );

  // ── User Profile ──────────────────────────────────────────────────────────────
  console.log('\n── User Profile ─────────────────────────────────────');
  console.log('  Provide details to personalize your BMO companion.');
  const userName = await ask(rl, 'Your Name', existing_name || 'Developer');
  const userRole = await ask(rl, 'Your Knowledge / Background Description', existing_role || 'Python/Web Developer');
  const userPref = await ask(rl, 'What should BMO do / Where should BMO focus', existing_preference || 'Assist with software development, debugging, and task automation');

  rl.close();

  // ── Write .env ────────────────────────────────────────────────────────────────
  mkdirp(BMO_HOME);
  mkdirp(path.join(BMO_HOME, 'data'));
  mkdirp(path.join(BMO_HOME, 'logs'));

  const envContent = [
    `# BMO Configuration — generated by bmo init`,
    `# Location: ${ENV_PATH}`,
    ``,
    `# Telegram`,
    `TELEGRAM_TOKEN=${token}`,
    `ALLOWED_USER_IDS=${userId}`,
    ``,
    `# OpenCode`,
    `OPENCODE_HOST=127.0.0.1`,
    `OPENCODE_PORT=${ocPort}`,
    ``,
    `# AI Model`,
    `OPENCODE_MODEL=${model}`,
    `OPENCODE_PROVIDER=${provider}`,
    ``,
    `# BFP Agent Discovery`,
    `BFP_REGISTRY_URL=${bfpRegistry}`,
    ``,
    `# Debug (set to True to enable verbose logging)`,
    `DEBUG=False`,
    ``,
  ].join('\n');

  fs.writeFileSync(ENV_PATH, envContent, 'utf8');

  // ── Update memory.md ──────────────────────────────────────────────────────────
  const packageMemoryPath = path.join(PKG_DIR, 'memory.md');

  let memoryContent = '';
  if (fs.existsSync(userMemoryPath)) {
    memoryContent = fs.readFileSync(userMemoryPath, 'utf8');
  } else if (fs.existsSync(packageMemoryPath)) {
    memoryContent = fs.readFileSync(packageMemoryPath, 'utf8');
  }

  if (memoryContent) {
    memoryContent = memoryContent.replace(/-\s*Name:\s*[^\r\n]*/i, `- Name: ${userName}`);
    memoryContent = memoryContent.replace(/-\s*Role:\s*[^\r\n]*/i, `- Role: ${userRole}`);
    memoryContent = memoryContent.replace(/-\s*Preference:\s*[^\r\n]*/i, `- Preference: ${userPref}`);
    fs.writeFileSync(userMemoryPath, memoryContent, 'utf8');
  }

  console.log('\n╭───────────────────────────────────────────────────╮');
  console.log('│' + `  [OK] Config saved to ${ENV_PATH}`.padEnd(51) + '│');
  console.log('│                                                   │');
  console.log('│  Run:  bmo        ← start BMO                    │');
  console.log('│        bmo relay  ← go online for BFP discovery  │');
  console.log('╰───────────────────────────────────────────────────╯\n');
})();
