lucide.createIcons();

const socket = io();
const chatArea = document.getElementById('chatArea');
const emptyState = document.getElementById('emptyState');
const input = document.getElementById('msgInput');
const sendBtn = document.getElementById('sendBtn');
const typingEl = document.getElementById('typingIndicator');
const statusDot = document.getElementById('statusDot');
const statusText = document.getElementById('statusText');
const connBanner = document.getElementById('connBanner');
const sessionList = document.getElementById('sessionList');
const sidebar = document.getElementById('sidebar');
const sidebarOverlay = document.getElementById('sidebarOverlay');

let sessions = [];
let activeSessionId = null;
let currentChatId = null;
let disconnectTimer = null;
let currentModel = localStorage.getItem('selectedModel') || 'opencode/big-pickle';
let currentAgent = localStorage.getItem('selectedAgent') || 'None';

// Socket.IO
socket.on('connect', () => {
  clearTimeout(disconnectTimer);
  connBanner.classList.remove('show');
  if (activeSessionId) socket.emit('set_session', activeSessionId);
  setStatus('online');
});

socket.on('disconnect', () => {
  disconnectTimer = setTimeout(() => {
    connBanner.classList.add('show');
  }, 2000);
  setStatus('offline');
});

socket.on('message', (msg) => {
  try {
    if (msg.session_id && msg.session_id !== activeSessionId) {
      console.log('[socket] filtered message: session_id', msg.session_id, '!= active', activeSessionId);
      return;
    }
    emptyState.style.display = 'none';
    const div = document.createElement('div');
    div.className = 'msg ' + (msg.role || 'bmo');
    if (msg.id) div.setAttribute('data-ts', msg.id);
    div.innerHTML = renderMessage(msg.text, msg.reasoning) + `<div class="time">${new Date(msg.time).toLocaleTimeString()}</div>`;
    chatArea.appendChild(div);
    lucide.createIcons();
    chatArea.scrollTop = chatArea.scrollHeight;
    if (msg.role === 'assistant' || msg.role === 'bmo') {
      setStatus('online');
      const liveEl = document.getElementById('liveReasoning');
      if (liveEl) liveEl.remove();
      if (_pollTimer) { clearInterval(_pollTimer); _pollTimer = null; }
    }
    console.log('[socket] rendered message:', msg.role, (msg.text || '').slice(0, 40));
  } catch (e) {
    console.error('[socket] message handler error:', e);
  }
});

socket.on('bmo_status', (data) => {
  if (data.status === 'typing') setStatus('typing');
  else setStatus('online');
});

socket.on('error', (data) => {
  showToast(data.message || 'An error occurred', 'error');
});

socket.on('reasoning', (data) => {
  if (data.session_id && data.session_id !== activeSessionId) return;
  if (!data.text) {
    const el = document.getElementById('liveReasoning');
    if (el) el.remove();
    return;
  }
  let el = document.getElementById('liveReasoning');
  if (!el) {
    el = document.createElement('div');
    el.id = 'liveReasoning';
    el.className = 'msg bmo reasoning-live';
    chatArea.appendChild(el);
    emptyState.style.display = 'none';
  }
  el.innerHTML = `<details class="thinking-block" open><summary><i data-lucide="sparkles" class="inline-icon"></i> Thinking...</summary><div class="thinking-content">${escapeHtml(data.text).replace(/\n/g, '<br>')}</div></details>`;
  chatArea.scrollTop = chatArea.scrollHeight;
  lucide.createIcons();
});

socket.on('session_title_updated', (data) => {
  const session = sessions.find(s => s.session_id === data.session_id);
  if (session) {
    session.title = data.title;
    renderSidebar();
  }
});

// Sidebar Toggle
document.getElementById('sidebarToggle').onclick = () => {
  sidebar.classList.toggle('collapsed');
  sidebarOverlay.classList.toggle('show');
  updateSidebarIcon();
};

document.getElementById('sidebarClose').onclick = () => {
  sidebar.classList.add('collapsed');
  sidebarOverlay.classList.remove('show');
  updateSidebarIcon();
};

sidebarOverlay.onclick = () => {
  sidebar.classList.add('collapsed');
  sidebarOverlay.classList.remove('show');
  updateSidebarIcon();
};

function updateSidebarIcon() {
  const toggleBtn = document.getElementById('sidebarToggle');
  const isCollapsed = sidebar.classList.contains('collapsed');
  const svg = toggleBtn.querySelector('svg');
  if (svg) svg.remove();
  const i = document.createElement('i');
  i.setAttribute('data-lucide', isCollapsed ? 'panel-left-open' : 'panel-left-close');
  toggleBtn.appendChild(i);
  lucide.createIcons();
}

// ── URL Param Session Loading ───────────────────────────────────
function getUrlParams() {
  const params = new URLSearchParams(window.location.search);
  return {
    chat_id: params.get('chat_id'),
    session_id: params.get('session_id'),
  };
}

async function initFromUrlParams() {
  const { chat_id, session_id } = getUrlParams();
  if (chat_id && session_id) {
    try {
      const r = await fetch('/api/set-session', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ chat_id: parseInt(chat_id), session_id })
      });
      if (r.ok) {
        currentChatId = parseInt(chat_id);
        _sessionIdFromUrl = session_id;
        activeSessionId = session_id;
        const profileId = document.getElementById('profileId');
        if (profileId) profileId.textContent = `ID: ${currentChatId}`;
        await loadSessions();
        await loadMessages(session_id);
        socket.emit('set_session', session_id);
        return true;
      }
    } catch (e) {
      console.error('Failed to load session from URL params:', e);
    }
  }
  return false;
}

async function initFallbackSession() {
  try {
    await loadSessions();
    if (activeSessionId) {
      const profileId = document.getElementById('profileId');
      if (profileId && currentChatId) profileId.textContent = `ID: ${currentChatId}`;
      await loadMessages(activeSessionId);
      socket.emit('set_session', activeSessionId);
    }
  } catch (e) {
    console.error('Fallback session load failed:', e);
  }
}

// Sessions
let _sessionIdFromUrl = null;

async function loadSessions() {
  try {
    const cid = currentChatId || '';
    const r = await fetch(`/api/sessions?chat_id=${cid}`);
    sessions = await r.json();
    renderSidebar();
    const active = sessions.find(s => s.is_active);
    if (active) activeSessionId = active.session_id;
    else if (sessions.length && !_sessionIdFromUrl) activeSessionId = sessions[0].session_id;
    document.getElementById('profileSessions').textContent = sessions.length;
  } catch (e) {}
}

function renderSidebar() {
  sessionList.innerHTML = sessions.map((s, i) => `
    <div class="session-item ${s.session_id === activeSessionId ? 'active' : ''}"
         data-sid="${s.session_id}" style="animation-delay: ${0.25 + i * 0.05}s">
      <div class="session-title">
        <span class="dot"></span>
        ${escapeHtml(s.title)}
      </div>
      <div class="session-meta">
        <i data-lucide="message-circle"></i>
        ${s.msg_count} msgs · ${formatTime(s.updated_at)}
      </div>
    </div>
  `).join('');
  sessionList.querySelectorAll('.session-item').forEach(el => {
    el.addEventListener('click', () => switchSession(el.dataset.sid));
  });
  lucide.createIcons();
}

function formatTime(ts) {
  if (!ts) return '';
  const d = new Date(ts * 1000);
  const now = new Date();
  const diff = now - d;
  if (diff < 60000) return 'now';
  if (diff < 3600000) return Math.floor(diff / 60000) + 'm ago';
  if (diff < 86400000) return Math.floor(diff / 3600000) + 'h ago';
  return d.toLocaleDateString();
}

async function switchSession(sid) {
  await fetch('/api/switch-session', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ chat_id: currentChatId, session_id: sid })
  });
  activeSessionId = sid;
  await loadSessions();
  await loadMessages(sid);
  socket.emit('set_session', sid);
  // Close sidebar on mobile
  if (window.innerWidth < 768) {
    sidebar.classList.add('collapsed');
    sidebarOverlay.classList.remove('show');
  }
}

// Messages
async function loadMessages(sid) {
  if (!sid) { chatArea.innerHTML = ''; emptyState.style.display = 'block'; return; }
  try {
    const r = await fetch(`/api/messages?session_id=${sid}&limit=100`);
    const msgs = await r.json();
    chatArea.innerHTML = '';
    if (msgs.length === 0) { emptyState.style.display = 'block'; return; }
    emptyState.style.display = 'none';
    msgs.forEach(m => {
      const div = document.createElement('div');
      div.className = 'msg ' + (m.sender === 'user' ? 'user' : 'bmo');
      div.setAttribute('data-ts', m.timestamp);
      div.innerHTML = renderMessage(m.content, m.reasoning) + `<div class="time">${new Date(m.timestamp * 1000).toLocaleTimeString()}</div>`;
      chatArea.appendChild(div);
    });
    chatArea.scrollTop = chatArea.scrollHeight;
    lucide.createIcons();
  } catch (e) { console.error('Failed to load messages:', e); }
}

function renderMessage(text, reasoning) {
  let html = '';
  
  // Thinking block
  if (reasoning) {
    html += `<details class="thinking-block"><summary><i data-lucide="brain" class="inline-icon"></i> Thinking</summary><div class="thinking-content">${escapeHtml(String(reasoning)).replace(/\n/g, '<br>')}</div></details>`;
  }
  
  // Markdown rendering
  const content = String(text || '');
  if (typeof marked !== 'undefined') {
    // Configure marked
    marked.setOptions({
      highlight: function(code, lang) {
        if (lang && typeof hljs !== 'undefined' && hljs.getLanguage(lang)) {
          try {
            return hljs.highlight(code, { language: lang }).value;
          } catch (e) {}
        }
        if (typeof hljs !== 'undefined') {
          try {
            return hljs.highlightAuto(code).value;
          } catch (e) {}
        }
        return escapeHtml(code);
      },
      langPrefix: 'hljs language-',
      breaks: true,
      gfm: true
    });
    
    // Custom renderer for code blocks with copy button and line numbers
    const renderer = new marked.Renderer();
    const originalCode = renderer.code.bind(renderer);
    renderer.code = function(token) {
      const code = token.text;
      const lang = token.lang || 'text';
      let highlighted;
      if (typeof hljs !== 'undefined' && hljs.getLanguage(lang)) {
        try {
          highlighted = hljs.highlight(code, { language: lang }).value;
        } catch (e) {
          highlighted = escapeHtml(code);
        }
      } else if (typeof hljs !== 'undefined') {
        try {
          highlighted = hljs.highlightAuto(code).value;
        } catch (e) {
          highlighted = escapeHtml(code);
        }
      } else {
        highlighted = escapeHtml(code);
      }
      
      // Add line numbers
      const lines = highlighted.split('\n');
      const lineNumbers = lines.map((_, i) => `<span class="line-number">${i + 1}</span>`).join('\n');
      
      return `<div class="code-block-wrapper">
        <div class="code-header">
          <span class="code-lang">${escapeHtml(lang)}</span>
          <button class="copy-btn" onclick="copyCode(this)"><i data-lucide="copy" class="inline-icon"></i> Copy</button>
        </div>
        <div class="code-block-with-lines">
          <div class="line-numbers">${lineNumbers}</div>
          <pre><code class="hljs language-${escapeHtml(lang)}">${highlighted}</code></pre>
        </div>
      </div>`;
    };
    
    marked.setOptions({ renderer });
    html += `<div class="md-content">${marked.parse(content)}</div>`;
  } else {
    // Fallback: basic rendering if marked not loaded
    html += content.replace(/<pre>([\s\S]*?)<\/pre>/g, '<div class="code-block"><code>$1</code></div>').replace(/\n/g, '<br>');
  }
  
  return html;
}

// Copy code function
function copyCode(btn) {
  const wrapper = btn.closest('.code-block-wrapper');
  const code = wrapper.querySelector('code');
  const text = code.textContent;
  navigator.clipboard.writeText(text).then(() => {
    const originalHTML = btn.innerHTML;
    btn.innerHTML = '<i data-lucide="check" class="inline-icon"></i> Copied!';
    btn.classList.add('copied');
    setTimeout(() => {
      btn.innerHTML = originalHTML;
      btn.classList.remove('copied');
      lucide.createIcons();
    }, 2000);
    lucide.createIcons();
  });
}

// Modal System
function openModal(id) {
  document.getElementById(id).classList.add('show');
  document.body.style.overflow = 'hidden';
}

function closeModal(id) {
  document.getElementById(id).classList.remove('show');
  document.body.style.overflow = '';
}

document.querySelectorAll('[data-close]').forEach(btn => {
  btn.onclick = () => closeModal(btn.dataset.close);
});

document.querySelectorAll('.modal-overlay').forEach(overlay => {
  overlay.onclick = (e) => {
    if (e.target === overlay) closeModal(overlay.id);
  };
});

// Models Modal
document.getElementById('btnModels').onclick = async () => {
  openModal('modelsModal');
  const body = document.getElementById('modelsBody');
  body.innerHTML = '<div class="modal-loading"><i data-lucide="loader-2" class="spin"></i> Loading models...</div>';
  lucide.createIcons();
  
  try {
    const r = await fetch('/api/models');
    const data = await r.json();
    const providers = data.providers || [];
    
    if (!providers.length) {
      body.innerHTML = '<div class="modal-empty"><i data-lucide="alert-circle"></i><p>No models available. Add API keys in settings first.</p></div>';
      lucide.createIcons();
      return;
    }
    
    renderModelPage(body, providers, 0, 0);
  } catch (e) {
    body.innerHTML = '<div class="modal-empty"><i data-lucide="alert-triangle"></i><p>Failed to load models</p></div>';
    lucide.createIcons();
  }
};

function renderModelPage(body, providers, pIdx, mPage) {
  const modelsPerPage = 10;
  const currentP = providers[pIdx];
  const allModels = currentP.models || [];
  const maxMPage = Math.max(0, Math.ceil(allModels.length / modelsPerPage) - 1);
  mPage = Math.max(0, Math.min(mPage, maxMPage));
  
  const start = mPage * modelsPerPage;
  const end = start + modelsPerPage;
  const pageModels = allModels.slice(start, end);
  
  let html = `
    <div class="model-header-bar">
      <span class="model-page-title"><i data-lucide="cpu"></i> Select a Model</span>
      <span class="model-page-info">${escapeHtml(currentP.name)} (${pIdx + 1}/${providers.length}) · Page ${mPage + 1}/${maxMPage + 1}</span>
    </div>
    <div class="model-grid">
  `;
  
  for (const model of pageModels) {
    const isSelected = currentModel === model.id;
    html += `
      <div class="model-card ${isSelected ? 'selected' : ''}" data-model="${model.id}" data-provider="${model.pid}">
        <div class="model-name">${model.is_free ? '<i data-lucide="gift" class="inline-icon"></i> ' : '<i data-lucide="zap" class="inline-icon"></i> '}${escapeHtml(model.name)}</div>
        <div class="model-provider">${escapeHtml(model.pid)}</div>
      </div>
    `;
  }
  
  html += '</div>';
  
  // Pagination controls
  html += '<div class="model-pagination">';
  if (mPage > 0) {
    html += `<button class="model-nav-btn" data-action="prev-model"><i data-lucide="chevron-left"></i> Prev Models</button>`;
  }
  if (mPage < maxMPage) {
    html += `<button class="model-nav-btn" data-action="next-model">Next Models <i data-lucide="chevron-right"></i></button>`;
  }
  html += '</div>';
  
  html += '<div class="model-provider-nav">';
  if (pIdx > 0) {
    html += `<button class="model-nav-btn secondary" data-action="prev-provider"><i data-lucide="chevrons-left"></i> Previous Provider</button>`;
  }
  if (pIdx < providers.length - 1) {
    html += `<button class="model-nav-btn secondary" data-action="next-provider">Next Provider <i data-lucide="chevrons-right"></i></button>`;
  }
  html += '</div>';
  
  body.innerHTML = html;
  
  body.querySelectorAll('.model-card').forEach(card => {
    card.onclick = () => selectModel(card.dataset.provider, card.dataset.model);
  });
  
  body.querySelectorAll('.model-nav-btn').forEach(btn => {
    btn.onclick = () => {
      const action = btn.dataset.action;
      let newPIdx = pIdx;
      let newMPage = mPage;
      
      if (action === 'prev-model') newMPage--;
      else if (action === 'next-model') newMPage++;
      else if (action === 'prev-provider') { newPIdx--; newMPage = 0; }
      else if (action === 'next-provider') { newPIdx++; newMPage = 0; }
      
      renderModelPage(body, providers, newPIdx, newMPage);
    };
  });
  
  // Re-render icons after dynamic content
  setTimeout(() => lucide.createIcons(), 50);
}

function updateModelBadge() {
  const el = document.getElementById('modelBadgeText');
  if (el) el.textContent = currentModel.split('/').pop();
}

function selectModel(provider, model) {
  currentModel = model;
  localStorage.setItem('selectedModel', model);
  document.getElementById('profileModel').textContent = model.split('/').pop();
  updateModelBadge();
  
  document.querySelectorAll('.model-card').forEach(c => c.classList.remove('selected'));
  document.querySelector(`.model-card[data-model="${model}"]`)?.classList.add('selected');
  
  showToast(`Model: ${model.split('/').pop()}`, 'success');
  
  if (activeSessionId) {
    fetch('/api/switch-model', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ session_id: activeSessionId, provider, model })
    }).catch(e => showToast(`Failed to switch model: ${e.message}`, 'error'));
  }
}

// Agents Modal
document.getElementById('btnAgents').onclick = async () => {
  openModal('agentsModal');
  const body = document.getElementById('agentsBody');
  body.innerHTML = '<div class="modal-loading"><i data-lucide="loader-2" class="spin"></i> Loading agents...</div>';
  lucide.createIcons();
  
  try {
    const r = await fetch(`/api/agents?chat_id=${currentChatId}`);
    const agents = await r.json();
    
    if (!agents.length) {
      body.innerHTML = `
        <div class="modal-empty">
          <i data-lucide="users"></i>
          <p>No agents yet</p>
          <button class="btn-primary" onclick="showCreateAgent()"><i data-lucide="plus"></i> Create Agent</button>
        </div>
      `;
      lucide.createIcons();
      return;
    }
    
    let html = '<div class="agent-list">';
    for (const agent of agents) {
      const isSelected = currentAgent === agent.name;
      html += `
        <div class="agent-card ${isSelected ? 'active' : ''}" data-agent="${agent.name}">
          <div class="agent-name">
            <i data-lucide="bot"></i>
            ${escapeHtml(agent.name)}
            ${isSelected ? '<span class="agent-badge">Active</span>' : ''}
          </div>
          <div class="agent-desc">${escapeHtml(agent.description || 'No description')}</div>
          <div class="agent-actions">
            <button class="agent-action-btn" onclick="event.stopPropagation(); selectAgent('${escapeHtml(agent.name)}')">
              <i data-lucide="check"></i> Select
            </button>
            <button class="agent-action-btn" onclick="event.stopPropagation(); editAgent(${agent.agent_id}, '${escapeHtml(agent.name)}', '${escapeHtml(agent.description || '')}', '${escapeHtml(agent.system_prompt || '')}')">
              <i data-lucide="edit"></i> Edit
            </button>
            <button class="agent-action-btn danger" onclick="event.stopPropagation(); deleteAgent(${agent.agent_id}, '${escapeHtml(agent.name)}')">
              <i data-lucide="trash-2"></i> Delete
            </button>
          </div>
        </div>
      `;
    }
    html += `
      <div class="agent-actions-footer">
        <button class="btn-primary create-agent-btn" onclick="showCreateAgent()">
          <i data-lucide="plus"></i> Create New Agent
        </button>
        <button class="btn-secondary restore-agent-btn" onclick="restoreDefaultAgent()">
          <i data-lucide="rotate-ccw"></i> Restore Default
        </button>
      </div>
    </div>`;
    body.innerHTML = html;
    
    body.querySelectorAll('.agent-card').forEach(card => {
      card.onclick = () => selectAgent(card.dataset.agent);
    });
    lucide.createIcons();
  } catch (e) {
    body.innerHTML = '<div class="modal-empty"><i data-lucide="alert-triangle"></i><p>Failed to load agents</p></div>';
    lucide.createIcons();
  }
};

function selectAgent(name) {
  currentAgent = name;
  localStorage.setItem('selectedAgent', name);
  document.getElementById('profileAgent').textContent = name;
  
  document.querySelectorAll('.agent-card').forEach(c => c.classList.remove('active'));
  document.querySelector(`.agent-card[data-agent="${name}"]`)?.classList.add('active');
  
  showToast(`Agent: ${name}`, 'success');
}

function editAgent(id, name, description, systemPrompt) {
  const body = document.getElementById('agentsBody');
  body.innerHTML = `
    <div class="create-agent-form">
      <h4 style="margin-bottom:16px;display:flex;align-items:center;gap:8px">
        <i data-lucide="edit"></i> Edit Agent
      </h4>
      <input type="hidden" id="agentEditId" value="${id}">
      <div class="form-group">
        <label><i data-lucide="user"></i> Agent Name</label>
        <input type="text" id="agentNameInput" value="${escapeHtml(name)}">
      </div>
      <div class="form-group">
        <label><i data-lucide="message-square"></i> Description</label>
        <input type="text" id="agentDescInput" value="${escapeHtml(description)}">
      </div>
      <div class="form-group">
        <label><i data-lucide="file-text"></i> System Prompt</label>
        <textarea id="agentPromptInput" rows="4">${escapeHtml(systemPrompt)}</textarea>
      </div>
      <div style="display:flex;gap:10px;flex-wrap:wrap">
        <button class="btn-primary" onclick="updateAgent()"><i data-lucide="save"></i> Save Changes</button>
        <button class="btn-secondary" onclick="document.getElementById('btnAgents').click()"><i data-lucide="arrow-left"></i> Cancel</button>
      </div>
    </div>
  `;
  lucide.createIcons();
}

async function updateAgent() {
  const id = document.getElementById('agentEditId').value;
  const name = document.getElementById('agentNameInput').value.trim();
  const desc = document.getElementById('agentDescInput').value.trim();
  const prompt = document.getElementById('agentPromptInput').value.trim();
  
  if (!name) {
    showToast('Agent name is required', 'error');
    return;
  }
  
  try {
    await fetch('/api/update-agent', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ agent_id: id, name, description: desc, system_prompt: prompt })
    });
    showToast(`Agent "${name}" updated!`, 'success');
    document.getElementById('btnAgents').click();
  } catch (e) {
    showToast('Failed to update agent', 'error');
  }
}

async function deleteAgent(id, name) {
  if (!confirm(`Delete agent "${name}"?`)) return;
  
  try {
    await fetch('/api/delete-agent', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ agent_id: id })
    });
    showToast(`Agent "${name}" deleted`, 'success');
    document.getElementById('btnAgents').click();
  } catch (e) {
    showToast('Failed to delete agent', 'error');
  }
}

function restoreDefaultAgent() {
  currentAgent = 'BMO Default';
  localStorage.setItem('selectedAgent', 'BMO Default');
  document.getElementById('profileAgent').textContent = 'BMO Default';
  showToast('Restored to BMO Default', 'success');
  closeModal('agentsModal');
}

function showCreateAgent() {
  const body = document.getElementById('agentsBody');
  body.innerHTML = `
    <div class="create-agent-form">
      <div class="form-group">
        <label><i data-lucide="user"></i> Agent Name</label>
        <input type="text" id="agentNameInput" placeholder="e.g., Code Reviewer">
      </div>
      <div class="form-group">
        <label><i data-lucide="message-square"></i> Description</label>
        <input type="text" id="agentDescInput" placeholder="What does this agent do?">
      </div>
      <div class="form-group">
        <label><i data-lucide="file-text"></i> System Prompt</label>
        <textarea id="agentPromptInput" rows="4" placeholder="Define the agent's behavior..."></textarea>
      </div>
      <div id="agentGenProgress" style="display:none" class="agent-progress">
        <div class="progress-bar-container">
          <div class="progress-bar" id="agentProgressBar"></div>
        </div>
        <p id="agentProgressText">Generating...</p>
      </div>
      <div style="display:flex;gap:10px;flex-wrap:wrap">
        <button class="btn-primary" onclick="createAgent()"><i data-lucide="check"></i> Create</button>
        <button class="btn-secondary" onclick="generateAgentPrompt()"><i data-lucide="wand-2"></i> AI Generate</button>
        <button class="btn-secondary" onclick="document.getElementById('btnAgents').click()"><i data-lucide="arrow-left"></i> Back</button>
      </div>
    </div>
  `;
  lucide.createIcons();
}

async function generateAgentPrompt() {
  const desc = document.getElementById('agentDescInput').value.trim();
  if (!desc) {
    showToast('Enter a description first', 'error');
    return;
  }
  
  const progressEl = document.getElementById('agentGenProgress');
  const progressBar = document.getElementById('agentProgressBar');
  const progressText = document.getElementById('agentProgressText');
  const promptInput = document.getElementById('agentPromptInput');
  
  progressEl.style.display = 'block';
  promptInput.disabled = true;
  progressBar.style.width = '20%';
  progressText.innerHTML = '<i data-lucide="cpu" class="inline-icon"></i> Connecting to AI...';
  lucide.createIcons();
  
  try {
    progressBar.style.width = '50%';
    progressText.innerHTML = '<i data-lucide="brain" class="inline-icon"></i> Generating agent personality...';
    lucide.createIcons();
    
    const r = await fetch('/api/generate-agent-prompt', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ description: desc })
    });
    
    if (!r.ok) {
      const err = await r.json();
      throw new Error(err.error || 'Server error');
    }
    
    progressBar.style.width = '80%';
    progressText.innerHTML = '<i data-lucide="file-text" class="inline-icon"></i> Processing response...';
    lucide.createIcons();
    
    const data = await r.json();
    
    // Parse JSON if returned
    let name = document.getElementById('agentNameInput').value.trim();
    let prompt = data.system_prompt;
    
    try {
      const parsed = JSON.parse(prompt);
      if (parsed.name && !name) name = parsed.name;
      if (parsed.prompt) prompt = parsed.prompt;
    } catch (e) {}
    
    progressBar.style.width = '100%';
    progressText.innerHTML = '<i data-lucide="check-circle" class="inline-icon"></i> Agent generated!';
    lucide.createIcons();
    
    promptInput.value = prompt;
    if (name) document.getElementById('agentNameInput').value = name;
    
    setTimeout(() => {
      progressEl.style.display = 'none';
      promptInput.disabled = false;
    }, 1500);
    
  } catch (e) {
    progressText.innerHTML = `<i data-lucide="alert-circle" class="inline-icon"></i> Failed: ${escapeHtml(e.message)}`;
    progressBar.style.background = 'var(--accent-danger)';
    lucide.createIcons();
    setTimeout(() => {
      progressEl.style.display = 'none';
      promptInput.disabled = false;
      progressBar.style.background = 'var(--accent-primary)';
    }, 4000);
  }
}

async function createAgent() {
  const name = document.getElementById('agentNameInput').value.trim();
  const desc = document.getElementById('agentDescInput').value.trim();
  const prompt = document.getElementById('agentPromptInput').value.trim();
  
  if (!name) {
    showToast('Agent name is required', 'error');
    return;
  }
  
  try {
    await fetch('/api/create-agent', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ chat_id: currentChatId, name, description: desc, system_prompt: prompt })
    });
    showToast(`Agent "${name}" created!`, 'success');
    document.getElementById('btnAgents').click();
  } catch (e) {
    showToast('Failed to create agent', 'error');
  }
}

// Profile Modal
document.getElementById('btnProfile').onclick = () => {
  document.getElementById('profileTheme').textContent = document.documentElement.getAttribute('data-theme') === 'dark' ? 'Dark' : 'Light';
  openModal('profileModal');
};

// Toolbar
document.getElementById('btnNew').addEventListener('click', async () => {
  const r = await fetch('/api/new-session', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ chat_id: currentChatId })
  });
  const data = await r.json();
  await switchSession(data.session_id);
  showToast('New session created!', 'success');
});

document.getElementById('btnReset').addEventListener('click', async () => {
  if (!activeSessionId) return;
  await fetch('/api/clear', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ session_id: activeSessionId })
  });
  chatArea.innerHTML = '';
  emptyState.style.display = 'block';
  showToast('Chat cleared', 'success');
});

document.getElementById('btnSummarize').addEventListener('click', async () => {
  if (!activeSessionId) return;
  statusText.textContent = 'Summarizing...';
  const r = await fetch('/api/summarize', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ session_id: activeSessionId })
  });
  const data = await r.json();
  statusText.textContent = 'Online';
  if (data.summary) {
    const div = document.createElement('div');
    div.className = 'msg bmo';
    div.innerHTML = '<b>📋 Summary</b><br>' + data.summary.slice(0, 500);
    chatArea.appendChild(div);
  }
});

// Input
input.addEventListener('input', () => { sendBtn.disabled = !input.value.trim(); });
input.addEventListener('keydown', (e) => { if (e.key === 'Enter' && !sendBtn.disabled) send(); });
sendBtn.addEventListener('click', send);

let _pollTimer = null;

function send() {
  const text = input.value.trim();
  if (!text) return;
  socket.emit('user_message', { text, session_id: activeSessionId, chat_id: currentChatId, model: currentModel });
  input.value = '';
  sendBtn.disabled = true;
  setStatus('typing');
  _startPollFallback(activeSessionId);
}

function _startPollFallback(sid) {
  if (_pollTimer) clearInterval(_pollTimer);
  let attempts = 0;
  _pollTimer = setInterval(async () => {
    attempts++;
    if (attempts > 20) { clearInterval(_pollTimer); _pollTimer = null; return; }
    try {
      const r = await fetch(`/api/messages?session_id=${sid}&limit=50`);
      const msgs = await r.json();
      let added = false;
      for (const m of msgs) {
        if (document.querySelector(`.msg[data-ts="${m.timestamp}"]`)) continue;
        const div = document.createElement('div');
        div.className = 'msg ' + (m.sender === 'user' ? 'user' : 'bmo');
        div.setAttribute('data-ts', m.timestamp);
        div.innerHTML = renderMessage(m.content, m.reasoning) + `<div class="time">${new Date(m.timestamp * 1000).toLocaleTimeString()}</div>`;
        chatArea.appendChild(div);
        added = true;
        emptyState.style.display = 'none';
      }
      if (added) {
        chatArea.scrollTop = chatArea.scrollHeight;
        lucide.createIcons();
      }
      if (msgs.some(m => m.sender === 'bmo')) {
        const lastMsg = msgs[msgs.length - 1];
        if (lastMsg && lastMsg.sender === 'bmo') {
          clearInterval(_pollTimer); _pollTimer = null;
          setStatus('online');
        }
      }
    } catch (e) {}
  }, 1500);
}

// Helpers
function escapeHtml(str) { const d = document.createElement('div'); d.textContent = str; return d.innerHTML; }

function setStatus(state) {
  statusDot.className = 'status-dot';
  if (state === 'typing') {
    statusDot.classList.add('typing');
    statusText.textContent = 'Typing...';
    typingEl.classList.add('show');
  } else if (state === 'online') {
    statusText.textContent = 'Online';
    typingEl.classList.remove('show');
  } else {
    statusDot.classList.add('offline');
    statusText.textContent = 'Offline';
    typingEl.classList.remove('show');
  }
}

function showToast(message, type = 'success') {
  const existing = document.querySelector('.toast');
  if (existing) {
    existing.classList.add('toast-exit');
    setTimeout(() => existing.remove(), 280);
  }
  setTimeout(() => {
    const toast = document.createElement('div');
    toast.className = `toast ${type}`;
    toast.innerHTML = `
      <i data-lucide="${type === 'success' ? 'check-circle' : 'alert-circle'}"></i>
      <span class="toast-text">${escapeHtml(message)}</span>
    `;
    document.body.appendChild(toast);
    lucide.createIcons();
    setTimeout(() => {
      toast.classList.add('toast-exit');
      setTimeout(() => toast.remove(), 280);
    }, 3000);
  }, 100);
}

// Theme toggle
const themeToggle = document.getElementById('themeToggle');
const html = document.documentElement;
const savedTheme = localStorage.getItem('theme') || 'dark';
html.setAttribute('data-theme', savedTheme);
updateThemeIcon(savedTheme);

themeToggle.onclick = () => {
  const current = html.getAttribute('data-theme');
  const next = current === 'dark' ? 'light' : 'dark';
  html.setAttribute('data-theme', next);
  localStorage.setItem('theme', next);
  updateThemeIcon(next);
};

function updateThemeIcon(theme) {
  const svg = themeToggle.querySelector('svg');
  if (svg) svg.remove();
  const i = document.createElement('i');
  i.setAttribute('data-lucide', theme === 'dark' ? 'sun' : 'moon');
  themeToggle.appendChild(i);
  lucide.createIcons();
}

// Init
initFromUrlParams().then((loaded) => {
  if (!loaded) initFallbackSession();
  updateModelBadge();
}).catch(() => { initFallbackSession(); updateModelBadge(); });
setInterval(loadSessions, 30000);
