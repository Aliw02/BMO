# BMO — OpenCode Telegram Bot

## Entry points
- `main.py` — Telegram polling + MCP SSE server (port 4097); uses `.bot.lock` to prevent duplicates (kills old PID via `psutil`)
- `run.py` — hot-reload via `watchfiles` (auto-restarts `main.py` on file changes)
- `webchat/server.js` — Express + Socket.IO web UI on port 3456 (uses "ask" mode)

## Quick start
```
pip install -r requirements.txt
cp .env.example .env   # set TELEGRAM_BOT_TOKEN, ALLOWED_USER_IDS
python main.py
```

## Key commands
| Action | Command |
|--------|---------|
| Run bot | `python main.py` |
| Hot-reload dev | `python run.py` |
| Auto-restart loop | `start_bmo.bat` |
| Web chat | `cd webchat && npm install && node server.js` |

## Architecture
- **Telegram handlers**: `handlers/messages.py` (2200+ lines, single file — all message/command/callback logic)
- **OpenCode client**: `core/bot_client.py` — httpx `AsyncClient` talking to `opencode serve` HTTP API (default port 4096)
- **Storage**: `storage/sqlite_storage.py` — SQLite with WAL mode. `get_storage()` always returns `SQLiteStorage` regardless of `STORAGE_TYPE` env var
- **Security**: `core/security.py` — `cryptography.fernet` for API key encryption; master key at `data/.master.key`
- **MCP server**: `tools/mcp_server.py` — FastMCP over SSE on port 4097, auto-started as daemon thread by `main.py`
- **OpenCode config**: `.opencode/config.json` — points to BMO's MCP server URL
- **Default model**: `big-pickle`, provider: `opencode`
- **Owner/admin**: user ID `732356803` (hardcoded in `handlers/messages.py`)

## Important env vars (`.env`)
- `TELEGRAM_BOT_TOKEN` or `TELEGRAM_TOKEN` — BotFather token (checked in that order)
- `ALLOWED_USER_IDS` — comma-separated Telegram user IDs (empty = allow all)
- `OPENCODE_SERVER_URL` or `OPENCODE_HOST`/`OPENCODE_PORT` — OpenCode server (default port 4096)
- `STORAGE_TYPE` — read but unused (code always uses SQLite)
- `MCP_SERVER_PORT` — default `4097`

## Bot quirks
- **No test/lint/typecheck tooling** — only `tests/test_task_registry.py` exists
- **HTML sanitization**: `_clean_telegram_html()` strips unsupported tags (`<ul>`, `<h1>`, etc.) and auto-closes `<b>`/`<i>`/`<code>`/`<pre>`
- **Auto file archives**: bot copies response file paths to `data/files/YYYY-MM-DD/` and sends as documents
- **Auto-summarization**: after 5 min inactivity (`INACTIVITY_TIMEOUT = 300`), old sessions are summarized on new session creation
- **System prompt** injected as `synthetic: True` (hidden from OpenCode history) on every message

## Git-ignored
`.env`, `data/`, `logs/`, `*.db`, `__pycache__/`, `.venv/`

## Portable distribution
- `BMO_Portable/` — self-contained copy with embedded Python 3.11.9, cloudflared, setup wizard
- Build script: `python scratch/create_portable_bmo.py`
