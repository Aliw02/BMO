# BMO Core Profile

This file contains the essential identity and core preferences of the USER and BMO.
Detailed technical experiences are stored separately in the Knowledge Vault (`data/experience/`).

## User Profile

- Name: [Enter Name]
- Role: [Enter Role]
- Preference: [Enter Preference]
- Arch: BMO is PORTABLE — each person runs their own instance on their device.
  The Telegram bot lives on the owner's device. The owner uses both CLI and Telegram
  to talk to their BMO. Other ALLOWED_USER_IDS can be added to connect via Telegram.
  Session sync is between CLI ↔ Telegram on the SAME device for the SAME user(s).
  ALLOWED_USER_IDS + OWNER_ID form a session group — they share one global active session.
  Users outside the group get isolated sessions.

## Learned Preferences

- Language: English
- Communication: Concise and professional.

## Knowledge Index

This index summarizes technical achievements stored in `data/experience/`. BMO should read these files to reuse past solutions.

- **new_session_context_leak.md**: `/new` now gives a true blank slate — `create_new_session()` forces a fresh backend session instead of reusing the old one. Root cause: `DELETE /session/{id}/message` doesn't clear the model provider's cached context window.
- **single_source_truth_api.md**: Eliminated `better-sqlite3` native binding entirely. Webchat now proxies all DB operations through a Python FastAPI server (`core/webchat_api.py`) on port 4098. Zero native Node.js dependencies. Single source of truth — all interfaces (CLI, Telegram, webchat) access the same `bot.db` through the same Python backend.
- **npm_publish_v2.1.17.md**: Published `@aliwey/bmo@2.1.17` with CLI autocomplete (custom `BMOCommandCompleter` with description proposals), standalone MCP, FastAPI DB bridge, offline diagnostics. 433 kB, 111 files.

---
*BMO: Always update this index after adding a new file to the vault.*
