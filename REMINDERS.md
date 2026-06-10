# BMO v2.1.16 — Pre-Publish Checklist

## Before testing on affected device
- [ ] Copy ALL changed files to the affected machine:
  - `core/webchat_api.py` — **new** FastAPI server (port 4098)
  - `webchat/db_api.js` — **new** HTTP client (replaces db.js)
  - `webchat/server.js` — rewritten async handlers
  - `scripts/web_cmd.js` — starts Python API before Node.js
  - `webchat/package.json` — removed better-sqlite3
  - `scripts/postinstall.js` — removed rebuild step
  - `core/bot_client.py` — offline detection fixes
  - `cli.py` — standalone MCP server + `/diagnose` command
- [ ] Delete `webchat/node_modules` on affected machine
- [ ] Delete `%APPDATA%\npm\node_modules\@aliwey\bmo\webchat\node_modules`
- [ ] Run `bmo web` to test Python API + webchat startup

## Test checklist (on affected machine)
- [ ] `/diagnose` works (was `UnboundLocalError`)
- [ ] `get_session_summaries` tool available (MCP standalone fallback)
- [ ] `bmo web` starts without `ERR_DLOPEN_FAILED`
- [ ] Webchat loads sessions and sends messages
- [ ] `bmo` CLI starts without "BMO is offline" when server is alive

## Before publishing to npm
- [x] Bump version in `package.json` → `2.1.16`
- [x] Run `npm publish` from clean state
- [x] Fixed: `!webchat/node_modules/` negation in `files` array (shrunk 5.9MB → 404kB)
- [ ] After publish: `npm install -g @aliwey/bmo` on fresh machine to test postinstall

## Architecture reminders
- **Single source of truth**: Python backend owns `bot.db`. CLI, Telegram, and webchat all go through it.
- **No native Node modules**: better-sqlite3 is gone. Webchat has zero compilation dependencies.
- **Offline detection**: `is_alive()` uses `< 500`, 3 retries, TCP socket fallback, `trust_env=False`.
- **MCP server**: Runs standalone on port 4097 even without `TELEGRAM_TOKEN`.
