# BMO — OpenCode Telegram Bot

## Entry points
- `main.py` — Telegram polling + MCP SSE server (hardcoded port 4097). Uses `.bot.lock` to kill stale PIDs via `psutil`.
- `run.py` — Hot-reload via `watchfiles` (watches `config/`, `handlers/`, `core/`, `models/`, `storage/`, root; **not** `tools/` or `tests/`).
- `webchat/server.js` — Express + Socket.IO web UI on port 3456 (default chat ID `732356803`, mode `"execute"`, model `qwen3.6-plus-free`).

## Quick start
```
pip install -r requirements.txt
pip install psutil watchfiles   # missing from requirements.txt
# No .env.example at root — copy from BMO_Portable/.env.example or create .env manually
python main.py
```

## Key commands
| Action | Command |
|--------|---------|
| Run bot | `python main.py` |
| Hot-reload | `python run.py` |
| Auto-restart loop | `start_bmo.bat` |
| Web chat | `cd webchat && npm install && node server.js` |
| Run tests | `python -m unittest tests/test_task_registry.py` or `python -m pytest tests/` |

## Architecture
- **Telegram logic**: `handlers/messages.py` (~2664 lines, single file with all handlers and callbacks)
- **Config loading**: `config/settings.py` — central `.env` loader; all other modules import from here
- **OpenCode API client**: `core/bot_client.py` — httpx `AsyncClient` talking to `opencode serve` HTTP API (default `127.0.0.1:4096`)
- **Shared state**: `core/shared_state.py` — `pending_permissions` dict for MCP permission requests
- **Storage**: `storage/sqlite_storage.py` (SQLite WAL mode). `storage/storage.py` has unused `JSONLStorage`. `get_storage()` always returns `SQLiteStorage` regardless of `STORAGE_TYPE` env var. DB at `data/bot.db`.
- **Encryption**: `core/security.py` — `cryptography.fernet.Fernet` for API key storage; master key at `data/.master.key`
- **MCP server**: `tools/mcp_server.py` (FastMCP over SSE, hardcoded port 4097) — auto-started in `threading.Thread` by `main.py`
- **Background tasks**: `tools/task_registry.py` — JSON-based registry for tracking server/tunnel PIDs and ports
- **System prompt**: `config/system-prompt.json` — shared between Telegram bot and webchat; defines BMO identity, tool rules, mode/agent prompts
- **OpenCode config**: `opencode.json` — MCP server URL + permission rules; `.opencode/config.json` — OpenCode CLI MCP server config
- **Webchat**: separate `webchat/bmo.db` (better-sqlite3) for sessions/messages; independent from `data/bot.db`
- **Owner/admin user ID**: `732356803` (hardcoded in `handlers/messages.py`, `webchat/server.js`, `webchat/public/app.js`, `webchat/public/index.html`)
- **Tests**: 2 files (`tests/test_task_registry.py`, `tests/test_mcp_poller.py`) using `unittest`; no lint/typecheck tooling

## Important env vars (`.env`)
- `TELEGRAM_BOT_TOKEN` or `TELEGRAM_TOKEN` — BotFather token (checked in that order)
- `ALLOWED_USER_IDS` — comma-separated Telegram user IDs (empty = allow all)
- `OPENCODE_SERVER_URL` or `OPENCODE_HOST`/`OPENCODE_PORT` — OpenCode server (default `127.0.0.1:4096`)
- `OPENCODE_PROVIDER`, `OPENCODE_MODEL` — global provider/model override (read in `handlers/messages.py`)
- `STORAGE_TYPE` — read but **unused** (code always uses SQLite)
- `DEBUG` — enable debug logging (default `False`)
- `MCP_SERVER_PORT` — **not read by any code** (port 4097 hardcoded in `main.py:96`)

## Bot quirks
- **HTML sanitization**: `_clean_telegram_html()` strips tags Telegram doesn't support (`<ul>`, `<h1>`, etc.) and auto-closes unclosed `<b>`/`<i>`/`<code>`/`<pre>`
- **Auto file archives**: bot scans responses for file paths, copies files to `data/files/YYYY-MM-DD/`, sends as documents
- **Auto-summarization**: after 5 min inactivity (`INACTIVITY_TIMEOUT = 300`), old sessions are summarized on new session creation
- **System prompt** injected as `synthetic: True` (hidden from OpenCode history) on every message
- **Webchat** hardcodes chat ID `732356803` in 4 files — changing admin ID requires updating all of them
- **`psutil` and `watchfiles`** used but missing from `requirements.txt` — install manually if needed
- **`.env` committed** (tracked before being added to `.gitignore`) — be careful not to leak tokens

## Directory layout
```
config/         Settings, system prompt JSON
core/           Bot client, security, shared state
handlers/       Telegram message/callback handlers (messages.py is the main file)
models/         ChatSession, ChatMessage, UserMemory data models
storage/        SQLite storage backend
tools/          MCP server, task registry, session summary helpers
webchat/        Express + Socket.IO web UI (port 3456) + better-sqlite3 DB
tests/          Unit tests (unittest, 2 files)
archive/        Old/one-off scripts (API probes, scrapers, OCR)
scratch/        Experimental scripts (tunnel launcher, portable builder)
BMO_Portable/   Self-contained distribution copy with embedded Python
data/           Runtime data (git-ignored): bot.db, sessions, chat history, files
logs/           Log files (git-ignored)
```

## Git-ignored
`.env`, `data/`, `logs/`, `*.db`, `__pycache__/`, `.venv/`, `*.pyc`

## Portable distribution
- `BMO_Portable/` — self-contained copy with embedded Python 3.11.9, cloudflared, setup wizard
- Build: `python scratch/create_portable_bmo.py`
- Has its own `AGENTS.md` (may diverge from root — currently states wrong default port 13773 and "ask" mode for webchat)

## Permissions (opencode.json)
Denied commands: `rm -rf`, `del /f`, `rmdir /s` (destructive); `python -m http.server`, `node server`, `cloudflared`, `nohup`, `start /b`, `start /min` (server/tunnel).
