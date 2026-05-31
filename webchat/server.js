const express = require('express');
const http = require('http');
const { Server } = require('socket.io');
const cors = require('cors');
const path = require('path');
const fs = require('fs');
const { v4: uuidv4 } = require('uuid');
const {
  db, listSessions, getSessionById, getActiveSession, getMessages,
  createSession, setActiveSession, switchSession,
  addMessageStmt, updateSessionTime, getOpenCodeSessionId, setOpenCodeSessionId,
  updateSummary, deleteMessages, updateTitle,
} = require('./db');

// ── Load shared system prompt config (single source of truth) ──
const configPath = path.join(__dirname, '..', 'config', 'system-prompt.json');
const systemConfig = JSON.parse(fs.readFileSync(configPath, 'utf-8'));

function buildSystemPrompt(mode = 'execute', agent = 'default', chatId = null, sessionId = null) {
  const modeInstruction = systemConfig.mode_prompts[mode] || systemConfig.mode_prompts.execute;
  const agentInstruction = systemConfig.agent_prompts[agent] || '';
  
  let chatContext = '';
  if (chatId) {
    chatContext = `\n\n[USER_CONTEXT]\nCURRENT_CHAT_ID: ${chatId}\nCURRENT_SESSION_ID: ${sessionId || 'unknown'}\n[END USER_CONTEXT]`;
    chatContext += '\n\n<b>FILE STORAGE</b>: When creating files, save them inside <code>data/files/</code>. Use date-based subfolders: <code>data/files/{YYYY-MM-DD}/{CURRENT_SESSION_ID}_{HHMMSS}_{filename}</code> so files are linked to sessions and dates.';
  }
  
  return systemConfig.base_prompt + systemConfig.memory_instruction + systemConfig.tool_instruction + chatContext + systemConfig.anti_loop + agentInstruction + `\n\nCURRENT PROTOCOL: ${modeInstruction}`;
}

const app = express();
const server = http.createServer(app);
const io = new Server(server, { cors: { origin: '*', methods: ['GET', 'POST'] } });

app.use(cors());
app.use(express.json());
app.use(express.static(path.join(__dirname, 'public')));

const OPENCODE_PORT = process.env.OPENCODE_PORT || process.env.OPENCODE_SERVER_PORT || '4096';
const OPENCODE_URL = `http://127.0.0.1:${OPENCODE_PORT}`;

// ── REST Endpoints ────────────────────────────────────────────

function resolveChatId(chatId) {
  if (chatId) return chatId;
  const row = db.prepare('SELECT DISTINCT chat_id FROM sessions ORDER BY updated_at DESC LIMIT 1').get();
  return row ? row.chat_id : null;
}

app.get('/api/sessions', (req, res) => {
  const chatId = resolveChatId(parseInt(req.query.chat_id));
  if (!chatId) return res.json([]);
  try {
    const rows = listSessions.all(chatId, chatId);
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

app.post('/api/new-session', express.json(), (req, res) => {
  const cid = resolveChatId(req.body.chat_id);
  if (!cid) return res.status(400).json({ error: 'chat_id required' });
  const uid = req.body.user_id || cid;
  const now = Date.now() / 1000;
  const sid = uuidv4();
  try {
    createSession.run(sid, cid, uid, req.body.username || '', now, now);
    setActiveSession.run(cid, sid);
    res.json({ session_id: sid });
  } catch (e) {
    res.status(500).json({ error: e.message });
  }
});

app.post('/api/switch-session', express.json(), (req, res) => {
  const { chat_id, session_id } = req.body;
  const cid = resolveChatId(chat_id);
  if (!cid) return res.status(400).json({ error: 'chat_id required' });
  try {
    switchSession.run(cid, session_id);
    res.json({ ok: true });
  } catch (e) {
    res.status(500).json({ error: e.message });
  }
});

app.post('/api/set-session', express.json(), (req, res) => {
  const { chat_id, session_id } = req.body;
  if (!chat_id || !session_id) {
    return res.status(400).json({ error: 'chat_id and session_id required' });
  }
  try {
    const session = getSessionById.get(session_id);
    if (!session) {
      return res.status(404).json({ error: 'Session not found' });
    }
    setActiveSession.run(chat_id, session_id);
    let ocRow = getOpenCodeSessionId.get(session_id);
    let opencode_session_id = ocRow ? ocRow.opencode_session_id : null;
    
    // Share Telegram session's OpenCode session for continuity
    if (!opencode_session_id) {
      const telRow = db.prepare(
        `SELECT opencode_session_id FROM sessions
         WHERE chat_id = ? AND opencode_session_id IS NOT NULL
         ORDER BY updated_at DESC LIMIT 1`
      ).get(chat_id);
      if (telRow && telRow.opencode_session_id) {
        setOpenCodeSessionId.run(telRow.opencode_session_id, session_id);
        opencode_session_id = telRow.opencode_session_id;
      }
    }
    
    res.json({
      opencode_session_id,
      session: {
        id: session.id,
        title: session.title,
        chat_id: session.chat_id,
      }
    });
  } catch (e) {
    res.status(500).json({ error: e.message });
  }
});

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

app.post('/api/summarize', express.json(), async (req, res) => {
  const { session_id } = req.body;
  if (!session_id) return res.status(400).json({ error: 'session_id required' });
  try {
    const msgs = getMessages.all(session_id, 50);
    const history = msgs.map(m => `${m.sender === 'user' ? 'User' : 'Assistant'}: ${m.content}`).join('\n');
    const ocSid = await createOpenCodeSession();
    const result = await callOpenCode(`Summarize this conversation concisely:\n\n${history}`, ocSid);
    const now = Date.now() / 1000;
    updateSummary.run(result.text, now, session_id);
    res.json({ summary: result.text });
  } catch (e) {
    res.status(500).json({ error: e.message });
  }
});

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

app.get('/api/models', async (req, res) => {
  try {
    const r = await fetch(`${OPENCODE_URL}/provider`);
    if (!r.ok) return res.status(502).json({ error: 'OpenCode unavailable' });
    const data = await r.json();
    
    // data has: { all: [...], connected: [...] }
    const allProviders = data.all || [];
    const connectedIds = data.connected || [];
    
    const validProviders = [];
    for (const p of allProviders) {
      const pId = p.id;
      const isAccessible = connectedIds.includes(pId);
      const models = p.models || {};
      
      const providerModels = [];
      for (const [mid, mData] of Object.entries(models)) {
        const isFree = mid.toLowerCase().includes(':free') || 
                       (mData.name || '').toLowerCase().includes('free') || 
                       pId === 'opencode';
        
        if (isAccessible || isFree) {
          let label = mData.name || mid;
          label = label.replace('-latest', '').replace('-pro', ' Pro').replace('-lite', ' Lite').replace('-flash', ' Flash');
          
          providerModels.push({
            id: `${pId}/${mid}`,
            pid: pId,
            mid: mid,
            name: label,
            is_free: isFree,
          });
        }
      }
      
      if (providerModels.length > 0) {
        providerModels.sort((a, b) => (a.is_free === b.is_free ? 0 : a.is_free ? -1 : 1));
        validProviders.push({
          id: pId,
          name: p.name || pId,
          models: providerModels,
        });
      }
    }
    
    // Ensure 'opencode' is always first
    validProviders.sort((a, b) => (a.id === 'opencode' ? -1 : b.id === 'opencode' ? 1 : 0));
    
    res.json({ providers: validProviders, connected: connectedIds });
  } catch (e) {
    res.status(502).json({ error: e.message });
  }
});

app.get('/api/agents', (req, res) => {
  const chatId = resolveChatId(parseInt(req.query.chat_id));
  if (!chatId) return res.json([]);
  try {
    const rows = db.prepare(
      'SELECT agent_id, name, description, system_prompt FROM user_agents WHERE chat_id = ? ORDER BY created_at DESC'
    ).all(chatId);
    res.json(rows.map(r => ({
      agent_id: r.agent_id,
      name: r.name,
      description: r.description || '',
      system_prompt: r.system_prompt || '',
    })));
  } catch (e) {
    res.status(500).json({ error: e.message });
  }
});

app.post('/api/create-agent', express.json(), (req, res) => {
  const { chat_id, name, description, system_prompt } = req.body;
  if (!name) return res.status(400).json({ error: 'name required' });
  try {
    const now = Date.now() / 1000;
    const uid = resolveChatId(chat_id) || 0;
    if (!uid) return res.status(400).json({ error: 'chat_id required' });
    db.prepare(
      'INSERT INTO user_agents (chat_id, user_id, name, description, system_prompt, created_at) VALUES (?, ?, ?, ?, ?, ?)'
    ).run(uid, uid, name, description || '', system_prompt || '', now);
    res.json({ ok: true });
  } catch (e) {
    res.status(500).json({ error: e.message });
  }
});

app.post('/api/update-agent', express.json(), (req, res) => {
  const { agent_id, name, description, system_prompt } = req.body;
  if (!agent_id || !name) return res.status(400).json({ error: 'agent_id and name required' });
  try {
    db.prepare(
      'UPDATE user_agents SET name = ?, description = ?, system_prompt = ? WHERE agent_id = ?'
    ).run(name, description || '', system_prompt || '', agent_id);
    res.json({ ok: true });
  } catch (e) {
    res.status(500).json({ error: e.message });
  }
});

app.post('/api/delete-agent', express.json(), (req, res) => {
  const { agent_id } = req.body;
  if (!agent_id) return res.status(400).json({ error: 'agent_id required' });
  try {
    db.prepare('DELETE FROM user_agents WHERE agent_id = ?').run(agent_id);
    res.json({ ok: true });
  } catch (e) {
    res.status(500).json({ error: e.message });
  }
});

app.post('/api/generate-agent-prompt', express.json(), async (req, res) => {
  const { description } = req.body;
  if (!description) return res.status(400).json({ error: 'description required' });
  try {
    // Generate a structured prompt template locally instead of calling OpenCode
    const systemPrompt = `You are ${description}. 

Your role and guidelines:
- Act as: ${description}
- Tone: Professional, helpful, and engaging
- Always stay in character
- Provide clear, accurate, and relevant responses
- If unsure, ask clarifying questions

Remember your identity and purpose in every interaction.`;

    res.json({ system_prompt: systemPrompt });
  } catch (e) {
    res.status(500).json({ error: e.message });
  }
});

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

app.post('/api/switch-model', express.json(), async (req, res) => {
  const { session_id, provider, model } = req.body;
  if (!session_id) return res.status(400).json({ error: 'session_id required' });
  try {
    const parts = (model || 'big-pickle').split('/');
    const bareModelId = parts.pop();
    const providerId = provider || parts.pop() || 'opencode';
    const payload = { model: { id: bareModelId, providerID: providerId } };
    const r = await fetch(`${OPENCODE_URL}/session`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload)
    });
    const data = await r.json();
    const newOcSid = data.id;
    const initMsgId = `msg_init_${Date.now()}`;
    await fetch(`${OPENCODE_URL}/session/${newOcSid}/init`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ messageID: initMsgId, modelID: bareModelId, providerID: providerId })
    });
    db.prepare('UPDATE sessions SET opencode_session_id = ?, updated_at = ? WHERE id = ?').run(newOcSid, Date.now() / 1000, session_id);
    res.json({ opencode_session_id: newOcSid });
  } catch (e) {
    res.status(500).json({ error: e.message });
  }
});

// ── OpenCode helpers ───────────────────────────────────────────

function generateTitle(firstMessage) {
  const words = firstMessage.trim().split(/\s+/);
  if (words.length <= 6) return firstMessage.trim();
  return words.slice(0, 6).join(' ') + '…';
}

async function createOpenCodeSession(modelId = 'qwen3.6-plus-free', providerId = 'opencode') {
  const payload = {
    model: { id: modelId, providerID: providerId }
  };
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 10000);
  try {
    const r = await fetch(`${OPENCODE_URL}/session`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload),
      signal: controller.signal
    });
    clearTimeout(timeout);
    if (!r.ok) {
      const errText = await r.text().catch(() => '');
      throw new Error(`create session failed: ${r.status} - ${errText.slice(0, 200)}`);
    }
    const data = await r.json();
    const sessionId = data.id;
    console.log('[OpenCode] Created session:', sessionId, 'model:', modelId);
    return sessionId;
  } catch (e) {
    clearTimeout(timeout);
    throw e;
  }
}

async function callOpenCode(text, sessionId, mode = 'execute', agent = 'default', chatId = null, onReasoning = null, modelId = null, providerId = null) {
  const systemPrompt = buildSystemPrompt(mode, agent, chatId, sessionId);
  
  const payload = {
    parts: [
      { type: 'text', text: systemPrompt, synthetic: true },
      { type: 'text', text }
    ]
  };
  
  console.log('[OpenCode] Sending to session:', sessionId);
  
  async function doSend(sid, timeoutMs = 15000) {
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), timeoutMs);
    try {
      const r = await fetch(`${OPENCODE_URL}/session/${sid}/message`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload),
        signal: controller.signal
      });
      clearTimeout(timeout);
      return r;
    } catch (e) {
      clearTimeout(timeout);
      console.log('[OpenCode] doSend error:', e.message);
      return null;
    }
  }
  
  let r = await doSend(sessionId);
  
  if (r && r.status === 404) {
    console.log('[OpenCode] Session not found, creating new one...');
    sessionId = await createOpenCodeSession(modelId || 'qwen3.6-plus-free', providerId || 'opencode');
    r = await doSend(sessionId);
  }
  
  // 500 with a recoverable model → create new session and retry
  if (r && r.status === 500 && modelId) {
    console.log('[OpenCode] Session 500, creating new session with model:', modelId);
    sessionId = await createOpenCodeSession(modelId, providerId);
    r = await doSend(sessionId, 30000);
  }
  
  // Abort/timeout (null r) on a fresh session → retry with longer timeout once
  if (!r && modelId) {
    console.log('[OpenCode] doSend aborted, retrying with 30s timeout...');
    r = await doSend(sessionId, 30000);
  }
  
  // Timeout (null r) is not an error — fall through to polling
  if (r && !r.ok) {
    const status = r.status;
    const errText = await r.text().catch(() => '');
    console.log('[OpenCode] Prompt failed:', status, errText.slice(0, 300));
    return { text: `Error: opencode returned status ${status} - ${errText.slice(0, 200)}`, reasoning: '', sessionId };
  }
  
  // Try to extract response from the immediate POST response first
  if (r) {
    try {
      console.log('[OpenCode] Parsing POST response...');
      const data = await r.json();
      console.log('[OpenCode] POST response keys:', Object.keys(data));
      const result = extractResponse(data);
      if (result.text) {
        console.log('[OpenCode] Response from POST:', result.text.slice(0, 100));
        return { ...result, sessionId };
      }
    } catch (e) {
      console.log('[OpenCode] Could not parse POST response:', e.message);
    }
  }
  
  // Poll for response
  const deadline = Date.now() + 60000;
  const pollInterval = 1500;
  let lastReasoning = '';
  
  console.log('[OpenCode] Polling for response...');
  
  while (Date.now() < deadline) {
    await new Promise(resolve => setTimeout(resolve, pollInterval));
    
    try {
      const controller = new AbortController();
      const pollTimeout = setTimeout(() => controller.abort(), 5000);
      const mr = await fetch(`${OPENCODE_URL}/session/${sessionId}/message?limit=20`, {
        signal: controller.signal
      });
      clearTimeout(pollTimeout);
      
      if (!mr.ok) continue;
      
      const messages = await mr.json();
      if (!messages || messages.length === 0) continue;
      
      const msg = messages[messages.length - 1];
      
      // Emit live reasoning updates
      if (onReasoning) {
        let reasoning = '';
        for (const p of (msg.parts || [])) {
          if (p.type === 'reasoning' && p.text) reasoning += p.text;
        }
        if (reasoning && reasoning !== lastReasoning) {
          onReasoning(reasoning);
          lastReasoning = reasoning;
        }
      }
      
      const info = msg.info || {};
      if (info.role !== 'assistant') continue;
      
      const result = extractResponse(msg);
      if (result.text) {
        console.log('[OpenCode] Response from poll:', result.text.slice(0, 100));
        return { ...result, sessionId };
      }
    } catch (e) {
      console.log('[OpenCode] Poll error:', e.message);
    }
  }
  
  console.log('[OpenCode] Poll timeout');
  return { text: '(Response timed out)', reasoning: '', sessionId };
}

function extractResponse(msg) {
  const parts = msg.parts || [];
  const info = msg.info || {};
  
  // Check for error parts
  const errorParts = parts.filter(p => p.type === 'error');
  if (errorParts.length > 0) {
    const errorMsg = errorParts[0].message || 'Unknown error';
    return { text: `Error: ${errorMsg}`, reasoning: '' };
  }
  
  // Extract reasoning/thinking
  let reasoning = '';
  for (const p of parts) {
    if (p.type === 'reasoning' && p.text) {
      reasoning += p.text;
    }
  }
  
  // Extract text parts (skip synthetic)
  const textParts = [];
  for (const p of parts) {
    if (p.synthetic) continue;
    if (p.type === 'text' && p.text) {
      textParts.push(p.text);
    }
  }
  
  const responseText = textParts.join('\n').trim();
  
  // Check if complete
  const hasFinish = parts.some(p => p.type === 'step-finish');
  const finishReason = info.finish;
  const isComplete = hasFinish || ['stop', 'error', 'length'].includes(finishReason);
  
  if (isComplete || responseText) {
    return { text: responseText || '(no response)', reasoning };
  }
  
  return { text: '', reasoning };
}

// ── Socket.IO ──────────────────────────────────────────────────

io.on('connection', (socket) => {
  console.log('Client connected:', socket.id);
  let activeSessionId = null;
  let activeChatId = null;

  socket.on('set_session', (sid) => { activeSessionId = sid; });

  socket.on('user_message', async (data) => {
    const text = data.text;
    const chatId = resolveChatId(data.chat_id) || activeChatId;
    const sid = data.session_id || activeSessionId;
    const model = data.model;
    // Cache chat_id for this socket session
    if (chatId) activeChatId = chatId;
    if (!sid) return socket.emit('error', { message: 'No active session' });

    console.log('[Socket] user_message:', text.slice(0, 50), 'session:', sid, 'model:', model);

    const now = Date.now() / 1000;
    addMessageStmt.run(sid, 'user', text, now, 'webchat');
    updateSessionTime.run(now, sid);
    io.emit('message', { id: now, role: 'user', text, time: new Date().toISOString(), session_id: sid });
    io.emit('bmo_status', { status: 'typing' });

    // Auto-title on 2nd message
    try {
      const msgCount = db.prepare('SELECT COUNT(*) as cnt FROM messages WHERE session_id = ?').get(sid);
      if (msgCount.cnt === 2) {
        const msgs = getMessages.all(sid, 2);
        const firstMsg = msgs[0]?.content || text;
        const title = generateTitle(firstMsg);
        updateTitle.run(title, now, sid);
        io.emit('session_title_updated', { session_id: sid, title });
      }
    } catch (e) {
      console.error('Auto-title error:', e.message);
    }

    try {
      const row = getOpenCodeSessionId.get(sid);
      let ocSid = row ? row.opencode_session_id : null;
      console.log('[Socket] OpenCode session ID from DB:', ocSid);
      
      let modelId = 'qwen3.6-plus-free';
      let providerId = 'opencode';
      if (model) {
        const parts = model.split('/');
        modelId = parts.pop();
        providerId = parts.pop() || 'opencode';
      }
      
      if (!ocSid) {
        console.log('[Socket] No OpenCode session, creating new one with model:', modelId);
        ocSid = await createOpenCodeSession(modelId, providerId);
        setOpenCodeSessionId.run(ocSid, sid);
        console.log('[Socket] Saved OpenCode session:', ocSid);
      }
      
      const result = await callOpenCode(text, ocSid, 'execute', 'default', chatId, (reasoning) => {
        io.emit('reasoning', { text: reasoning, session_id: sid });
      }, modelId, providerId);
      
      // Save new sessionId if callOpenCode recovered from 500/404
      if (result && result.sessionId && result.sessionId !== ocSid) {
        setOpenCodeSessionId.run(result.sessionId, sid);
        console.log('[Socket] Updated OpenCode session after recovery:', result.sessionId);
      }
      const responseText = result.text;
      const reasoningText = result.reasoning || '';
      addMessageStmt.run(sid, 'bmo', responseText, Date.now() / 1000, 'webchat');
      io.emit('message', {
        id: Date.now() / 1000, role: 'bmo', text: responseText, reasoning: reasoningText,
        time: new Date().toISOString(), session_id: sid,
      });
      io.emit('reasoning', { text: null, session_id: sid });
    } catch (e) {
      console.error('[Socket] Error:', e.message);
      io.emit('message', {
        id: Date.now() / 1000, role: 'bmo', text: `Error: ${e.message}`,
        time: new Date().toISOString(), session_id: sid,
      });
    }
    io.emit('bmo_status', { status: 'online' });
  });

  socket.on('disconnect', () => { console.log('Client disconnected:', socket.id); });
});

const PORT = process.env.PORT || 3456;
server.listen(PORT, '0.0.0.0', () => {
  console.log(`BMO WebChat running on http://0.0.0.0:${PORT}`);
});
