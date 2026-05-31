# Webchat Rewrite Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use subagent-driven-development or executing-plans. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rewrite webchat to share SQLite database with Telegram bot, support inline actions, add sidebar/multi-session management, and add "Chat on Web" deployment button in Telegram menu.

**Architecture:** Node.js Express backend reads/writes the same `data/bot.db` (SQLite) as the Telegram bot. Frontend is single-page HTML/CSS/JS. Socket.IO for real-time.

**Tech Stack:** Node.js 18+, Express, better-sqlite3, Socket.IO, vanilla HTML/CSS/JS

**Database:** Shared `data/bot.db` — tables: `sessions`, `messages`, `active_sessions`, `user_memory`, `provider_keys`, `permissions`, `user_agents`

---

### Task 1: Install better-sqlite3

**Files:**
- Modify: `webchat/package.json`

- [ ] **Install better-sqlite3 dependency**

```bash
cd webchat
npm install better-sqlite3
```

Expected: `better-sqlite3` appears in `package.json` dependencies and `node_modules/`.

---

### Task 2: Create db.js — Shared Database Module

**Files:**
- Create: `webchat/db.js`

- [ ] **Write db.js with read-only query helpers**

Create `webchat/db.js`:

```js
const Database = require('better-sqlite3');
const path = require('path');

const DB_PATH = path.join(__dirname, '..', 'data', 'bot.db');
const db = new Database(DB_PATH, { fileMustExist: false });
db.pragma('journal_mode = WAL');
db.pragma('foreign_keys = ON');

// ── Session queries ──

const listSessions = db.prepare(`
  SELECT s.id, s.title, s.summary, s.updated_at,
    (SELECT COUNT(*) FROM messages WHERE session_id = s.id) as msg_count,
    (SELECT session_id FROM active_sessions WHERE chat_id = ?) as active_id
  FROM sessions s WHERE s.chat_id = ? ORDER BY s.updated_at DESC
`);

const getSessionById = db.prepare('SELECT * FROM sessions WHERE id = ?');

const getActiveSession = db.prepare('SELECT session_id FROM active_sessions WHERE chat_id = ?');

// ── Message queries ──

const getMessages = db.prepare(
  'SELECT sender, content, timestamp, message_id FROM messages WHERE session_id = ? ORDER BY timestamp ASC LIMIT ?'
);

module.exports = { db, listSessions, getSessionById, getActiveSession, getMessages };
```

---

### Task 3: Create GET /api/sessions Endpoint

**Files:**
- Modify: `webchat/server.js`

- [ ] **Refactor Express app setup and add sessions endpoint**

Replace the top of `webchat/server.js` to import `db.js` instead of `DATA_FILE`/`loadMessages`, and add:

```js
const { db, listSessions, getSessionById, getActiveSession, getMessages } = require('./db');
// (keep existing express, http, Server, cors, path imports)

// Remove: const DATA_FILE = ..., loadMessages(), saveMessages()

app.get('/api/sessions', (req, res) => {
  const chatId = parseInt(req.query.chat_id) || 732356803;
  try {
    const rows = listSessions.all(chatId);
    const activeRow = getActiveSession.get(chatId);
    const sessions = rows.map(r => ({
      session_id: r.id,
      title: r.title || r.id.slice(0, 8),
      summary: (r.summary || '').slice(0, 100),
      msg_count: r.msg_count,
      updated_at: r.updated_at,
      is_active: r.id === (activeRow ? activeRow.session_id : null),
    }));
    res.json(sessions);
  } catch (e) {
    res.status(500).json({ error: e.message });
  }
});
```

Test: `curl http://localhost:3456/api/sessions` returns session list from shared DB.

---

### Task 4: Create POST /api/new-session Endpoint

**Files:**
- Modify: `webchat/server.js`

- [ ] **Add new-session endpoint**

Add prepared statements to `db.js`:

```js
const createSession = db.prepare(`
  INSERT INTO sessions (id, chat_id, user_id, username, title, created_at, updated_at, metadata)
  VALUES (?, ?, ?, ?, '', ?, ?, '{}')
`);
const setActiveSession = db.prepare(
  'INSERT OR REPLACE INTO active_sessions (chat_id, session_id) VALUES (?, ?)'
);

module.exports = { db, listSessions, getSessionById, getActiveSession, getMessages, createSession, setActiveSession };
```

Add to `server.js`:

```js
const { v4: uuidv4 } = require('uuid');

app.post('/api/new-session', express.json(), (req, res) => {
  const { chat_id, user_id, username } = req.body;
  const cid = chat_id || 732356803;
  const uid = user_id || cid;
  const now = Date.now() / 1000;
  const sid = uuidv4();
  try {
    createSession.run(sid, cid, uid, username || '', now, now);
    setActiveSession.run(cid, sid);
    res.json({ session_id: sid });
  } catch (e) {
    res.status(500).json({ error: e.message });
  }
});
```

Add `uuid` package: `npm install uuid`

---

### Task 5: Create POST /api/switch-session Endpoint

**Files:**
- Modify: `webchat/server.js`

- [ ] **Add switch-session endpoint**

```js
const switchSession = db.prepare(
  'INSERT OR REPLACE INTO active_sessions (chat_id, session_id) VALUES (?, ?)'
);

module.exports = { ... existingExports, switchSession };
```

In `server.js`:

```js
app.post('/api/switch-session', express.json(), (req, res) => {
  const { chat_id, session_id } = req.body;
  try {
    switchSession.run(chat_id || 732356803, session_id);
    res.json({ ok: true });
  } catch (e) {
    res.status(500).json({ error: e.message });
  }
});
```

---

### Task 6: Create GET /api/messages Endpoint

**Files:**
- Modify: `webchat/server.js`

- [ ] **Add messages endpoint**

```js
app.get('/api/messages', (req, res) => {
  const sessionId = req.query.session_id;
  const limit = parseInt(req.query.limit) || 50;
  if (!sessionId) return res.status(400).json({ error: 'session_id required' });
  try {
    const msgs = getMessages.all(sessionId, limit);
    res.json(msgs);
  } catch (e) {
    res.status(500).json({ error: e.message });
  }
});
```

---

### Task 7: Rewrite Socket.IO for Shared DB Messaging

**Files:**
- Modify: `webchat/server.js`

- [ ] **Rewrite Socket.IO handlers to use DB + OpenCode**

Replace the Socket.IO `connection` handler:

```js
// Prepared statements for messaging
const addMessageStmt = db.prepare(
  'INSERT INTO messages (session_id, sender, content, timestamp) VALUES (?, ?, ?, ?)'
);
const updateSessionTime = db.prepare('UPDATE sessions SET updated_at = ? WHERE id = ?');
const getOpenCodeSessionId = db.prepare('SELECT opencode_session_id FROM sessions WHERE id = ?');
const setOpenCodeSessionId = db.prepare('UPDATE sessions SET opencode_session_id = ? WHERE id = ?');

io.on('connection', (socket) => {
  console.log('Client connected:', socket.id);
  let activeSessionId = null;

  socket.on('set_session', (sid) => {
    activeSessionId = sid;
  });

  socket.on('user_message', async (data) => {
    const text = data.text;
    const chatId = data.chat_id || 732356803;
    const sid = data.session_id || activeSessionId;

    if (!sid) return socket.emit('error', { message: 'No active session' });

    const now = Date.now() / 1000;

    // 1. Save user message to DB
    addMessageStmt.run(sid, 'user', text, now);
    updateSessionTime.run(now, sid);
    io.emit('message', { id: now, role: 'user', text, time: new Date().toISOString(), session_id: sid });
    io.emit('bmo_status', { status: 'typing' });

    try {
      // 2. Get or create OpenCode session
      const row = getOpenCodeSessionId.get(sid);
      let ocSid = row ? row.opencode_session_id : null;

      if (!ocSid) {
        ocSid = await createOpenCodeSession();
        setOpenCodeSessionId.run(ocSid, sid);
      }

      // 3. Send to OpenCode
      const result = await callOpenCode(text, ocSid);
      const responseText = result.text;

      // 4. Save response & emit
      addMessageStmt.run(sid, 'assistant', responseText, Date.now() / 1000);
      io.emit('message', {
        id: Date.now(),
        role: 'bmo',
        text: responseText,
        time: new Date().toISOString(),
        session_id: sid,
      });
    } catch (e) {
      io.emit('message', { id: Date.now(), role: 'bmo', text: `Error: ${e.message}`, time: new Date().toISOString(), session_id: sid });
    }

    io.emit('bmo_status', { status: 'online' });
  });

  socket.on('disconnect', () => {
    console.log('Client disconnected:', socket.id);
  });
});
```

Keep `createOpenCodeSession()` and `callOpenCode()` from the existing server.js, but remove `loadMessages`/`saveMessages` and the `DATA_FILE` references.

---

### Task 8: Create POST /api/summarize Endpoint

**Files:**
- Modify: `webchat/server.js`

- [ ] **Add summarize endpoint**

```js
const updateSummary = db.prepare('UPDATE sessions SET summary = ?, updated_at = ? WHERE id = ?');

app.post('/api/summarize', express.json(), async (req, res) => {
  const { session_id } = req.body;
  if (!session_id) return res.status(400).json({ error: 'session_id required' });

  try {
    // Get last 50 messages for context
    const msgs = getMessages.all(session_id, 50);
    const history = msgs.map(m => `${m.sender === 'user' ? 'User' : 'Assistant'}: ${m.content}`).join('\n');

    const ocSid = await createOpenCodeSession();
    const result = await callOpenCode(
      `Summarize this conversation concisely:\n\n${history}`,
      ocSid
    );

    const now = Date.now() / 1000;
    updateSummary.run(result.text, now, session_id);
    res.json({ summary: result.text });
  } catch (e) {
    res.status(500).json({ error: e.message });
  }
});
```

---

### Task 9: Create POST /api/clear Endpoint

**Files:**
- Modify: `webchat/server.js`

- [ ] **Add clear endpoint**

```js
const deleteMessages = db.prepare('DELETE FROM messages WHERE session_id = ?');

app.post('/api/clear', express.json(), (req, res) => {
  const { session_id } = req.body;
  if (!session_id) return res.status(400).json({ error: 'session_id required' });

  try {
    deleteMessages.run(session_id);
    res.json({ ok: true });
  } catch (e) {
    res.status(500).json({ error: e.message });
  }
});
```

---

### Task 10: Create GET /api/models Proxy

**Files:**
- Modify: `webchat/server.js`

- [ ] **Add models endpoint proxying OpenCode**

```js
const OPENCODE_URL = `http://127.0.0.1:${process.env.OPENCODE_PORT || process.env.OPENCODE_SERVER_PORT || '13773'}`;

app.get('/api/models', async (req, res) => {
  try {
    const r = await fetch(`${OPENCODE_URL}/api/provider`);
    if (!r.ok) return res.status(502).json({ error: 'OpenCode unavailable' });
    const data = await r.json();
    res.json(data);
  } catch (e) {
    res.status(502).json({ error: e.message });
  }
});
```

---

### Task 11: Create GET /api/agents Endpoint

**Files:**
- Modify: `webchat/server.js`

- [ ] **Add agents endpoint**

```js
const { db } = require('./db');

app.get('/api/agents', (req, res) => {
  const chatId = parseInt(req.query.chat_id) || 732356803;
  try {
    const rows = db.prepare(
      'SELECT agent_id, name, description, system_prompt FROM user_agents WHERE chat_id = ? ORDER BY created_at DESC'
    ).all(chatId);
    res.json(rows);
  } catch (e) {
    res.status(500).json({ error: e.message });
  }
});
```

---

### Task 12: Create POST /api/set-title and /api/switch-model Endpoints

**Files:**
- Modify: `webchat/server.js`

- [ ] **Add set-title endpoint**

```js
const updateTitle = db.prepare('UPDATE sessions SET title = ?, updated_at = ? WHERE id = ?');

app.post('/api/set-title', express.json(), (req, res) => {
  const { session_id, title } = req.body;
  if (!session_id) return res.status(400).json({ error: 'session_id required' });
  try {
    updateTitle.run(title, Date.now() / 1000, session_id);
    res.json({ ok: true });
  } catch (e) {
    res.status(500).json({ error: e.message });
  }
});
```

- [ ] **Add switch-model endpoint**

```js
app.post('/api/switch-model', express.json(), async (req, res) => {
  const { session_id, provider, model } = req.body;
  if (!session_id) return res.status(400).json({ error: 'session_id required' });
  try {
    // Create new OpenCode session with new model
    const payload = { model: { id: model || 'big-pickle', providerID: provider || 'opencode' } };
    const r = await fetch(`${OPENCODE_URL}/session`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload)
    });
    const data = await r.json();
    const newOcSid = data.id;
    // /init handshake
    const initMsgId = `msg_init_${Date.now()}`;
    await fetch(`${OPENCODE_URL}/session/${newOcSid}/init`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ messageID: initMsgId, modelID: model, providerID: provider })
    });
    // Store in DB
    db.prepare('UPDATE sessions SET opencode_session_id = ?, updated_at = ? WHERE id = ?').run(newOcSid, Date.now() / 1000, session_id);
    res.json({ opencode_session_id: newOcSid });
  } catch (e) {
    res.status(500).json({ error: e.message });
  }
});
```

---

### Task 13: Rewrite Frontend HTML Structure

**Files:**
- Rewrite: `webchat/public/index.html`

- [ ] **Restructure HTML with sidebar + toolbar layout**

Replace `index.html` body with:

```html
<div id="connBanner">⚠️ Connection lost — reconnecting...</div>
<div class="app-layout">
  <aside class="sidebar" id="sidebar">
    <div class="sidebar-header">
      <h2>💬 Sessions</h2>
      <button id="sidebarClose">&times;</button>
    </div>
    <div class="session-list" id="sessionList"></div>
  </aside>

  <main class="main-panel">
    <div class="header-row">
      <button id="sidebarToggle" class="icon-btn">☰</button>
      <span class="status-dot" id="statusDot"></span>
      <span id="statusText">Online</span>
      <span class="mode-badge" id="modeBadge">⚡ Execute</span>
      <button id="reloadBtn" class="icon-btn">🔄</button>
    </div>

    <div class="chat-area" id="chatArea">
      <div class="empty" id="emptyState">Send a message to start ✨</div>
    </div>

    <div class="typing-indicator" id="typingIndicator">
      <span></span><span></span><span></span>
    </div>

    <div class="toolbar" id="toolbar">
      <button id="btnNew">🆕 New</button>
      <button id="btnSummarize">💾 Save</button>
      <button id="btnReset">🗑️ Clear</button>
      <button id="btnModels">🤖 Models</button>
      <button id="btnAgents">🎭 Agents</button>
      <button id="btnSkills">🛠️ Skills</button>
    </div>

    <div class="input-area">
      <input type="text" id="msgInput" placeholder="Type your message..." autofocus>
      <button id="sendBtn" disabled>➤</button>
    </div>
  </main>
</div>
```

---

### Task 14: Frontend CSS — Dark Theme with Sidebar

**Files:**
- Modify: `webchat/public/index.html` (within `<style>`)

- [ ] **Add sidebar and toolbar CSS**

Add to existing `<style>`:

```css
.app-layout { display: flex; height: 100vh; }

.sidebar {
  width: 280px; background: var(--bg-secondary);
  border-right: 1px solid var(--border);
  display: flex; flex-direction: column; flex-shrink: 0;
  transition: margin-left 0.3s ease;
  overflow-y: auto;
}

.sidebar.collapsed { margin-left: -280px; }

.sidebar-header {
  padding: 14px 16px; display: flex; justify-content: space-between;
  align-items: center; border-bottom: 1px solid var(--border);
}

.sidebar-header h2 { font-size: 15px; color: var(--text-primary); }

.session-list { flex: 1; overflow-y: auto; padding: 8px; }

.session-item {
  padding: 10px 12px; border-radius: 10px; cursor: pointer;
  margin-bottom: 4px; transition: background 0.15s;
}

.session-item:hover { background: var(--bg-card); }
.session-item.active { background: var(--bg-card); border-left: 3px solid var(--gold); }
.session-title { font-size: 14px; color: var(--text-primary); font-weight: 600; }
.session-meta { font-size: 12px; color: var(--text-muted); margin-top: 2px; }

.main-panel { flex: 1; display: flex; flex-direction: column; min-width: 0; }

.header-row {
  display: flex; align-items: center; gap: 10px;
  padding: 8px 16px; background: var(--bg-secondary);
  border-bottom: 1px solid var(--border); flex-shrink: 0;
}

.icon-btn {
  background: none; border: none; color: var(--text-secondary);
  font-size: 18px; cursor: pointer; padding: 4px 8px; border-radius: 6px;
}

.icon-btn:hover { color: var(--text-primary); background: var(--bg-card); }

.mode-badge {
  font-size: 12px; padding: 2px 10px; border-radius: 10px;
  background: var(--gold-dark); color: #fff; margin-left: auto;
}

.toolbar {
  display: flex; gap: 6px; padding: 8px 16px;
  background: var(--bg-secondary); border-top: 1px solid var(--border);
  flex-shrink: 0; overflow-x: auto;
}

.toolbar button {
  padding: 6px 14px; border-radius: 20px; border: 1px solid var(--border);
  background: var(--bg-card); color: var(--text-primary); font-size: 13px;
  cursor: pointer; white-space: nowrap; transition: all 0.15s;
  font-family: inherit;
}

.toolbar button:hover {
  border-color: var(--gold); color: var(--gold-light);
}
```

---

### Task 15: Frontend JS — Session Sidebar with Polling

**Files:**
- Modify: `webchat/public/index.html` (within `<script>`)

- [ ] **Add sidebar session loading and polling logic**

Add to existing `<script>`:

```js
let sessions = [];
let activeSessionId = null;

async function loadSessions() {
  try {
    const r = await fetch('/api/sessions?chat_id=732356803');
    sessions = await r.json();
    renderSidebar();
    const active = sessions.find(s => s.is_active);
    if (active) activeSessionId = active.session_id;
    else if (sessions.length) activeSessionId = sessions[0].session_id;
  } catch (e) { /* ignore polling errors */ }
}

function renderSidebar() {
  const list = document.getElementById('sessionList');
  list.innerHTML = sessions.map(s => `
    <div class="session-item ${s.session_id === activeSessionId ? 'active' : ''}"
         data-sid="${s.session_id}">
      <div class="session-title">${escapeHtml(s.title)}</div>
      <div class="session-meta">${s.msg_count} msgs</div>
    </div>
  `).join('');
  list.querySelectorAll('.session-item').forEach(el => {
    el.addEventListener('click', () => switchSession(el.dataset.sid));
  });
}

async function switchSession(sid) {
  await fetch('/api/switch-session', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ chat_id: 732356803, session_id: sid })
  });
  activeSessionId = sid;
  await loadSessions();
  await loadMessages(sid);
  socket.emit('set_session', sid);
}

function escapeHtml(str) {
  const d = document.createElement('div');
  d.textContent = str;
  return d.innerHTML;
}

// Poll sessions every 30s
setInterval(loadSessions, 30000);

// Sidebar toggle
document.getElementById('sidebarToggle').onclick = () => {
  document.getElementById('sidebar').classList.toggle('collapsed');
};
document.getElementById('sidebarClose').onclick = () => {
  document.getElementById('sidebar').classList.add('collapsed');
};
```

---

### Task 16: Frontend JS — Load Messages for Active Session

**Files:**
- Modify: `webchat/public/index.html`

- [ ] **Add message loading function**

```js
async function loadMessages(sid) {
  const chatArea = document.getElementById('chatArea');
  const emptyState = document.getElementById('emptyState');

  if (!sid) {
    chatArea.innerHTML = '';
    emptyState.style.display = 'block';
    return;
  }

  try {
    const r = await fetch(`/api/messages?session_id=${sid}&limit=100`);
    const msgs = await r.json();

    chatArea.innerHTML = '';
    if (msgs.length === 0) {
      emptyState.style.display = 'block';
      return;
    }

    emptyState.style.display = 'none';
    msgs.forEach(m => {
      const div = document.createElement('div');
      div.className = 'msg ' + (m.sender === 'user' ? 'user' : 'bmo');
      div.innerHTML = renderMessage(m.content) + `<div class="time">${new Date(m.timestamp * 1000).toLocaleTimeString()}</div>`;
      chatArea.appendChild(div);
    });
    chatArea.scrollTop = chatArea.scrollHeight;
  } catch (e) {
    console.error('Failed to load messages:', e);
  }
}

function renderMessage(text) {
  return text
    .replace(/<pre>([\s\S]*?)<\/pre>/g, '<div class="code-block"><code>$1</code></div>')
    .replace(/\n/g, '<br>');
}
```

---

### Task 17: Frontend JS — Toolbar Actions

**Files:**
- Modify: `webchat/public/index.html`

- [ ] **Wire toolbar buttons to REST API**

```js
document.getElementById('btnNew').addEventListener('click', async () => {
  const r = await fetch('/api/new-session', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ chat_id: 732356803 })
  });
  const data = await r.json();
  await switchSession(data.session_id);
});

document.getElementById('btnReset').addEventListener('click', async () => {
  if (!activeSessionId) return;
  await fetch('/api/clear', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ session_id: activeSessionId })
  });
  document.getElementById('chatArea').innerHTML = '';
  document.getElementById('emptyState').style.display = 'block';
});

document.getElementById('btnSummarize').addEventListener('click', async () => {
  if (!activeSessionId) return;
  document.getElementById('statusText').textContent = 'Summarizing...';
  const r = await fetch('/api/summarize', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ session_id: activeSessionId })
  });
  const data = await r.json();
  document.getElementById('statusText').textContent = 'Online';
  if (data.summary) {
    // Show summary in chat
    const chatArea = document.getElementById('chatArea');
    const div = document.createElement('div');
    div.className = 'msg bmo';
    div.innerHTML = `<b>📋 Summary</b><br>${data.summary.slice(0, 500)}`;
    chatArea.appendChild(div);
  }
});

document.getElementById('btnModels').addEventListener('click', async () => {
  const r = await fetch('/api/models');
  // TODO: Show model picker modal — simplified: alert for now
  alert('Model picker — implement modal UI here');
});

document.getElementById('btnAgents').addEventListener('click', async () => {
  const r = await fetch('/api/agents?chat_id=732356803');
  const agents = await r.json();
  const names = agents.map(a => `• ${a.name}: ${a.description || 'No description'}`).join('\n');
  alert(`Custom Agents:\n${names || 'No custom agents'}`);
});
```

---

### Task 18: Frontend JS — Socket.IO Message Streaming

**Files:**
- Modify: `webchat/public/index.html`

- [ ] **Update Socket.IO handlers for session-aware messaging**

Update the Socket.IO initialization and handlers:

```js
const socket = io();

socket.on('connect', () => {
  if (activeSessionId) socket.emit('set_session', activeSessionId);
});

socket.on('message', (msg) => {
  // Only show messages for the active session
  if (msg.session_id && msg.session_id !== activeSessionId) return;

  const chatArea = document.getElementById('chatArea');
  document.getElementById('emptyState').style.display = 'none';

  const div = document.createElement('div');
  div.className = 'msg ' + msg.role;
  div.innerHTML = renderMessage(msg.text) + `<div class="time">${new Date(msg.time).toLocaleTimeString()}</div>`;
  chatArea.appendChild(div);
  chatArea.scrollTop = chatArea.scrollHeight;
});

socket.on('bmo_status', (data) => {
  const dot = document.getElementById('statusDot');
  const text = document.getElementById('statusText');
  dot.className = 'status-dot';
  if (data.status === 'typing') {
    dot.classList.add('typing');
    text.textContent = 'Typing...';
    document.getElementById('typingIndicator').classList.add('show');
  } else {
    text.textContent = 'Online';
    document.getElementById('typingIndicator').classList.remove('show');
  }
});
```

---

### Task 19: Add 🌐 "Chat on Web" Inline Button

**Files:**
- Modify: `handlers/messages.py`

- [ ] **Add button to build_inline_menu**

In `handlers/messages.py`, `build_inline_menu()` at line ~156, change Row 5 from:

```python
# Row 5: Tools
kb.append([
    InlineKeyboardButton("🎭 Agents", callback_data="action:agents"),
    InlineKeyboardButton("🛠️ Skills", callback_data="action:use_skill"),
])
```

to:

```python
# Row 5: Tools
kb.append([
    InlineKeyboardButton("🎭 Agents", callback_data="action:agents"),
    InlineKeyboardButton("🛠️ Skills", callback_data="action:use_skill"),
])
# Row 6: Web chat
kb.append([
    InlineKeyboardButton("🌐 Chat on Web", callback_data="action:launch_webchat"),
])
# Row 7: System (was Row 6)
kb.append([
    InlineKeyboardButton("ℹ️ Status", callback_data="action:status"),
    InlineKeyboardButton("⚙️ Settings", callback_data="action:settings"),
])
```

---

### Task 20: Implement launch_webchat Deploy Handler

**Files:**
- Modify: `handlers/messages.py`

- [ ] **Add async handler that starts server + tunnel**

In `handlers/messages.py`, add function:

```python
async def _handle_launch_webchat(update, context):
    import subprocess, asyncio, os, re

    query = update.callback_query
    await query.edit_message_text(
        "🌐 <b>Starting Web Chat...</b>\n\nLaunching server and tunnel...",
        parse_mode=ParseMode.HTML,
    )

    webchat_dir = os.path.join(os.path.dirname(__file__), '..', 'webchat')

    # 1. Start Node.js server
    server_proc = await asyncio.create_subprocess_exec(
        'node', 'server.js',
        cwd=webchat_dir,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    await asyncio.sleep(2)  # Wait for server to bind

    # 2. Start Cloudflare tunnel
    tunnel_proc = await asyncio.create_subprocess_exec(
        'cloudflared', 'tunnel', '--url', 'http://localhost:3456',
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )

    # 3. Poll stderr for tunnel URL
    url = None
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        line = await asyncio.wait_for(tunnel_proc.stderr.readline(), timeout=5)
        line_text = line.decode().strip()
        m = re.search(r'https://[a-zA-Z0-9.-]+\.trycloudflare\.com', line_text)
        if m:
            url = m.group(0)
            break

    if not url:
        await query.edit_message_text(
            "❌ <b>Failed to start tunnel</b>\n\nCheck that cloudflared is installed.",
            parse_mode=ParseMode.HTML,
        )
        return

    await query.edit_message_text(
        f"🌐 <b>Web Chat is Live!</b>\n\nOpen your browser:\n<code>{url}</code>\n\n"
        f"<i>Session shared with Telegram bot.</i>",
        parse_mode=ParseMode.HTML,
    )
```

---

### Task 21: Wire launch_webchat into Callback Router

**Files:**
- Modify: `handlers/messages.py`

- [ ] **Route action:launch_webchat to handler**

In the `inline_action_callback` function, find the `action = data.split(":")[1]` dispatch logic and add:

```python
if action == "launch_webchat":
    await _handle_launch_webchat(update, context)
    return
```

---

### Task 22: Verify End-to-End Session Sharing

**Files:**
- None (manual test)

- [ ] **Start all services and verify**

```bash
# Terminal 1: OpenCode
opencode serve

# Terminal 2: Telegram bot
python main.py

# Terminal 3: Webchat
cd webchat && node server.js
```

Steps:
1. Open `http://localhost:3456` in browser
2. Send a message via webchat
3. Check `data/bot.db` messages table has the message
4. Send `/menu` in Telegram → see same session (check title/message count)
5. Send message in Telegram → verify it appears in webchat on reload

---

### Task 23: Verify Inline Actions End-to-End

**Files:**
- None (manual test)

- [ ] **Test all toolbar buttons**

Test each action from the webchat toolbar:
1. **New** → creates new session in DB, sidebar updates, chat clears
2. **Save** → triggers summarize, summary appears in chat
3. **Clear** → deletes messages for current session
4. **Models** → shows model picker (or logs to console)
5. **Agents** → shows custom agents list
6. **Skills** → (future: show available skills)

---

### Task 24: Verify "Chat on Web" Button from Telegram

**Files:**
- None (manual test)

- [ ] **Test the deploy flow**

1. Tap 🌐 Chat on Web in Telegram inline menu
2. Verify: "Starting Web Chat..." message appears
3. Wait for tunnel URL
4. Click URL in Telegram → opens webchat
5. Verify webchat shares sessions with Telegram bot

---

## Timeout Audit (Completed)

All timeouts increased in `config/settings.py`:
| Setting | Old Value | New Value |
|---------|-----------|-----------|
| `OPENCODE_TIMEOUT` | 600s | 1200s |
| `OPENCODE_POLL_TIMEOUT` | 3600s | 3600s (unchanged) |

No other timeouts needed changing — they are all short-lived (5-30s for `/init`, `/provider`, summarization, key verification).
