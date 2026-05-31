# Webchat Inline Launch & Session Continuity — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** One-click "Chat on Web" inline button that deploys webchat in the background without freezing the bot, with session continuity so the webchat shows the user's existing Telegram conversation.

**Architecture:** Fix blocking `time.sleep()` by adding an async poller. Add dynamic inline menu that shows "Chat on Web" or "Stop Webchat" based on port status. Add `/api/set-session` endpoint on the webchat server for session continuity. Frontend loads past messages via URL params.

**Tech Stack:** Python (python-telegram-bot, asyncio), Node.js (Express, Socket.IO, better-sqlite3)

---

### Task 1: Non-Blocking Tunnel Polling

**Files:**
- Modify: `tools/mcp_server.py:53-67`

- [ ] **Step 1: Write failing test for async poller**

File: `tests/test_mcp_poller.py`

```python
"""
Tests for tools/mcp_server.py — async tunnel URL polling.
"""

import os
import sys
import tempfile
import unittest
import asyncio
from unittest.mock import patch

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, PROJECT_ROOT)

import tools.mcp_server as mcp


class TestPollLogForUrlAsync(unittest.TestCase):

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.log_file = os.path.join(self.temp_dir, "tunnel.log")

    def tearDown(self):
        if os.path.exists(self.log_file):
            os.remove(self.log_file)
        if os.path.exists(self.temp_dir):
            os.rmdir(self.temp_dir)

    def test_returns_none_when_no_url(self):
        with open(self.log_file, "w") as f:
            f.write("some log content without a url")
        result = asyncio.run(mcp._poll_log_for_url_async(self.log_file, timeout=5))
        self.assertIsNone(result)

    def test_returns_url_when_found(self):
        with open(self.log_file, "w") as f:
            f.write("hello https://abc-123.trycloudflare.com world")
        result = asyncio.run(mcp._poll_log_for_url_async(self.log_file, timeout=5))
        self.assertEqual(result, "https://abc-123.trycloudflare.com")

    def test_timeout_returns_none(self):
        # No log file at all — poller should timeout and return None
        result = asyncio.run(mcp._poll_log_for_url_async("/nonexistent/file.log", timeout=3))
        self.assertIsNone(result)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m unittest tests.test_mcp_poller -v`

Expected: FAIL with `AttributeError: module 'tools.mcp_server' has no attribute '_poll_log_for_url_async'`

- [ ] **Step 3: Add async version to `tools/mcp_server.py`**

After the sync `_poll_log_for_url` (line 67), add:

```python
async def _poll_log_for_url_async(log_file: str, timeout: int = 75) -> str | None:
    """Async version — uses asyncio.sleep instead of time.sleep. Returns URL or None."""
    start = time.time()
    while time.time() - start < timeout:
        await asyncio.sleep(2)
        if os.path.exists(log_file):
            try:
                with open(log_file, "r", encoding="utf-8", errors="ignore") as f:
                    content = f.read()
                    match = re.search(r"https://[a-zA-Z0-9-]+\.trycloudflare\.com", content)
                    if match:
                        return match.group(0)
            except IOError:
                pass
    return None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m unittest tests.test_mcp_poller -v`

Expected: All 3 tests PASS

- [ ] **Step 5: Run existing tests to verify no regression**

Run: `python -m unittest tests.test_task_registry -v`

Expected: All existing tests PASS

- [ ] **Step 6: Commit**

```bash
git add tools/mcp_server.py tests/test_mcp_poller.py
git commit -m "feat: add async tunnel URL poller with 75s timeout"
```

---

### Task 2: Webchat `/api/set-session` Endpoint

**Files:**
- Modify: `webchat/server.js` (add endpoint)

- [ ] **Step 1: Add `POST /api/set-session` to `webchat/server.js`**

After the `/api/sessions` endpoint (around line 60), add:

```javascript
app.post('/api/set-session', (req, res) => {
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
    const ocRow = getOpenCodeSessionId.get(session_id);
    const opencode_session_id = ocRow ? ocRow.opencode_session_id : null;
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
```

- [ ] **Step 2: Manually verify the endpoint responds**

Start the webchat server: `node webchat/server.js`

Test the endpoint:
```bash
curl -X POST http://localhost:3456/api/set-session ^
  -H "Content-Type: application/json" ^
  -d "{\"chat_id\": 732356803, \"session_id\": \"test-id\"}"
```

Expected: `{"opencode_session_id":null,"session":{"id":"test-id","title":"","chat_id":732356803}}` or 404

- [ ] **Step 3: Commit**

```bash
git add webchat/server.js
git commit -m "feat: add /api/set-session endpoint for session continuity"
```

---

### Task 3: Webchat Frontend Session Loading from URL Params

**Files:**
- Modify: `webchat/public/app.js`

- [ ] **Step 1: Add URL param parsing and session loading at init**

Replace the init section at the bottom of `app.js` (lines 735-737):

```javascript
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
        activeSessionId = session_id;
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

// Init
initFromUrlParams().catch(() => {});
setInterval(loadSessions, 30000);
```

- [ ] **Step 2: Add dynamic `currentChatId` and use it in API calls**

In `app.js`, add a `currentChatId` variable alongside `activeSessionId`:

```javascript
let sessions = [];
let activeSessionId = null;
let currentChatId = 732356803;  // default fallback
let disconnectTimer = null;
```

In `initFromUrlParams()` (from Step 1), set `currentChatId`:
```javascript
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
        activeSessionId = session_id;
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
```

Replace all hardcoded `732356803` with `currentChatId` in these functions:
- `loadSessions()` — `/api/sessions?chat_id=${currentChatId}`
- `switchSession(sid)` — include `chat_id: currentChatId` in POST body
- `btnNew` click handler — include `chat_id: currentChatId` in POST body

- [ ] **Step 3: Update webchat server endpoints to accept optional chat_id**

In `webchat/server.js`, modify the `/api/sessions` endpoint (line 45-46) to accept `chat_id` or fall back:

```javascript
app.get('/api/sessions', (req, res) => {
  const chatId = parseInt(req.query.chat_id) || 0;
  try {
    // If no chat_id provided, return sessions for the first available chat
    const allChats = db.prepare('SELECT DISTINCT chat_id FROM sessions LIMIT 1').get();
    const effectiveChatId = chatId || (allChats ? allChats.chat_id : 732356803);
    const rows = listSessions.all(effectiveChatId, effectiveChatId);
    const activeRow = getActiveSession.get(effectiveChatId);
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

- [ ] **Step 4: Verify webchat still works**

Start: `node webchat/server.js`

Visit `http://localhost:3456` — should load normally without the `732356803` hardcode.

Visit `http://localhost:3456/?chat_id=732356803&session_id=<existing-session-id>` — should load that session's messages.

- [ ] **Step 5: Commit**

```bash
git add webchat/public/app.js webchat/server.js
git commit -m "feat: webchat loads session from URL params, removes hardcoded chat_id"
```

---

### Task 4: Dynamic Inline Menu

**Files:**
- Modify: `handlers/messages.py:121-173`

- [ ] **Step 1: Modify `build_inline_menu()` to check port status**

Change the "Web chat" row (lines 162-165) to be dynamic:

```python
    # Row 6: Web chat (dynamic — shows launch or stop based on port status)
    from tools.task_registry import check_port_conflict
    conflict = check_port_conflict(3456)
    webchat_btn = (
        InlineKeyboardButton("⏹ Stop Webchat", callback_data="action:stop_webchat")
        if conflict
        else InlineKeyboardButton("🌐 Chat on Web", callback_data="action:launch_webchat")
    )
    kb.append([webchat_btn])
```

- [ ] **Step 2: Add `action:stop_webchat` route in `inline_action_callback`**

In the dispatcher (after line 323), add:

```python
    elif data == "action:stop_webchat":
        await _handle_stop_webchat(update, context)
        return
```

- [ ] **Step 3: Implement `_handle_stop_webchat()`**

Add before `_handle_launch_webchat` (around line 480):

```python
async def _handle_stop_webchat(update, context):
    """Stop the running webchat server and its tunnel."""
    from tools.task_registry import get_active_tasks, remove_task, check_port_conflict
    import psutil

    query = update.callback_query
    await query.edit_message_text(
        "⏹ <b>Stopping Webchat...</b>",
        parse_mode=ParseMode.HTML,
    )

    conflict = check_port_conflict(3456)
    if not conflict:
        await query.edit_message_text(
            "⚠️ <b>No webchat running.</b>",
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data="back_to_menu")]])
        )
        return

    # Kill webchat server and tunnel processes
    tasks = get_active_tasks()
    stopped = []
    for task in tasks:
        if task.get("port") == 3456 or task.get("type") in ("webchat", "tunnel"):
            try:
                proc = psutil.Process(task["pid"])
                for child in proc.children(recursive=True):
                    child.kill()
                proc.kill()
                remove_task(task["pid"])
                stopped.append(task["type"])
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                remove_task(task["pid"])
                stopped.append(task["type"])

    await query.edit_message_text(
        f"⏹ <b>Webchat stopped.</b>\n\nStopped: {', '.join(stopped) if stopped else 'nothing'}",
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data="back_to_menu")]])
    )
```

- [ ] **Step 4: Verify the inline menu works**

Run: `python main.py`

Open Telegram inline menu — see "🌐 Chat on Web" button.
If webchat is running — see "⏹ Stop Webchat" button.

- [ ] **Step 5: Commit**

```bash
git add handlers/messages.py
git commit -m "feat: dynamic inline menu shows launch/stop webchat based on port"
```

---

### Task 5: Non-Blocking Launch Handler

**Files:**
- Modify: `handlers/messages.py:485-589`

- [ ] **Step 1: Refactor `_handle_launch_webchat` to use async polling**

Replace the body of `_handle_launch_webchat`:

```python
async def _handle_launch_webchat(update, context):
    import re, time, asyncio
    from pathlib import Path
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup
    from tools.task_registry import register_task, check_port_conflict
    from tools.mcp_server import _start_detached, _poll_log_for_url_async

    query = update.callback_query
    chat_id = query.message.chat_id
    message_id = query.message.message_id

    # Check if already running
    conflict = check_port_conflict(3456)
    if conflict:
        url = conflict.get("url", "")
        await query.edit_message_text(
            f"✅ <b>Web Chat Already Running!</b>\n\n"
            f"PID: {conflict['pid']}\n"
            + (f"🔗 <a href='{url}'>Open Web Chat</a>\n\n" if url else "")
            + f"Use the Stop button to shut it down.",
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=True,
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data="back_to_menu")]])
        )
        return

    base_dir = Path(__file__).resolve().parent.parent
    webchat_dir = base_dir / 'webchat'
    logs_dir = base_dir / "logs"
    logs_dir.mkdir(exist_ok=True)

    server_port = 3456
    server_log = str(logs_dir / "webchat_server.log")
    tunnel_log = str(logs_dir / "webchat_tunnel.log")

    # Start node server
    server_cmd = "node server.js"
    server_proc = _start_detached(server_cmd, server_log, cwd=str(webchat_dir))
    register_task(
        pid=server_proc.pid,
        command=server_cmd,
        port=server_port,
        task_type="webchat",
        description="Webchat server",
        log_file=server_log,
    )

    await query.edit_message_text(
        "🌐 <b>Starting Web Chat...</b>\n\n"
        "Launching server and tunnel in background. "
        "This usually takes 15-30 seconds. Your bot remains responsive!",
        parse_mode=ParseMode.HTML,
    )

    await asyncio.sleep(2)

    # Start cloudflare tunnel
    tunnel_cmd = f"cloudflared tunnel --url http://localhost:{server_port}"
    tunnel_proc = _start_detached(tunnel_cmd, tunnel_log)
    register_task(
        pid=tunnel_proc.pid,
        command=tunnel_cmd,
        port=server_port,
        task_type="tunnel",
        description=f"Tunnel for webchat on port {server_port}",
        log_file=tunnel_log,
    )

    # Non-blocking URL polling
    async def _poll_url(chat_id, message_id, tunnel_pid, session_id):
        await asyncio.sleep(15)
        url = await _poll_log_for_url_async(tunnel_log, timeout=75)

        if url:
            from tools.task_registry import update_task
            update_task(tunnel_pid, url=url)

            # Call webchat API to set session continuity
            try:
                import httpx
                async with httpx.AsyncClient(timeout=5) as client:
                    await client.post(
                        f"http://localhost:{server_port}/api/set-session",
                        json={"chat_id": chat_id, "session_id": session_id}
                    )
            except Exception:
                pass  # Webchat may not be ready yet, session will load via URL params

            await context.bot.edit_message_text(
                chat_id=chat_id,
                message_id=message_id,
                text=(
                    f"✅ <b>Web Chat Online!</b>\n\n"
                    f"🌍 Public: <a href='{url}'>Open Web Chat</a>\n"
                    f"💻 Local: http://localhost:{server_port}\n\n"
                    f"<i>The webchat is using your current session. "
                    f"Open the URL to continue the conversation.</i>"
                ),
                parse_mode=ParseMode.HTML,
                disable_web_page_preview=True,
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🔙 Back", callback_data="back_to_menu")]
                ])
            )

            # Inform BMO (use module-level storage)
            storage.add_message(
                chat_id, "assistant",
                f"The user moved their conversation to the webchat at {url}. "
                "Same OpenCode session. Reply here to continue on Telegram."
            )
        else:
            await context.bot.edit_message_text(
                chat_id=chat_id,
                message_id=message_id,
                text=(
                    f"⚠️ <b>Tunnel Still Connecting...</b>\n\n"
                    f"Server PID: {server_proc.pid}\n"
                    f"Tunnel PID: {tunnel_pid}\n\n"
                    f"The tunnel is taking longer than expected. "
                    f"Check status in a moment using the inline menu.",
                ),
                parse_mode=ParseMode.HTML,
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🔙 Back", callback_data="back_to_menu")]
                ])
            )

    # Get current session for continuity
    session = storage.load_session(chat_id)
    session_id = session.session_id if session else ""

    asyncio.create_task(_poll_url(chat_id, message_id, tunnel_proc.pid, session_id))
```

- [ ] **Step 2: Verify the launch flow works**

Run: `python main.py`
Click "Chat on Web" button.
Expected: Message edits to "Starting Web Chat..." then after ~15-30s, edits to the URL.
Bot remains responsive during the wait.

- [ ] **Step 3: Run existing tests**

Run: `python -m unittest tests.test_task_registry tests.test_mcp_poller -v`

Expected: All PASS

- [ ] **Step 4: Commit**

```bash
git add handlers/messages.py
git commit -m "fix: non-blocking webchat launch with async tunnel polling"
```

---

### Task 6: Final Verification

- [ ] **Step 1: Run full test suite**

Run: `python -m unittest discover tests -v`

Expected: All tests PASS

- [ ] **Step 2: Manual end-to-end test**

1. Run: `python main.py`
2. Open Telegram, click inline menu → "🌐 Chat on Web"
3. Verify: message shows "Starting Web Chat..." — bot stays responsive
4. Wait: after ~15-30s message updates with URL
5. Open URL in browser → webchat loads with full conversation history
6. Send a message in webchat → response comes through
7. Click inline menu again → shows "⏹ Stop Webchat"
8. Click "⏹ Stop Webchat" → processes killed, button reverts to "🌐 Chat on Web"
