const express = require('express');
const http = require('http');
const { Server } = require('socket.io');
const cors = require('cors');
const path = require('path');
const fs = require('fs');
const { v4: uuidv4 } = require('uuid');
const db = require('./db_api');

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

// ── Auto-redirect root to active session (before static middleware) ──
app.get('/', async (req, res) => {
  if (req.query.chat_id && req.query.session_id) {
    return res.sendFile(path.join(__dirname, 'public', 'index.html'));
  }
  const chatId = await db.resolveChatId();
  if (chatId) {
    try {
      const sessions = await db.listSessions(chatId);
      const active = sessions.find(s => s.is_active);
      if (active) {
        return res.redirect(`/?chat_id=${chatId}&session_id=${active.session_id}`);
      }
      if (sessions.length > 0) {
        return res.redirect(`/?chat_id=${chatId}&session_id=${sessions[0].session_id}`);
      }
    } catch (_) {}
  }
  res.sendFile(path.join(__dirname, 'public', 'index.html'));
});

app.use(express.static(path.join(__dirname, 'public')));

const OPENCODE_PORT = process.env.OPENCODE_PORT || process.env.OPENCODE_SERVER_PORT || '4800';
const OPENCODE_URL = `http://127.0.0.1:${OPENCODE_PORT}`;

// ── REST Endpoints ────────────────────────────────────────────

app.get('/api/sessions', async (req, res) => {
  const chatId = await db.resolveChatId(parseInt(req.query.chat_id));
  if (!chatId) return res.json([]);
  try {
    const sessions = await db.listSessions(chatId);
    res.json(sessions);
  } catch (e) {
    res.status(500).json({ error: e.message });
  }
});

app.post('/api/new-session', async (req, res) => {
  const cid = await db.resolveChatId(req.body.chat_id);
  if (!cid) return res.status(400).json({ error: 'chat_id required' });
  const uid = req.body.user_id || cid;
  try {
    const result = await db.createSession(cid, uid, req.body.username || '');
    res.json({ session_id: result.session_id });
  } catch (e) {
    res.status(500).json({ error: e.message });
  }
});

app.post('/api/switch-session', async (req, res) => {
  const { chat_id, session_id } = req.body;
  const cid = await db.resolveChatId(chat_id);
  if (!cid) return res.status(400).json({ error: 'chat_id required' });
  try {
    await db.switchSession(cid, session_id);
    res.json({ ok: true });
  } catch (e) {
    res.status(500).json({ error: e.message });
  }
});

app.post('/api/set-session', async (req, res) => {
  const { chat_id, session_id } = req.body;
  if (!chat_id || !session_id) {
    return res.status(400).json({ error: 'chat_id and session_id required' });
  }
  try {
    const result = await db.setSession(chat_id, session_id);
    res.json(result);
  } catch (e) {
    if (e.message.includes('404')) {
      return res.status(404).json({ error: 'Session not found' });
    }
    res.status(500).json({ error: e.message });
  }
});

app.get('/api/messages', async (req, res) => {
  const sessionId = req.query.session_id;
  const limit = parseInt(req.query.limit) || 50;
  if (!sessionId) return res.status(400).json({ error: 'session_id required' });
  try {
    const msgs = await db.getMessages(sessionId, limit);
    res.json(msgs);
  } catch (e) {
    res.status(500).json({ error: e.message });
  }
});

app.post('/api/summarize', async (req, res) => {
  const { session_id } = req.body;
  if (!session_id) return res.status(400).json({ error: 'session_id required' });
  try {
    const msgs = await db.getMessages(session_id, 50);
    const history = msgs.map(m => `${m.sender === 'user' ? 'User' : 'Assistant'}: ${m.content}`).join('\n');
    const ocSid = await createOpenCodeSession();
    const result = await callOpenCode(`Summarize this conversation concisely:\n\n${history}`, ocSid);
    await db.setSummary(session_id, result.text);
    res.json({ summary: result.text });
  } catch (e) {
    res.status(500).json({ error: e.message });
  }
});

app.post('/api/clear', async (req, res) => {
  const { session_id } = req.body;
  if (!session_id) return res.status(400).json({ error: 'session_id required' });
  try {
    await db.clearMessages(session_id);
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

    validProviders.sort((a, b) => (a.id === 'opencode' ? -1 : b.id === 'opencode' ? 1 : 0));

    res.json({ providers: validProviders, connected: connectedIds });
  } catch (e) {
    res.status(502).json({ error: e.message });
  }
});

app.get('/api/agents', async (req, res) => {
  const chatId = await db.resolveChatId(parseInt(req.query.chat_id));
  if (!chatId) return res.json([]);
  try {
    const agents = await db.listAgents(chatId);
    res.json(agents);
  } catch (e) {
    res.status(500).json({ error: e.message });
  }
});

app.post('/api/create-agent', async (req, res) => {
  const { chat_id, name, description, system_prompt } = req.body;
  if (!name) return res.status(400).json({ error: 'name required' });
  try {
    const cid = await db.resolveChatId(chat_id);
    if (!cid) return res.status(400).json({ error: 'chat_id required' });
    await db.createAgent(cid, name, description, system_prompt);
    res.json({ ok: true });
  } catch (e) {
    res.status(500).json({ error: e.message });
  }
});

app.post('/api/update-agent', async (req, res) => {
  const { agent_id, name, description, system_prompt } = req.body;
  if (!agent_id || !name) return res.status(400).json({ error: 'agent_id and name required' });
  try {
    await db.updateAgent(agent_id, name, description, system_prompt);
    res.json({ ok: true });
  } catch (e) {
    res.status(500).json({ error: e.message });
  }
});

app.post('/api/delete-agent', async (req, res) => {
  const { agent_id } = req.body;
  if (!agent_id) return res.status(400).json({ error: 'agent_id required' });
  try {
    await db.deleteAgent(agent_id);
    res.json({ ok: true });
  } catch (e) {
    res.status(500).json({ error: e.message });
  }
});

app.post('/api/generate-agent-prompt', async (req, res) => {
  const { description } = req.body;
  if (!description) return res.status(400).json({ error: 'description required' });
  try {
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

app.post('/api/set-title', async (req, res) => {
  const { session_id, title } = req.body;
  if (!session_id) return res.status(400).json({ error: 'session_id required' });
  try {
    await db.setTitle(session_id, title);
    res.json({ ok: true });
  } catch (e) {
    res.status(500).json({ error: e.message });
  }
});

app.post('/api/switch-model', async (req, res) => {
  const { session_id, provider, model } = req.body;
  if (!session_id) return res.status(400).json({ error: 'session_id required' });
  try {
    const parts = (model || 'big-pickle').split('/');
    const bareModelId = parts.pop();
    const providerId = provider || parts.pop() || 'opencode';
    const payload = {
      model: { id: bareModelId, providerID: providerId },
      agent: 'build'
    };
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
    await db.switchModel(session_id, newOcSid);
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

async function createOpenCodeSession(modelId = 'qwen3.6-plus-free', providerId = 'opencode', agent = 'build') {
  const payload = {
    model: { id: modelId, providerID: providerId },
    agent: agent === 'default' ? 'build' : agent
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
    console.log('[OpenCode] Created session:', sessionId, 'model:', modelId, 'agent:', payload.agent);
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
    sessionId = await createOpenCodeSession(modelId || 'qwen3.6-plus-free', providerId || 'opencode', agent);
    r = await doSend(sessionId);
  }

  if (r && r.status === 500 && modelId) {
    console.log('[OpenCode] Session 500, creating new session with model:', modelId);
    sessionId = await createOpenCodeSession(modelId, providerId, agent);
    r = await doSend(sessionId, 30000);
  }

  if (!r && modelId) {
    console.log('[OpenCode] doSend aborted, retrying with 30s timeout...');
    r = await doSend(sessionId, 30000);
  }

  if (r && !r.ok) {
    const status = r.status;
    const errText = await r.text().catch(() => '');
    console.log('[OpenCode] Prompt failed:', status, errText.slice(0, 300));
    return { text: `Error: opencode returned status ${status} - ${errText.slice(0, 200)}`, reasoning: '', sessionId };
  }

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

  const errorParts = parts.filter(p => p.type === 'error');
  if (errorParts.length > 0) {
    const errorMsg = errorParts[0].message || 'Unknown error';
    return { text: `Error: ${errorMsg}`, reasoning: '' };
  }

  let reasoning = '';
  for (const p of parts) {
    if (p.type === 'reasoning' && p.text) {
      reasoning += p.text;
    }
  }

  const textParts = [];
  for (const p of parts) {
    if (p.synthetic) continue;
    if (p.type === 'text' && p.text) {
      textParts.push(p.text);
    }
  }

  const responseText = textParts.join('\n').trim();

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
    const chatId = await db.resolveChatId(data.chat_id) || activeChatId;
    const sid = data.session_id || activeSessionId;
    const model = data.model;
    if (chatId) activeChatId = chatId;
    if (!sid) return socket.emit('error', { message: 'No active session' });

    console.log('[Socket] user_message:', text.slice(0, 50), 'session:', sid, 'model:', model);

    await db.addMessage(sid, 'user', text, 'webchat');
    io.emit('message', { id: Date.now() / 1000, role: 'user', text, time: new Date().toISOString(), session_id: sid });
    io.emit('bmo_status', { status: 'typing' });

    // Auto-title on 2nd message
    try {
      const msgCount = await db.getMessageCount(sid);
      if (msgCount.cnt === 2) {
        const msgs = await db.getMessages(sid, 2);
        const firstMsg = msgs[0]?.content || text;
        const title = generateTitle(firstMsg);
        await db.setTitle(sid, title);
        io.emit('session_title_updated', { session_id: sid, title });
      }
    } catch (e) {
      console.error('Auto-title error:', e.message);
    }

    try {
      const ocResult = await db.getOpenCodeSessionId(sid);
      let ocSid = ocResult ? ocResult.opencode_session_id : null;
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
        await db.setOpenCodeSessionId(ocSid, sid);
        console.log('[Socket] Saved OpenCode session:', ocSid);
      }

      const result = await callOpenCode(text, ocSid, 'execute', 'default', chatId, (reasoning) => {
        io.emit('reasoning', { text: reasoning, session_id: sid });
      }, modelId, providerId);

      if (result && result.sessionId && result.sessionId !== ocSid) {
        await db.setOpenCodeSessionId(result.sessionId, sid);
        console.log('[Socket] Updated OpenCode session after recovery:', result.sessionId);
      }
      const responseText = result.text;
      const reasoningText = result.reasoning || '';
      await db.addMessage(sid, 'bmo', responseText, 'webchat');
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
