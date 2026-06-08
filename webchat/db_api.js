/**
 * BMO Webchat DB API Client
 *
 * Replaces better-sqlite3 with HTTP calls to the Python backend (core/webchat_api.py).
 * Zero native dependencies — works on any Node.js version without compilation.
 */

const BMO_API_URL = process.env.BMO_API_URL || 'http://127.0.0.1:4098';

async function api(path, options = {}) {
  const url = `${BMO_API_URL}${path}`;
  const r = await fetch(url, {
    headers: { 'Content-Type': 'application/json' },
    ...options,
  });
  if (!r.ok) {
    const text = await r.text().catch(() => '');
    throw new Error(`API ${r.status} ${path}: ${text.slice(0, 200)}`);
  }
  return r.json();
}

function apiGet(path) {
  return api(path, { method: 'GET' });
}

function apiPost(path, body) {
  return api(path, { method: 'POST', body: JSON.stringify(body) });
}

function apiPut(path, body) {
  return api(path, { method: 'PUT', body: JSON.stringify(body) });
}

function apiDelete(path) {
  return api(path, { method: 'DELETE' });
}

// ── Chat resolution ─────────────────────────────────────────────────────────

async function resolveChatId(chatId) {
  if (chatId) return chatId;
  const data = await apiGet('/api/chat/resolve');
  return data.chat_id || null;
}

// ── Sessions ────────────────────────────────────────────────────────────────

async function listSessions(chatId) {
  return apiGet(`/api/sessions?chat_id=${chatId}`);
}

async function getSessionById(sessionId) {
  return apiGet(`/api/sessions/${sessionId}`);
}

async function createSession(chatId, userId, username) {
  return apiPost('/api/sessions', {
    chat_id: chatId,
    user_id: userId,
    username: username || '',
  });
}

async function activateSession(chatId, sessionId) {
  return apiPost(`/api/sessions/${sessionId}/activate`, { chat_id: chatId });
}

async function switchSession(chatId, sessionId) {
  return apiPost('/api/sessions/switch', { chat_id: chatId, session_id: sessionId });
}

async function setSession(chatId, sessionId) {
  return apiPost('/api/sessions/set', { chat_id: chatId, session_id: sessionId });
}

async function getMessages(sessionId, limit) {
  return apiGet(`/api/sessions/${sessionId}/messages?limit=${limit || 50}`);
}

async function addMessage(sessionId, sender, content, interface_) {
  return apiPost(`/api/sessions/${sessionId}/messages`, {
    sender,
    content,
    interface: interface_ || 'webchat',
  });
}

async function setTitle(sessionId, title) {
  return apiPost(`/api/sessions/${sessionId}/title`, { title });
}

async function setSummary(sessionId, summary) {
  return apiPost(`/api/sessions/${sessionId}/summary`, { summary });
}

async function clearMessages(sessionId) {
  return apiPost(`/api/sessions/${sessionId}/clear`);
}

async function getOpenCodeSessionId(sessionId) {
  return apiGet(`/api/sessions/${sessionId}/opencode-session`);
}

async function setOpenCodeSessionId(opencodeSessionId, sessionId) {
  return apiPost(`/api/sessions/${sessionId}/opencode-session`, { opencode_session_id: opencodeSessionId });
}

async function getMessageCount(sessionId) {
  return apiGet(`/api/sessions/${sessionId}/msg-count`);
}

async function switchModel(sessionId, opencodeSessionId) {
  return apiPost(`/api/sessions/${sessionId}/switch-model`, { opencode_session_id: opencodeSessionId });
}

// ── Agents ──────────────────────────────────────────────────────────────────

async function listAgents(chatId) {
  return apiGet(`/api/agents?chat_id=${chatId}`);
}

async function createAgent(chatId, name, description, systemPrompt) {
  return apiPost('/api/agents', { chat_id: chatId, name, description: description || '', system_prompt: systemPrompt || '' });
}

async function updateAgent(agentId, name, description, systemPrompt) {
  return apiPut(`/api/agents/${agentId}`, { name, description: description || '', system_prompt: systemPrompt || '' });
}

async function deleteAgent(agentId) {
  return apiDelete(`/api/agents/${agentId}`);
}

module.exports = {
  resolveChatId,
  listSessions,
  getSessionById,
  createSession,
  activateSession,
  switchSession,
  setSession,
  getMessages,
  addMessage,
  setTitle,
  setSummary,
  clearMessages,
  getOpenCodeSessionId,
  setOpenCodeSessionId,
  getMessageCount,
  switchModel,
  listAgents,
  createAgent,
  updateAgent,
  deleteAgent,
};
