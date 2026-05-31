# Webchat Inline Launch & Session Continuity

**Date:** 2026-05-21
**Status:** Draft

## Problem

When the user clicks "Chat on Web" from the Telegram inline menu, the bot freezes for up to 30 seconds. This happens because `_poll_log_for_url()` in `tools/mcp_server.py:53` uses synchronous `time.sleep(2)` inside an `asyncio.create_task`, blocking the Telegram bot's event loop.

Additionally, the launched webchat starts a **fresh** AI session — it doesn't carry over the user's existing conversation history or OpenCode session context from Telegram.

## Goals

1. One-click "Chat on Web" deployment that never blocks the bot
2. When webchat is already running, show "⏹ Stop Webchat" instead
3. Webchat URL carries session continuity — user sees their Telegram conversation history
4. BMO is informed via a message in the session that the conversation moved to webchat
5. 75-second timeout for cloudflare tunnel URL polling

## Architecture

The fix touches three layers: Telegram bot (Python), MCP utility functions (Python), and the webchat server (Node.js).

### Layer 1: Non-Blocking Tunnel Polling

**File:** `tools/mcp_server.py`

Add async version of `_poll_log_for_url`:

```python
async def _poll_log_for_url_async(log_file: str, timeout: int = 75) -> str | None:
    """Async version — uses asyncio.sleep instead of time.sleep."""
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

The sync version remains unchanged for MCP tool (`check_task_status`) compatibility.

### Layer 2: Non-Blocking Launch Handler

**File:** `handlers/messages.py`

Refactor `_handle_launch_webchat` (line 485):

1. **Check port 3456** — if already in use, show "⏹ Stop Webchat" inline button
2. **Start node server** via `_start_detached` (unchanged, already non-blocking)
3. **Edit message** to "🌐 Starting webchat..."
4. **Start cloudflare tunnel** via `_start_detached` (unchanged, already non-blocking)
5. **Create non-blocking `_poll_url`** awaitable that:
   - Awaits `_poll_log_for_url_async(tunnel_log, timeout=75)`
   - On URL found: calls `editMessageText` with the public URL and a "Back to Menu" button, then calls `/api/set-session` on the webchat
   - On timeout: edits message to "⚠️ Tunnel still connecting..." with a "Check Status" button
6. **Return immediately** — bot remains responsive during polling

Add `action:stop_webchat` handler:

1. Look up webchat + tunnel tasks in task registry by port
2. Kill processes via `psutil` (process tree)
3. Remove from task registry
4. Edit message: "⏹ Webchat stopped."

### Layer 3: Dynamic Inline Menu

**File:** `handlers/messages.py` — `build_inline_menu()` (line 121)

Modify the "Chat on Web" button to be dynamic:

- **Webchat not running:** `InlineKeyboardButton("🌐 Chat on Web", callback_data="action:launch_webchat")`
- **Webchat running:** `InlineKeyboardButton("⏹ Stop Webchat", callback_data="action:stop_webchat")`

Check: `check_port_conflict(3456)` returns a task dict or `None`.

### Layer 4: Session Continuity

**File:** `webchat/server.js`

Add `POST /api/set-session` endpoint:

- Input: `{ chat_id, session_id }`
- Calls `setActiveSession(chat_id, session_id)` on shared DB
- Reads `opencode_session_id` from `sessions` table metadata
- Returns: `{ opencode_session_id, session }`
- Returns `404` if session_id not found in DB

**File:** `webchat/public/app.js`

On page load (`DOMContentLoaded`):

- Parse URL params: `?chat_id=X&session_id=Y`
- If present:
  - Call `POST /api/set-session` with the params
  - Call `GET /api/messages?session_id=Y&limit=100` to load history
  - Render all messages in the chat area
  - Set `activeSessionId = Y`
  - Emit `set_session` via Socket.IO
- Hardcoded `732356803` fallback removed from all session load calls
- Load `chat_id` from URL param, or fall back to existing `active_sessions` entry from DB
- Load `session_id` from URL param, or fall back to the active session for that chat_id

**File:** `webchat/server.js` — Socket.IO `user_message` handler

Currently (`server.js:515`): creates a **new** OpenCode session if `opencode_session_id` is null. With `/api/set-session` already setting this in the DB, the webchat reuses the Telegram bot's OpenCode session. No change needed to the handler itself — it reads from the DB which is now pre-populated.

### Layer 5: Inform BMO

**File:** `handlers/messages.py`

After successful deployment (inside `_poll_url` after URL found):

```python
storage.add_message(chat_id, "assistant",
    "The user moved their conversation to the webchat at [URL]. "
    "Same OpenCode session. Reply here to continue on Telegram.")
```

## Flow Diagrams

### Happy Path (Webchat Not Running)

```
User taps "🌐 Chat on Web"
  → build_inline_menu() checks port 3456
  → Not running → show "🌐 Chat on Web" button
  → User taps
  → _handle_launch_webchat()
    → Start node server (detached)
    → Edit: "🌐 Starting webchat..."
    → Start cloudflared tunnel (detached)
    → Create async _poll_url task
    → Return (bot responsive)
  → ~15s later, tunnel URL found
    → Edit: "✅ Webchat Online! URL: ..."
    → POST /api/set-session { chat_id, session_id }
    → Add message: "Moved to webchat..."
    → User opens URL → sees full conversation history
```

### Webchat Already Running

```
User taps "Chat on Web"
  → build_inline_menu() checks port 3456
  → Running → show "⏹ Stop Webchat" button
  → User taps
  → _handle_stop_webchat()
    → Kill webchat + tunnel processes
    → Remove from registry
    → Edit: "⏹ Webchat stopped."
```

## Files Changed

| File | Type | Changes |
|------|------|---------|
| `tools/mcp_server.py` | Modify | Add `_poll_log_for_url_async()` |
| `handlers/messages.py` | Modify | Refactor `_handle_launch_webchat`; add `action:stop_webchat`; dynamic `build_inline_menu`; BMO notification |
| `webchat/server.js` | Modify | Add `POST /api/set-session` endpoint |
| `webchat/public/app.js` | Modify | Parse URL params, load session on init |

## Edge Cases

- **Tunnel never comes up (75s timeout):** User sees "⚠️ Tunnel still connecting..." with a "Check Status" button that re-polls
- **Node server fails to start:** `_start_detached` returns a PID but process may crash immediately. Poll the server port via HTTP check before starting tunnel
- **Port 3456 in use by another app:** Handler detects conflict via `check_port_conflict()` — show "Port 3456 in use by another application" message rather than "Stop Webchat"
- **Bot restarts during tunnel polling:** The polling task is lost on restart. Stale processes remain; stop handler cleans up via psutil
