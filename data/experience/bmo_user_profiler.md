# BMO User Profiler & Dual-Identity System

## What
Two-layer identity architecture for BMO:
- **BMO.md** — static identity file at BMO_HOME root. BMO's persona, capabilities, and behavioral instructions. Editable by BMO via file tools.
- **USER.md** — dynamic profile file at BMO_HOME root. Updated every 7 messages by ProfilerEngine to record stable user traits, preferences, habits, constraints.

Both files are injected into the OpenCode system context (like memory.md) via `bot_client.py`'s `_get_bmo_identity()` and `_get_user_profile()` methods with 60s TTL caching.

## Files Created
- `BMO.md` — BMO identity (root of BMO_HOME)
- `USER.md` — User profile (root of BMO_HOME)
- `core/profiler_engine.py` — ProfilerEngine class
- `tests/test_profiler_engine.py` — 13 tests, all passing

## Files Modified
- `config/settings.py` — Added `BMO_FILE` / `USER_FILE` path constants
- `config/system-prompt.json` — Added `identity_instruction` and `profile_instruction` keys
- `core/bot_client.py` — Added `_get_bmo_identity()`, `_get_user_profile()`, injection into system context of both `_send_direct()` and `_send_via_worker()`
- `handlers/messages.py` — Imported `ProfilerEngine`, instantiated `profiler` at module level, added `_check_profiler()` async function, trigger calls at both message-counter points (greeting fast-path and main LLM response path)

## Architecture
- `ProfilerEngine.should_run(chat_id, message_count)` — tracks per-chat last-run count, returns True every 7 messages
- `ProfilerEngine.analyze_and_update(...)` — calls LLM via `opencode_client.send_simple_query()` to extract PREFERENCE/HABIT/CONSTRAINT/TRAIT observations
- `ProfilerEngine.write_observation(observation)` — appends under `## Recent Observations` section in USER.md
- Both BMO.md and USER.md read via TTL-cached methods in `OpenCodeBotClient` (same pattern as `memory.md`)
- System prompt assembly order: `base + memory_instruction + identity_instruction + bmo_block + profile_instruction + user_block + tool_instruction + chat_context + ...`

## Trigger Points
1. Greeting fast-path (line ~2435): after random greeting response sent
2. Main `_process()` path (line ~2480): after LLM response chunked and sent

Both fire as `asyncio.create_task()` to avoid blocking response delivery.

## Key Decisions
- BMO.md and USER.md at BMO_HOME root for visibility, separate from `data/` directory
- Profiler reuses existing message-counter pattern (same 7-message cycle as auto-summary)
- `send_simple_query()` already existed on `OpenCodeBotClient` — no adapter needed
- No Telegram notification for profiler updates (silent background task)

## Verification
- 13 unit tests for `ProfilerEngine` pass
- All 91 tests in test suite pass
