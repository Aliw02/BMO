# BMO — OpenCode Telegram Bot

## Entry points
- `main.py` — Telegram polling + MCP SSE server (port 4097); `.bot.lock` prevents duplicates (kills old PID via `psutil`)
- `run.py` — hot-reload via `watchfiles` (watches `config/`, `handlers/`, `core/`, `models/`, `storage/`)
- `webchat/server.js` — Express + Socket.IO web UI on port 3456

## Quick start
```
pip install -r requirements.txt
cp .env.example .env   # fill TELEGRAM_BOT_TOKEN, ALLOWED_USER_IDS
python main.py
```

## Key commands
| Action | Command |
|--------|---------|
| Run bot | `python main.py` |
| Hot-reload | `python run.py` |
| Auto-restart loop | `start_bmo.bat` |
| Web chat + tunnel | `start_webchat.bat` |

## Architecture
- **Telegram handlers**: `handlers/messages.py` (2200+ lines, single file)
- **OpenCode client**: `core/bot_client.py` — httpx `AsyncClient` talking to `opencode serve` HTTP API (port 4096)
- **Storage**: SQLite via `storage/sqlite_storage.py` (WAL mode). `get_storage()` always returns `SQLiteStorage` — `STORAGE_TYPE` env var is read but ignored
- **Encryption**: `core/security.py` — `cryptography.fernet` for API keys; master key at `data/.master.key`
- **MCP server**: `tools/mcp_server.py` (FastMCP SSE on port 4097) — auto-started in `threading.Thread` by `main.py`
- **Task registry**: `tools/task_registry.py` — background process management with `data/background_tasks.json`
- **Default model**: `big-pickle`, provider: `opencode`
- **BMO identity**: `data/memory.md` — core profile + knowledge index
- **Owner/admin user ID**: `732356803` (hardcoded in `handlers/messages.py`)

## Env vars (`.env`)
- `TELEGRAM_BOT_TOKEN` or `TELEGRAM_TOKEN` — BotFather token (checked in that order)
- `ALLOWED_USER_IDS` — comma-separated Telegram user IDs (empty = allow all)
- `OPENCODE_SERVER_URL` or `OPENCODE_HOST`/`OPENCODE_PORT` — OpenCode server (default: `127.0.0.1:4096`)
- `STORAGE_TYPE` — read but unused (code always uses SQLite)

## Bot quirks
- **No test/lint/typecheck tooling** — only `tests/test_task_registry.py` exists
- **HTML sanitization**: `_clean_telegram_html()` strips unsupported tags (`<ul>`, `<h1>`, etc.) and auto-closes `<b>`/`<i>`/`<code>`/`<pre>`
- **Auto file archival**: bot scans responses for file paths, copies to `data/files/YYYY-MM-DD/`, sends as documents
- **Auto-summarization**: after 5 min inactivity (`INACTIVITY_TIMEOUT = 300`), old sessions are summarized on new session creation
- **System prompt** injected as `synthetic: True` (hidden from OpenCode history) on every message
- **Session double handshake**: `/session` POST creates session, then `/session/{id}/init` locks in the model

## Git-ignored
`.env`, `data/`, `logs/`, `*.db`, `__pycache__/`

## Portable distribution
- `BMO_Portable/` — self-contained copy with embedded Python 3.11.9, cloudflared, setup wizard
- Build script: `python scratch/create_portable_bmo.py`
