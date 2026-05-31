# BMO Proactive Tool Usage & Freeze Fix Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix the model freeze on first message and enforce proactive use of MCP tools, subagents, and skills.

**Architecture:** Remove AGENTS.md (freeze trigger), rewrite system prompt with mandatory trigger rules, add dynamic MCP tool discovery, fix /init race condition, and add granular bash permissions to opencode.json.

**Tech Stack:** Python (httpx, asyncio), Telegram Bot API, OpenCode serve REST API, FastMCP, SQLite

---

### Task 1: Delete AGENTS.md (Freeze Root Cause)

**Files:**
- Delete: `AGENTS.md`

- [ ] **Step 1: Remove AGENTS.md**

Run:
```powershell
Remove-Item -LiteralPath "F:\Programming\ProgrammingWithPython\OpenCodeTes.zip\AGENTS.md"
```

Verify:
```powershell
Test-Path "F:\Programming\ProgrammingWithPython\OpenCodeTes.zip\AGENTS.md"
```
Expected: `False`

- [ ] **Step 2: Commit**

```bash
git add -u AGENTS.md
git commit -m "fix: remove AGENTS.md to eliminate model freeze on file operations"
```

---

### Task 2: Fix /init Race Condition in bot_client.py

**Files:**
- Modify: `core/bot_client.py:141-154`

- [ ] **Step 1: Fix the fire-and-forget /init call**

Replace `asyncio.create_task(self._http.post(f"/session/{session_id}/init", ...))` with `await self._http.post(f"/session/{session_id}/init", ...)`

Full replacement block in `create_session()`:
```python
            # --- DOUBLE HANDSHAKE: Call /init to lock in the model ---
            try:
                import uuid
                init_msg_id = f"msg_init_{uuid.uuid4().hex[:8]}"
                init_payload = {
                    "messageID": init_msg_id,
                    "modelID": m_id,
                    "providerID": p_id
                }
                logger.info("Initializing session %s with model %s...", session_id, m_id)
                await self._http.post(f"/session/{session_id}/init", json=init_payload, timeout=30.0)
            except Exception as init_err:
                logger.warning("Optional /init failed for session %s: %s", session_id, init_err)
            # --------------------------------------------------------
```

- [ ] **Step 2: Commit**

```bash
git add core/bot_client.py
git commit -m "fix: await /init handshake to eliminate session race condition"
```

---

### Task 3: Add _build_tool_context() Method to bot_client.py

**Files:**
- Modify: `core/bot_client.py` (add after `_get_agents_cached()`)

- [ ] **Step 1: Add the method**

```python
    async def _build_tool_context(self) -> str:
        """Discover available MCP tools and inject as context before each message."""
        try:
            tools = await self.get_mcp_tools()
            if tools and "tools" in tools:
                lines = ["\n\n[AVAILABLE MCP TOOLS — USE THESE, NOT BASH]"]
                for t in tools["tools"]:
                    name = t.get("name", "")
                    desc = (t.get("description", "") or "")[:100]
                    lines.append(f"- {name}: {desc}")
                lines.append("MANDATORY: For servers/tunnels/long-running processes, use these tools. NEVER run via bash.")
                return "\n".join(lines)
        except Exception as e:
            logger.debug("Failed to build tool context: %s", e)
        return ""
```

- [ ] **Step 2: Commit**

```bash
git add core/bot_client.py
git commit -m "feat: add dynamic MCP tool discovery for system prompt injection"
```

---

### Task 4: Rewrite System Prompt with Mandatory Rules

**Files:**
- Modify: `core/bot_client.py` — replace `TELEGRAM_SYSTEM_PROMPT` constant

- [ ] **Step 1: Replace TELEGRAM_SYSTEM_PROMPT**

Replace the entire `TELEGRAM_SYSTEM_PROMPT = r"""..."""` block with the new version containing:
- MANDATORY TOOL USAGE PROTOCOL (explicit rules + workflow)
- SUBAGENT TRIGGER RULES
- SKILL TRIGGER RULES
- FILE OPERATION RULES (AGENTS.md prohibition)
- All existing protocols (Knowledge Vault, Background Task Registry, Webchat, Web App Deployment)

See the full prompt content in the design document.

- [ ] **Step 2: Commit**

```bash
git add core/bot_client.py
git commit -m "feat: rewrite system prompt with mandatory tool usage, subagent, and skill trigger rules"
```

---

### Task 5: Inject Tool Context into send_query()

**Files:**
- Modify: `core/bot_client.py` — `send_query()` method

- [ ] **Step 1: Add tool context injection**

Find:
```python
        full_system_context = system_base + memory_instruction + tool_instruction + chat_context + skills_block + memory_block + security_warning + f"\n\nCURRENT PROTOCOL: {mode_instruction}" + anti_loop + agent_instruction
```

Replace with:
```python
        # Inject available MCP tools dynamically
        tool_context = await self._build_tool_context()

        full_system_context = system_base + memory_instruction + tool_instruction + chat_context + skills_block + memory_block + security_warning + tool_context + f"\n\nCURRENT PROTOCOL: {mode_instruction}" + anti_loop + agent_instruction
```

- [ ] **Step 2: Commit**

```bash
git add core/bot_client.py
git commit -m "feat: inject dynamic MCP tool context into every message's system prompt"
```

---

### Task 6: Add Granular Bash Permissions to opencode.json

**Files:**
- Modify: `opencode.json`

- [ ] **Step 1: Add permission section**

```json
{
  "permission": {
    "bash": {
      "*": "allow",
      "python -m http.server*": "deny",
      "node server*": "deny",
      "cloudflared*": "deny",
      "nohup*": "deny",
      "start /b*": "deny",
      "start /min*": "deny",
      "rm -rf*": "deny",
      "del /f*": "deny",
      "rmdir /s*": "deny"
    }
  }
}
```

- [ ] **Step 2: Commit**

```bash
git add opencode.json
git commit -m "config: add granular bash permissions to block server/tunnel commands"
```

---

### Task 7: Verify All Changes Work Together

- [ ] **Step 1: Verify AGENTS.md deleted**
```powershell
Test-Path "F:\Programming\ProgrammingWithPython\OpenCodeTes.zip\AGENTS.md"
```
Expected: `False`

- [ ] **Step 2: Verify bot_client.py syntax**
```powershell
python -m py_compile "F:\Programming\ProgrammingWithPython\OpenCodeTes.zip\core\bot_client.py"
```
Expected: No output

- [ ] **Step 3: Verify opencode.json valid JSON**
```powershell
python -c "import json; json.load(open('F:\Programming\ProgrammingWithPython\OpenCodeTes.zip\opencode.json')); print('Valid JSON')"
```
Expected: `Valid JSON`

- [ ] **Step 4: Verify mandatory rules in system prompt**
```powershell
python -c "
content = open('F:\Programming\ProgrammingWithPython\OpenCodeTes.zip\core\bot_client.py', encoding='utf-8').read()
assert 'MANDATORY TOOL USAGE PROTOCOL' in content
assert 'SUBAGENT TRIGGER RULES' in content
assert 'SKILL TRIGGER RULES' in content
assert 'DO NOT' in content and 'AGENTS.md' in content
print('All mandatory rules present')
"
```
Expected: `All mandatory rules present`

- [ ] **Step 5: Verify /init is awaited**
```powershell
python -c "
content = open('F:\Programming\ProgrammingWithPython\OpenCodeTes.zip\core\bot_client.py', encoding='utf-8').read()
assert 'await self._http.post' in content and '/init' in content
print('/init race condition fixed')
"
```
Expected: `/init race condition fixed`

- [ ] **Step 6: Final commit**
```bash
git add -A
git commit -m "feat: complete BMO proactive tool usage system"
```
