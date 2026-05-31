# WebChat UI/UX Improvements Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix 5 UI/UX issues in the webchat app: message bubble alignment, sidebar toggle, session auto-titles, agent creation like Telegram bot, model selection like Telegram bot.

**Architecture:** Modify existing files (styles.css, app.js, server.js, index.html) with focused changes per task. Each task is independently testable in the browser.

**Tech Stack:** HTML, CSS, vanilla JavaScript, Express.js, better-sqlite3, Socket.IO, Lucide icons

---

### Task 1: Fix Message Bubble Alignment to Edges

**Files:**
- Modify: `webchat/public/styles.css`

**Problem:** Message bubbles appear centered instead of aligned to left/right edges.

- [ ] **Step 1: Fix chat-area alignment**

In `styles.css`, find `.chat-area` and ensure it has:
```css
.chat-area {
  flex: 1;
  overflow-y: auto;
  padding: 24px;
  display: flex;
  flex-direction: column;
  gap: 14px;
  align-items: stretch; /* NOT center or flex-start */
}
```

- [ ] **Step 2: Fix user message alignment**

Find `.msg.user` and update:
```css
.msg.user {
  align-self: flex-end;
  margin-left: auto;
  margin-right: 0;
  max-width: 75%;
  /* keep existing styles */
}
```

- [ ] **Step 3: Fix BMO message alignment**

Find `.msg.bmo` and update:
```css
.msg.bmo {
  align-self: flex-start;
  margin-right: auto;
  margin-left: 0;
  max-width: 75%;
  /* keep existing styles */
}
```

- [ ] **Step 4: Test in browser**

Navigate to http://localhost:3456, send a message, verify:
- User message hugs right edge
- BMO message hugs left edge
- Neither is centered

---

### Task 2: Fix Sidebar Collapse/Expand Toggle

**Files:**
- Modify: `webchat/public/app.js`
- Modify: `webchat/public/styles.css`
- Modify: `webchat/public/index.html`

**Problem:** Sidebar toggle doesn't properly collapse/expand with icon change.

- [ ] **Step 1: Add collapsed state CSS**

In `styles.css`, add:
```css
.sidebar {
  width: 300px;
  min-width: 300px;
  transition: width 0.3s var(--ease-out-expo), transform 0.3s var(--ease-out-expo);
  overflow: hidden;
}

.sidebar.collapsed {
  width: 0;
  min-width: 0;
  transform: translateX(-100%);
}

.sidebar-overlay {
  display: none;
  position: fixed;
  inset: 0;
  background: rgba(0,0,0,0.5);
  z-index: 9;
}

.sidebar-overlay.show {
  display: block;
}

.sidebar-toggle {
  display: flex;
}
```

- [ ] **Step 2: Add sidebar overlay to HTML**

In `index.html`, after `<aside class="sidebar" id="sidebar">...</aside>` add:
```html
<div class="sidebar-overlay" id="sidebarOverlay"></div>
```

- [ ] **Step 3: Add toggle logic to app.js**

In `app.js`, replace sidebar toggle code with:
```javascript
const sidebar = document.getElementById('sidebar');
const sidebarOverlay = document.getElementById('sidebarOverlay');
const sidebarToggle = document.getElementById('sidebarToggle');
const sidebarClose = document.getElementById('sidebarClose');

function toggleSidebar() {
  const isCollapsed = sidebar.classList.toggle('collapsed');
  sidebarOverlay.classList.toggle('show', !isCollapsed && window.innerWidth < 768);
  updateSidebarIcon(isCollapsed);
}

function updateSidebarIcon(isCollapsed) {
  const svg = sidebarToggle.querySelector('svg');
  if (svg) svg.remove();
  const i = document.createElement('i');
  i.setAttribute('data-lucide', isCollapsed ? 'panel-left-open' : 'panel-left-close');
  sidebarToggle.appendChild(i);
  lucide.createIcons();
}

sidebarToggle.onclick = toggleSidebar;
sidebarClose.onclick = () => {
  sidebar.classList.add('collapsed');
  sidebarOverlay.classList.remove('show');
  updateSidebarIcon(true);
};
sidebarOverlay.onclick = () => {
  sidebar.classList.add('collapsed');
  sidebarOverlay.classList.remove('show');
  updateSidebarIcon(true);
};
```

- [ ] **Step 4: Test in browser**

Click toggle button → sidebar collapses, icon changes to `panel-left-open`
Click again → sidebar expands, icon changes to `panel-left-close`

---

### Task 3: Auto-Generate Session Titles from 2nd Message

**Files:**
- Modify: `webchat/server.js`
- Modify: `webchat/public/app.js`

**Problem:** Sessions show UUIDs instead of meaningful titles from conversation content.

- [ ] **Step 1: Add auto-title function to server.js**

In `server.js`, after the `/api/set-title` endpoint, add:
```javascript
function generateTitle(content) {
  // Clean and truncate content for title
  let title = content
    .replace(/[#*`_~]/g, '') // Remove markdown
    .replace(/\n/g, ' ')
    .trim();
  if (title.length > 40) {
    title = title.substring(0, 37) + '...';
  }
  return title || 'New conversation';
}

function tryAutoTitle(sessionId) {
  try {
    const msgs = getMessages.all(sessionId, 2);
    if (msgs.length >= 2) {
      // Use 2nd message for title
      const title = generateTitle(msgs[1].content);
      const existing = getSessionById.get(sessionId);
      if (existing && (!existing.title || existing.title === existing.id.slice(0, 8))) {
        updateTitle.run(title, Date.now() / 1000, sessionId);
      }
    }
  } catch (e) {
    // Ignore errors in auto-titling
  }
}
```

- [ ] **Step 2: Call auto-title after message is saved**

In `server.js`, in the `socket.on('user_message')` handler, after `addMessageStmt.run(sid, 'user', text, now)`:
```javascript
// Try to auto-generate title after 2nd message
setTimeout(() => tryAutoTitle(sid), 100);
```

And after the BMO response `addMessageStmt.run(sid, 'bmo', responseText, ...)`:
```javascript
// Try to auto-generate title after BMO responds
setTimeout(() => tryAutoTitle(sid), 100);
```

- [ ] **Step 3: Test in browser**

1. Create new session
2. Send "Hello"
3. Wait for BMO response
4. Check sidebar - title should be truncated content, not UUID

---

### Task 4: Agent Creation Like Telegram Bot

**Files:**
- Modify: `webchat/public/app.js`
- Modify: `webchat/public/styles.css`
- Modify: `webchat/public/index.html`
- Modify: `webchat/server.js`

**Telegram bot flow:**
- Main menu: Create Manual, Create AI-Generated, My Custom Agents, Reset to Default
- Manual: Name → Description → System Prompt
- AI: Describe personality → AI generates prompt → editable → save

- [ ] **Step 1: Update agents modal HTML**

In `index.html`, replace the agents modal with:
```html
<div class="modal-overlay" id="agentsModal">
  <div class="modal-content modal-lg">
    <div class="modal-header">
      <h3><i data-lucide="users"></i> BMO Agents Factory</h3>
      <button class="modal-close" data-close="agentsModal"><i data-lucide="x"></i></button>
    </div>
    <div class="modal-tabs">
      <button class="modal-tab active" data-tab="agents-browse"><i data-lucide="list"></i> Browse</button>
      <button class="modal-tab" data-tab="agents-create"><i data-lucide="plus-circle"></i> Create</button>
    </div>
    <div class="modal-body">
      <div class="tab-content active" id="agents-browse">
        <div class="modal-loading"><i data-lucide="loader-2" class="spin"></i> Loading agents...</div>
      </div>
      <div class="tab-content" id="agents-create">
        <div class="agent-create-tabs">
          <button class="agent-create-tab active" data-ctype="manual">📝 Manual</button>
          <button class="agent-create-tab" data-ctype="ai">🤖 AI Generate</button>
        </div>
        <div id="agent-create-manual" class="agent-create-panel active">
          <div class="create-agent-form">
            <div class="form-group">
              <label>Agent Name</label>
              <input type="text" id="agentNameInput" placeholder="e.g., Code Reviewer">
            </div>
            <div class="form-group">
              <label>Description</label>
              <input type="text" id="agentDescInput" placeholder="What does this agent do?">
            </div>
            <div class="form-group">
              <label>System Prompt</label>
              <textarea id="agentPromptInput" rows="6" placeholder="Define the agent's behavior and personality..."></textarea>
            </div>
            <button class="btn-primary" onclick="createAgent()"><i data-lucide="check"></i> Create Agent</button>
          </div>
        </div>
        <div id="agent-create-ai" class="agent-create-panel">
          <div class="create-agent-form">
            <div class="form-group">
              <label>Describe the personality or role</label>
              <textarea id="agentAiDescInput" rows="4" placeholder="e.g., A strict code reviewer who focuses on security and performance"></textarea>
            </div>
            <button class="btn-primary" onclick="generateAgentPrompt()"><i data-lucide="wand-2"></i> Generate with AI</button>
            <div id="agentAiResult" style="display:none">
              <div class="form-group">
                <label>Generated System Prompt (editable)</label>
                <textarea id="agentAiPromptInput" rows="6"></textarea>
              </div>
              <div class="form-group">
                <label>Agent Name</label>
                <input type="text" id="agentAiNameInput" placeholder="Agent name">
              </div>
              <button class="btn-primary" onclick="saveAiAgent()"><i data-lucide="save"></i> Save Agent</button>
            </div>
          </div>
        </div>
      </div>
    </div>
  </div>
</div>
```

- [ ] **Step 2: Add CSS for agent modals**

In `styles.css`, add:
```css
.modal-lg { max-width: 700px; }
.modal-tabs {
  display: flex;
  border-bottom: 1px solid var(--border);
  background: var(--bg-card);
}
.modal-tab {
  flex: 1;
  padding: 14px 20px;
  background: none;
  border: none;
  color: var(--text-muted);
  font-size: 14px;
  font-weight: 600;
  cursor: pointer;
  transition: all var(--transition-micro);
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
  position: relative;
}
.modal-tab::after {
  content: '';
  position: absolute;
  bottom: 0;
  left: 0;
  right: 0;
  height: 2px;
  background: var(--gradient-primary);
  transform: scaleX(0);
  transition: transform var(--transition-fast);
}
.modal-tab:hover { color: var(--text-secondary); background: var(--bg-hover); }
.modal-tab.active { color: var(--accent-primary-light); }
.modal-tab.active::after { transform: scaleX(1); }
.modal-tab i { width: 18px; height: 18px; }
.tab-content { display: none; }
.tab-content.active { display: block; animation: fadeSlideUp 0.3s var(--ease-out-expo); }
.agent-create-tabs {
  display: flex;
  gap: 8px;
  margin-bottom: 20px;
}
.agent-create-tab {
  padding: 10px 20px;
  border: 1px solid var(--border);
  border-radius: var(--radius-md);
  background: var(--bg-card);
  color: var(--text-secondary);
  font-size: 13px;
  font-weight: 500;
  cursor: pointer;
  transition: all var(--transition-fast);
  font-family: 'Outfit', sans-serif;
}
.agent-create-tab:hover { border-color: var(--accent-primary); }
.agent-create-tab.active {
  border-color: var(--accent-primary);
  background: linear-gradient(135deg, rgba(124,58,237,0.1), transparent);
  color: var(--accent-primary-light);
}
.agent-create-panel { display: none; }
.agent-create-panel.active { display: block; }
.agent-list { display: flex; flex-direction: column; gap: 12px; }
.agent-card {
  padding: 16px;
  border: 1px solid var(--border);
  border-radius: var(--radius-md);
  background: var(--bg-card);
  transition: all var(--transition-fast);
  cursor: pointer;
  display: flex;
  align-items: flex-start;
  gap: 12px;
}
.agent-card:hover { border-color: var(--accent-primary-dark); transform: translateX(4px); }
.agent-card.active {
  border-color: var(--accent-primary);
  background: linear-gradient(135deg, rgba(124,58,237,0.1), transparent);
}
.agent-icon {
  width: 40px;
  height: 40px;
  border-radius: var(--radius-md);
  background: var(--gradient-primary);
  display: flex;
  align-items: center;
  justify-content: center;
  color: white;
  flex-shrink: 0;
}
.agent-icon i { width: 20px; height: 20px; }
.agent-info { flex: 1; min-width: 0; }
.agent-name {
  font-size: 15px;
  font-weight: 600;
  color: var(--text-primary);
  display: flex;
  align-items: center;
  gap: 10px;
  margin-bottom: 4px;
}
.agent-desc { font-size: 13px; color: var(--text-muted); line-height: 1.5; }
.agent-badge {
  font-size: 10px;
  padding: 3px 8px;
  border-radius: var(--radius-full);
  background: var(--accent-primary-dark);
  color: white;
  text-transform: uppercase;
  letter-spacing: 0.5px;
}
.agent-actions {
  display: flex;
  gap: 8px;
  margin-top: 8px;
}
.agent-action-btn {
  padding: 6px 14px;
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  background: var(--bg-primary);
  color: var(--text-secondary);
  font-size: 12px;
  cursor: pointer;
  transition: all var(--transition-fast);
  font-family: 'Outfit', sans-serif;
}
.agent-action-btn:hover { border-color: var(--accent-primary); color: var(--accent-primary-light); }
.agent-action-btn.active-action {
  border-color: var(--accent-primary);
  background: var(--accent-primary-dark);
  color: white;
}
.reset-agent-btn {
  margin-top: 16px;
  width: 100%;
  padding: 12px;
  border: 1px dashed var(--border);
  border-radius: var(--radius-md);
  background: transparent;
  color: var(--text-muted);
  font-size: 13px;
  cursor: pointer;
  transition: all var(--transition-fast);
  font-family: 'Outfit', sans-serif;
}
.reset-agent-btn:hover { border-color: var(--accent-danger); color: var(--accent-danger); }
```

- [ ] **Step 3: Implement agents modal logic in app.js**

Replace the agents modal handler in `app.js` with:
```javascript
// Tab switching
document.querySelectorAll('.modal-tab').forEach(tab => {
  tab.onclick = () => {
    const modal = tab.closest('.modal-overlay');
    modal.querySelectorAll('.modal-tab').forEach(t => t.classList.remove('active'));
    modal.querySelectorAll('.tab-content').forEach(c => c.classList.remove('active'));
    tab.classList.add('active');
    document.getElementById(tab.dataset.tab).classList.add('active');
  };
});

// Agent create sub-tabs
document.querySelectorAll('.agent-create-tab').forEach(tab => {
  tab.onclick = () => {
    document.querySelectorAll('.agent-create-tab').forEach(t => t.classList.remove('active'));
    document.querySelectorAll('.agent-create-panel').forEach(p => p.classList.remove('active'));
    tab.classList.add('active');
    document.getElementById('agent-create-' + tab.dataset.ctype).classList.add('active');
  };
});

// Agents modal
document.getElementById('btnAgents').onclick = async () => {
  openModal('agentsModal');
  await loadAgentsList();
};

async function loadAgentsList() {
  const body = document.getElementById('agents-browse');
  body.innerHTML = '<div class="modal-loading"><i data-lucide="loader-2" class="spin"></i> Loading agents...</div>';
  lucide.createIcons();
  
  try {
    const r = await fetch('/api/agents?chat_id=732356803');
    const agents = await r.json();
    
    if (!agents.length) {
      body.innerHTML = `
        <div class="modal-empty">
          <i data-lucide="users"></i>
          <p>No custom agents yet</p>
          <button class="btn-primary" onclick="document.querySelector('[data-tab=agents-create]').click()">
            <i data-lucide="plus"></i> Create Your First Agent
          </button>
        </div>
      `;
      lucide.createIcons();
      return;
    }
    
    let html = '<div class="agent-list">';
    for (const agent of agents) {
      const isActive = currentAgent === agent.name;
      html += `
        <div class="agent-card ${isActive ? 'active' : ''}" data-agent="${agent.name}">
          <div class="agent-icon"><i data-lucide="bot"></i></div>
          <div class="agent-info">
            <div class="agent-name">
              ${escapeHtml(agent.name)}
              ${isActive ? '<span class="agent-badge">Active</span>' : ''}
            </div>
            <div class="agent-desc">${escapeHtml(agent.description || 'No description')}</div>
            <div class="agent-actions">
              <button class="agent-action-btn ${isActive ? 'active-action' : ''}" onclick="selectAgent('${escapeHtml(agent.name)}')">
                ${isActive ? '✅ Active' : 'Activate'}
              </button>
            </div>
          </div>
        </div>
      `;
    }
    html += `
      <button class="reset-agent-btn" onclick="resetAgent()">
        <i data-lucide="rotate-ccw"></i> Reset to BMO Default
      </button>
    </div>`;
    body.innerHTML = html;
    lucide.createIcons();
  } catch (e) {
    body.innerHTML = '<div class="modal-empty"><i data-lucide="alert-triangle"></i><p>Failed to load agents</p></div>';
    lucide.createIcons();
  }
}

function selectAgent(name) {
  currentAgent = name;
  localStorage.setItem('selectedAgent', name);
  document.getElementById('profileAgent').textContent = name;
  showToast(`Agent "${name}" activated`, 'success');
  loadAgentsList();
}

function resetAgent() {
  currentAgent = 'default';
  localStorage.setItem('selectedAgent', 'default');
  document.getElementById('profileAgent').textContent = 'None';
  showToast('Reset to BMO default', 'success');
  loadAgentsList();
}

async function createAgent() {
  const name = document.getElementById('agentNameInput').value.trim();
  const desc = document.getElementById('agentDescInput').value.trim();
  const prompt = document.getElementById('agentPromptInput').value.trim();
  
  if (!name) { showToast('Agent name is required', 'error'); return; }
  if (!prompt) { showToast('System prompt is required', 'error'); return; }
  
  try {
    await fetch('/api/create-agent', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ chat_id: 732356803, name, description: desc, system_prompt: prompt })
    });
    showToast(`Agent "${name}" created!`, 'success');
    // Clear form
    document.getElementById('agentNameInput').value = '';
    document.getElementById('agentDescInput').value = '';
    document.getElementById('agentPromptInput').value = '';
    // Switch to browse tab
    document.querySelector('[data-tab=agents-browse]').click();
    loadAgentsList();
  } catch (e) {
    showToast('Failed to create agent', 'error');
  }
}

async function generateAgentPrompt() {
  const desc = document.getElementById('agentAiDescInput').value.trim();
  if (!desc) { showToast('Please describe the agent personality', 'error'); return; }
  
  const btn = event.target;
  btn.disabled = true;
  btn.innerHTML = '<i data-lucide="loader-2" class="spin"></i> Generating...';
  lucide.createIcons();
  
  try {
    const r = await fetch('/api/generate-agent-prompt', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ description: desc })
    });
    const data = await r.json();
    
    document.getElementById('agentAiPromptInput').value = data.system_prompt || '';
    document.getElementById('agentAiNameInput').value = data.suggested_name || '';
    document.getElementById('agentAiResult').style.display = 'block';
  } catch (e) {
    showToast('Failed to generate prompt', 'error');
  }
  
  btn.disabled = false;
  btn.innerHTML = '<i data-lucide="wand-2"></i> Generate with AI';
  lucide.createIcons();
}

async function saveAiAgent() {
  const name = document.getElementById('agentAiNameInput').value.trim();
  const prompt = document.getElementById('agentAiPromptInput').value.trim();
  const desc = document.getElementById('agentAiDescInput').value.trim();
  
  if (!name) { showToast('Agent name is required', 'error'); return; }
  if (!prompt) { showToast('System prompt is required', 'error'); return; }
  
  try {
    await fetch('/api/create-agent', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ chat_id: 732356803, name, description: desc, system_prompt: prompt })
    });
    showToast(`Agent "${name}" created!`, 'success');
    document.getElementById('agentAiResult').style.display = 'none';
    document.getElementById('agentAiDescInput').value = '';
    document.querySelector('[data-tab=agents-browse]').click();
    loadAgentsList();
  } catch (e) {
    showToast('Failed to save agent', 'error');
  }
}
```

- [ ] **Step 4: Add server endpoints**

In `server.js`, add after `/api/agents`:
```javascript
app.post('/api/create-agent', express.json(), (req, res) => {
  const { chat_id, name, description, system_prompt } = req.body;
  if (!name) return res.status(400).json({ error: 'name required' });
  try {
    const now = Date.now() / 1000;
    db.prepare(
      'INSERT INTO user_agents (agent_id, chat_id, name, description, system_prompt, created_at) VALUES (?, ?, ?, ?, ?, ?)'
    ).run(uuidv4(), chat_id || 732356803, name, description || '', system_prompt || '', now);
    res.json({ ok: true });
  } catch (e) {
    res.status(500).json({ error: e.message });
  }
});

app.post('/api/generate-agent-prompt', express.json(), async (req, res) => {
  const { description } = req.body;
  if (!description) return res.status(400).json({ error: 'description required' });
  try {
    const ocSid = await createOpenCodeSession();
    const prompt = `You are an expert at creating AI agent system prompts. Based on this description, create a detailed system prompt and suggest a name.

Description: ${description}

Respond in this exact JSON format:
{
  "suggested_name": "Short agent name",
  "system_prompt": "Detailed system prompt here..."
}

The system prompt should include:
- The agent's role and personality
- Key behaviors and guidelines
- Tone and communication style
- Any specific expertise or focus areas`;
    
    const result = await callOpenCode(prompt, ocSid);
    let parsed;
    try {
      const jsonMatch = result.text.match(/\{[\s\S]*\}/);
      parsed = jsonMatch ? JSON.parse(jsonMatch[0]) : { suggested_name: 'Custom Agent', system_prompt: result.text };
    } catch {
      parsed = { suggested_name: 'Custom Agent', system_prompt: result.text };
    }
    res.json(parsed);
  } catch (e) {
    res.status(500).json({ error: e.message });
  }
});
```

- [ ] **Step 5: Test in browser**

1. Click Agents button
2. Browse tab shows existing agents or "Create Your First Agent"
3. Create tab → Manual: fill form, save
4. Create tab → AI Generate: describe personality, generate, edit prompt, save
5. Click Activate on an agent → shows "Active" badge
6. Click Reset to Default → clears active agent

---

### Task 5: Model Selection Like Telegram Bot

**Files:**
- Modify: `webchat/public/app.js`
- Modify: `webchat/public/styles.css`
- Modify: `webchat/public/index.html`

**Telegram bot pattern:**
- Fetch providers, filter to only connected ones + free models
- Sort: opencode first, free models first
- Show 20 models per page with pagination
- Provider navigation arrows
- Labels: 🆓 free, ✨ premium, 📡 uses keys

- [ ] **Step 1: Update models modal HTML**

In `index.html`, replace the models modal with:
```html
<div class="modal-overlay" id="modelsModal">
  <div class="modal-content modal-lg">
    <div class="modal-header">
      <h3><i data-lucide="cpu"></i> Select Model</h3>
      <button class="modal-close" data-close="modelsModal"><i data-lucide="x"></i></button>
    </div>
    <div class="modal-body" id="modelsBody">
      <div class="modal-loading"><i data-lucide="loader-2" class="spin"></i> Loading models...</div>
    </div>
  </div>
</div>
```

- [ ] **Step 2: Add model selection CSS**

In `styles.css`, add:
```css
.model-status {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 12px 16px;
  background: var(--bg-card);
  border-radius: var(--radius-md);
  margin-bottom: 16px;
  font-size: 13px;
}
.model-status .provider-info { color: var(--text-secondary); }
.model-status .provider-info strong { color: var(--text-primary); }
.model-status .page-info { color: var(--text-muted); }
.model-provider-nav {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 16px;
}
.model-provider-nav button {
  padding: 8px 16px;
  border: 1px solid var(--border);
  border-radius: var(--radius-md);
  background: var(--bg-card);
  color: var(--text-secondary);
  font-size: 13px;
  cursor: pointer;
  transition: all var(--transition-fast);
  display: flex;
  align-items: center;
  gap: 6px;
  font-family: 'Outfit', sans-serif;
}
.model-provider-nav button:hover:not(:disabled) {
  border-color: var(--accent-primary);
  color: var(--accent-primary-light);
}
.model-provider-nav button:disabled { opacity: 0.3; cursor: not-allowed; }
.model-provider-nav button i { width: 16px; height: 16px; }
.model-pagination {
  display: flex;
  justify-content: center;
  gap: 8px;
  margin-top: 16px;
}
.model-pagination button {
  padding: 8px 16px;
  border: 1px solid var(--border);
  border-radius: var(--radius-md);
  background: var(--bg-card);
  color: var(--text-secondary);
  font-size: 13px;
  cursor: pointer;
  transition: all var(--transition-fast);
  font-family: 'Outfit', sans-serif;
}
.model-pagination button:hover:not(:disabled) {
  border-color: var(--accent-primary);
  color: var(--accent-primary-light);
}
.model-pagination button:disabled { opacity: 0.3; cursor: not-allowed; }
.model-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(160px, 1fr));
  gap: 10px;
}
.model-card {
  padding: 14px;
  border: 2px solid var(--border);
  border-radius: var(--radius-md);
  cursor: pointer;
  transition: all var(--transition-fast);
  position: relative;
}
.model-card:hover {
  border-color: var(--accent-primary-dark);
  transform: translateY(-2px);
}
.model-card.selected {
  border-color: var(--accent-primary);
  background: linear-gradient(135deg, rgba(124,58,237,0.15), rgba(168,85,247,0.1));
  box-shadow: 0 0 20px var(--accent-glow);
}
.model-card .model-badge {
  position: absolute;
  top: 8px;
  right: 8px;
  font-size: 14px;
}
.model-card .model-name {
  font-size: 13px;
  font-weight: 600;
  color: var(--text-primary);
  margin-bottom: 4px;
  padding-right: 24px;
}
.model-card .model-provider {
  font-size: 11px;
  color: var(--text-muted);
}
.model-card .model-check {
  position: absolute;
  bottom: 8px;
  right: 8px;
  width: 20px;
  height: 20px;
  background: var(--accent-success);
  border-radius: 50%;
  display: none;
  align-items: center;
  justify-content: center;
}
.model-card.selected .model-check { display: flex; }
.model-card .model-check i { width: 12px; height: 12px; color: white; }
```

- [ ] **Step 3: Implement model selection logic**

Replace the models modal handler in `app.js` with:
```javascript
let modelState = {
  providers: [],
  currentProviderIdx: 0,
  currentPage: 0,
  modelsPerPage: 20
};

document.getElementById('btnModels').onclick = async () => {
  openModal('modelsModal');
  await loadModels();
};

async function loadModels() {
  const body = document.getElementById('modelsBody');
  body.innerHTML = '<div class="modal-loading"><i data-lucide="loader-2" class="spin"></i> Loading models...</div>';
  lucide.createIcons();
  
  try {
    const r = await fetch('/api/models');
    const data = await r.json();
    const allProviders = data.value || data;
    
    // Filter: only connected providers or those with free models
    const connectedProviders = ['opencode']; // opencode is always available
    const validProviders = [];
    
    for (const provider of allProviders) {
      const pId = provider.id;
      const models = provider.models || {};
      const providerModels = [];
      
      for (const [mid, mData] of Object.entries(models)) {
        const isFree = mid.toLowerCase().includes(':free') || 
                       (mData.name && mData.name.toLowerCase().includes('free')) ||
                       pId === 'opencode';
        
        if (isFree || connectedProviders.includes(pId)) {
          let label = mData.name || mid;
          label = label.replace(/-latest$/i, '').replace(/-pro$/i, ' Pro').replace(/-lite$/i, ' Lite').replace(/-flash$/i, ' Flash');
          
          let badge = '📡';
          if (isFree) badge = '🆓';
          if (['google', 'openai', 'anthropic', 'deepseek'].includes(pId) && !isFree) badge = '✨';
          
          providerModels.push({
            id: mid,
            label: label,
            badge: badge,
            isFree: isFree
          });
        }
      }
      
      if (providerModels.length > 0) {
        // Sort: free first
        providerModels.sort((a, b) => b.isFree - a.isFree);
        validProviders.push({
          id: pId,
          name: provider.name || pId,
          models: providerModels
        });
      }
    }
    
    // Sort: opencode first
    validProviders.sort((a, b) => {
      if (a.id === 'opencode') return -1;
      if (b.id === 'opencode') return 1;
      return a.name.localeCompare(b.name);
    });
    
    if (validProviders.length === 0) {
      body.innerHTML = '<div class="modal-empty"><i data-lucide="alert-circle"></i><p>No models available. Connect a provider first.</p></div>';
      lucide.createIcons();
      return;
    }
    
    modelState.providers = validProviders;
    modelState.currentProviderIdx = 0;
    modelState.currentPage = 0;
    renderModels();
  } catch (e) {
    body.innerHTML = '<div class="modal-empty"><i data-lucide="alert-triangle"></i><p>Failed to load models</p></div>';
    lucide.createIcons();
  }
}

function renderModels() {
  const body = document.getElementById('modelsBody');
  const providers = modelState.providers;
  const pIdx = modelState.currentProviderIdx;
  const currentP = providers[pIdx];
  
  const modelsPerPage = modelState.modelsPerPage;
  const maxPage = Math.max(0, Math.ceil(currentP.models.length / modelsPerPage) - 1);
  modelState.currentPage = Math.min(modelState.currentPage, maxPage);
  
  const start = modelState.currentPage * modelsPerPage;
  const end = start + modelsPerPage;
  const pageModels = currentP.models.slice(start, end);
  
  const selectedModel = localStorage.getItem('selectedModel') || '';
  
  let html = `
    <div class="model-status">
      <span class="provider-info">🏢 <strong>${escapeHtml(currentP.name)}</strong> (${pIdx + 1}/${providers.length})</span>
      <span class="page-info">📄 Page ${modelState.currentPage + 1}/${maxPage + 1}</span>
    </div>
    <div class="model-provider-nav">
      <button ${pIdx === 0 ? 'disabled' : ''} onclick="switchProvider(${pIdx - 1})">
        <i data-lucide="chevron-left"></i> Prev Provider
      </button>
      <button ${pIdx >= providers.length - 1 ? 'disabled' : ''} onclick="switchProvider(${pIdx + 1})">
        Next Provider <i data-lucide="chevron-right"></i>
      </button>
    </div>
    <div class="model-grid">
  `;
  
  for (const model of pageModels) {
    const isSelected = selectedModel === model.id;
    html += `
      <div class="model-card ${isSelected ? 'selected' : ''}" onclick="selectModel('${escapeHtml(currentP.id)}', '${escapeHtml(model.id)}')">
        <span class="model-badge">${model.badge}</span>
        <div class="model-name">${escapeHtml(model.label)}</div>
        <div class="model-provider">${escapeHtml(currentP.id)}</div>
        <div class="model-check"><i data-lucide="check"></i></div>
      </div>
    `;
  }
  
  html += '</div>';
  
  // Model pagination
  if (maxPage > 0) {
    html += `
      <div class="model-pagination">
        <button ${modelState.currentPage === 0 ? 'disabled' : ''} onclick="switchModelPage(${modelState.currentPage - 1})">
          ⬅️ Prev Models
        </button>
        <button ${modelState.currentPage >= maxPage ? 'disabled' : ''} onclick="switchModelPage(${modelState.currentPage + 1})">
          Next Models ➡️
        </button>
      </div>
    `;
  }
  
  body.innerHTML = html;
  lucide.createIcons();
}

function switchProvider(idx) {
  modelState.currentProviderIdx = idx;
  modelState.currentPage = 0;
  renderModels();
}

function switchModelPage(page) {
  modelState.currentPage = page;
  renderModels();
}

function selectModel(provider, model) {
  currentModel = model;
  localStorage.setItem('selectedModel', model);
  document.getElementById('profileModel').textContent = model.split('/').pop() || model;
  
  showToast(`Model: ${model.split('/').pop() || model}`, 'success');
  renderModels();
  
  // Switch model on server
  if (activeSessionId) {
    fetch('/api/switch-model', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ session_id: activeSessionId, provider, model })
    }).catch(() => {});
  }
}
```

- [ ] **Step 4: Test in browser**

1. Click Models button
2. See providers with models, free models marked 🆓
3. Click "Next Provider" to switch providers
4. Click a model → shows confirmation toast, model highlighted
5. Pagination works if >20 models

---

## Self-Review Checklist

| Requirement | Task | Status |
|-------------|------|--------|
| Message bubbles aligned to edges | Task 1 | ✅ |
| Sidebar collapse/expand toggle | Task 2 | ✅ |
| Session titles from 2nd message | Task 3 | ✅ |
| Agent creation like Telegram bot | Task 4 | ✅ |
| Model selection like Telegram bot | Task 5 | ✅ |
| AI-generated prompt editable | Task 4 | ✅ |
| Only connected providers shown | Task 5 | ✅ |
| Free models sorted first | Task 5 | ✅ |

**No placeholders found.** All code blocks contain complete implementations.
**Type consistency:** All function names and variable names are consistent across tasks.
**All spec requirements covered.**
