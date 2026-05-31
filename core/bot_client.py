"""
OpenCode integration client for the bot.
Communicates directly with a running `opencode serve` instance via HTTP REST API.
"""

import asyncio
import logging
import os
import re
import time
import httpx
import json
from typing import Optional

from config.settings import OPENCODE_BASE_URL, OPENCODE_TIMEOUT, OPENCODE_POLL_INTERVAL, OPENCODE_POLL_TIMEOUT

logger = logging.getLogger(__name__)

HEADERS = {"Content-Type": "application/json"}

# ── Load shared system prompt config (single source of truth) ──
_config_path = os.path.join(os.path.dirname(__file__), "..", "config", "system-prompt.json")
with open(_config_path, "r", encoding="utf-8") as _f:
    _system_config = json.load(_f)

TELEGRAM_SYSTEM_PROMPT = _system_config["base_prompt"]


class OpenCodeBotClient:
    """Client that talks directly to the opencode serve HTTP API."""

    _CACHE_TTL = 60  # seconds

    def __init__(self):
        self.is_connected = False
        self.last_session_id: Optional[str] = None
        self._http = httpx.AsyncClient(
            base_url=OPENCODE_BASE_URL,
            headers=HEADERS,
            timeout=float(OPENCODE_TIMEOUT),
        )
        self._memory_cache: Optional[str] = None
        self._memory_cache_time: float = 0
        self._agents_cache: Optional[list] = None
        self._agents_cache_time: float = 0
        self._memory_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "data", "memory.md"))

    async def is_alive(self) -> bool:
        """Lightweight check if the server is responding."""
        try:
            r = await self._http.get("/session", timeout=5.0)
            return r.status_code == 200
        except Exception:
            return False

    async def connect(self) -> bool:
        """Verify that opencode serve is reachable."""
        try:
            r = await self._http.get("/session")
            if r.status_code == 200:
                self.is_connected = True
                logger.info("Connected to opencode serve at %s", OPENCODE_BASE_URL)
            else:
                logger.warning("opencode serve returned %d", r.status_code)
                self.is_connected = False
        except Exception as e:
            logger.error("Cannot reach opencode serve: %s", e)
            self.is_connected = False
        return self.is_connected

    async def create_session(self, provider_id: Optional[str] = None, model_id: Optional[str] = None, env: Optional[dict] = None) -> Optional[str]:
        """Create a new opencode session and return its ID."""
        try:
            # Force defaults if None/Empty
            p_id = provider_id if (provider_id and str(provider_id) != "None") else "opencode"
            m_id = model_id if (model_id and str(model_id) != "None") else "big-pickle"
            
            # The server expects a nested 'model' object
            payload = {
                "model": {
                    "id": m_id,
                    "providerID": p_id
                }
            }
            
            if env:
                payload["env"] = env
            
            logger.info("--- OPENCODE SESSION REQUEST ---")
            logger.info("Payload: %s", json.dumps(payload))
            
            r = await self._http.post("/session", json=payload)
            r.raise_for_status()
            
            data = r.json()
            session_id = data.get("id")
            
            actual_model = data.get("model", {}).get("id", "Unknown")
            logger.info("--- OPENCODE SESSION CREATED: %s (Model: %s) ---", session_id, actual_model)
            return session_id
        except Exception as e:
            logger.error("Failed to create session: %s", e)
            return None

    async def get_session_info(self, session_id: str) -> Optional[dict]:
        """Fetch session details from the server to verify active model etc."""
        try:
            r = await self._http.get(f"/session/{session_id}")
            if r.status_code == 200:
                return r.json()
            return None
        except Exception as e:
            logger.debug("Failed to get session info: %s", e)
            return None

    async def get_providers(self) -> dict:
        """Fetch available providers and their models."""
        try:
            r = await self._http.get("/provider", timeout=10)
            r.raise_for_status()
            return r.json()
        except Exception as e:
            logger.error("Failed to fetch providers: %s", e)
            return {}

    async def delete_messages(self, session_id: str) -> bool:
        """Clears all messages in a session (Reset History)."""
        try:
            r = await self._http.delete(f"/session/{session_id}/message")
            return r.status_code == 200
        except Exception as e:
            logger.error("Failed to delete session messages: %s", e)
            return False

    async def get_agents(self) -> list:
        """Fetch available agents (skills)."""
        try:
            r = await self._http.get("/agent", timeout=10)
            r.raise_for_status()
            return r.json()
        except Exception as e:
            logger.error("Failed to fetch agents: %s", e)
            return []

    async def get_mcp_tools(self) -> dict:
        """Fetch available MCP tools."""
        try:
            r = await self._http.get("/config", timeout=10)
            r.raise_for_status()
            return r.json().get("mcp", {})
        except Exception as e:
            logger.error("Failed to fetch MCP tools: %s", e)
            return {}

    async def _get_memory_content(self) -> str:
        """Read memory.md with caching to avoid disk I/O on every message."""
        now = time.monotonic()
        if self._memory_cache is not None and (now - self._memory_cache_time) < self._CACHE_TTL:
            return self._memory_cache
        try:
            if os.path.exists(self._memory_path):
                with open(self._memory_path, "r", encoding="utf-8") as f:
                    self._memory_cache = f.read()
            else:
                self._memory_cache = ""
        except Exception as e:
            logger.warning("Could not read memory file: %s", e)
            self._memory_cache = ""
        self._memory_cache_time = now
        return self._memory_cache

    async def _get_agents_cached(self) -> list:
        """Fetch agents/skills list with caching to avoid HTTP call on every message."""
        now = time.monotonic()
        if self._agents_cache is not None and (now - self._agents_cache_time) < self._CACHE_TTL:
            return self._agents_cache
        try:
            r = await self._http.get("/agent", timeout=5)
            if r.status_code == 200:
                self._agents_cache = r.json()
            else:
                self._agents_cache = []
        except Exception:
            self._agents_cache = self._agents_cache or []
        self._agents_cache_time = now
        return self._agents_cache

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

    async def send_query(
        self, 
        query: str, 
        session_id: Optional[str] = None, 
        files: Optional[list[dict]] = None,
        provider_id: Optional[str] = None,
        model_id: Optional[str] = None,
        active_mode: str = "ask",
        active_agent: str = "default",
        provider_env: Optional[dict] = None,
        chat_id: Optional[int] = None,
        session_uuid: Optional[str] = None
    ) -> str:
        """
        Send a query to opencode serve with a hidden system context.
        Creates a new session if session_id is None.
        Accepts optional files list: [{"path": "...", "mime": "..."}]
        Returns the assistant's Telegram-HTML-formatted response.
        No longer handles status callbacks — caller manages polling separately.
        """
        if not self.is_connected:
            connected = await self.connect()
            if not connected:
                return "❌ Error: Could not connect to OpenCode backend. Is the server running?"

        if not session_id:
            # Inject keys if provided
            session_id = await self.create_session(provider_id, model_id, env=provider_env)
            if not session_id:
                return "Error: Could not create OpenCode session."
        
        self.last_session_id = session_id

        memory_content = await self._get_memory_content()

        # Mode-specific instructions
        mode_prompts = _system_config["mode_prompts"]
        mode_instruction = mode_prompts.get(active_mode, mode_prompts["execute"])
        
        # Anti-loop directive: prevent re-reading old tasks from history
        anti_loop = _system_config["anti_loop"]
        
        # Agent-specific personas
        agent_prompts = _system_config["agent_prompts"]
        agent_instruction = agent_prompts.get(active_agent, "")
        
        # Inject available skills/agents
        skills_block = ""
        try:
            agents = await self._get_agents_cached()
            if agents:
                lines = ["\n\n<AVAILABLE SKILLS>"]
                for a in agents[:10]:
                    name = a.get("name", "")
                    desc = a.get("description", "")
                    lines.append(f"- {name}: {desc}")
                lines.append("</AVAILABLE SKILLS>\nUse these proactively when needed.")
                skills_block = "\n".join(lines)
        except Exception:
            pass

        # Build final system context with Memory and Tools
        chat_context = ""
        if chat_id:
            chat_context = f"\n\n[USER_CONTEXT]\nCURRENT_CHAT_ID: {chat_id}\nCURRENT_SESSION_ID: {session_uuid or 'unknown'}\n[END USER_CONTEXT]"
            chat_context += "\n\n<b>FILE STORAGE</b>: When creating files, save them inside <code>data/files/</code>. Use date-based subfolders: <code>data/files/{{YYYY-MM-DD}}/{{CURRENT_SESSION_ID}}_{{HHMMSS}}_{{filename}}</code> so files are linked to sessions and dates."
        
        tool_instruction = _system_config["tool_instruction"]
        
        # Inject available skills/agents
        skills_block = ""
        try:
            agents = await self._get_agents_cached()
            if agents:
                lines = ["\n\n<AVAILABLE SKILLS>"]
                for a in agents[:10]:
                    name = a.get("name", "")
                    desc = a.get("description", "")
                    lines.append(f"- {name}: {desc}")
                lines.append("</AVAILABLE SKILLS>\nUse these proactively when needed.")
                skills_block = "\n".join(lines)
        except Exception:
            pass

        # Resolve project root dynamically for portability
        project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
        system_base = TELEGRAM_SYSTEM_PROMPT.replace("{PROJECT_ROOT}", project_root)

        # Proactive Security Guard: Detect external paths in query and remind the model
        security_warning = ""
        external_paths = re.findall(r'([a-zA-Z]:\\[^`"\'\s\n<>]+|/[^`"\'\s\n<>]+)', query)
        for p in external_paths:
            try:
                p_abs = os.path.abspath(p)
                if not p_abs.startswith(project_root):
                    security_warning = f"\n\n[SECURITY ALERT]\nUser requested access to external path: {p_abs}\nYou MUST call request_permission(chat_id={chat_id}, reason='...', scope='{p_abs}') before reading or processing this file.\n[END ALERT]"
                    break
            except Exception:
                continue

        # Inject available MCP tools dynamically
        tool_context = await self._build_tool_context()
        
        memory_content = await self._get_memory_content()
        memory_block = f"\n\n[LONG-TERM MEMORY]\n{memory_content}\n[END MEMORY]" if memory_content else ""
        memory_instruction = _system_config["memory_instruction"]

        full_system_context = system_base + memory_instruction + tool_instruction + chat_context + skills_block + memory_block + security_warning + tool_context + f"\n\nCURRENT PROTOCOL: {mode_instruction}" + _system_config["anti_loop"] + agent_instruction

        async def _do_send(sid):
            try:
                payload = {
                    "parts": [
                        {
                            "type": "text",
                            "text": full_system_context,                            "synthetic": True,
                        },
                        {
                            "type": "text",
                            "text": query,
                        },
                    ]
                }
                
                if files:
                    for f in files:
                        file_path = f["path"].replace('\\', '/')
                        payload["parts"].append({
                            "type": "file",
                            "url": f"file:///{file_path}",
                            "mime": f["mime"]
                        })

                r = await self._http.post(f"/session/{sid}/message", json=payload)
                return r, None
            except httpx.TimeoutException as e:
                logger.error("Timeout sending prompt to session %s [%ss]: %s", sid, self._http.timeout, e)
                return None, f"Request timed out after {self._http.timeout}s"
            except httpx.ConnectError as e:
                logger.error("Cannot connect to opencode serve at %s: %s", OPENCODE_BASE_URL, e)
                return None, f"Cannot reach OpenCode at {OPENCODE_BASE_URL} — is the server running?"
            except Exception as e:
                logger.error("Error in _do_send: %s [%s]", e, type(e).__name__)
                return None, f"Network error ({type(e).__name__}): {e}"

        r, err = await _do_send(session_id)
        
        # Automatic recovery: if session not found, clear it and try once more
        if r is not None and r.status_code == 404:
            logger.warning("Session %s not found on server. Creating new session...", session_id)
            session_id = await self.create_session(provider_id, model_id, env=provider_env)
            if not session_id:
                return "Error: Session lost and could not create a new one."
            self.last_session_id = session_id
            r, err = await _do_send(session_id)

        if r is None or r.status_code not in (200, 201):
            status_code = r.status_code if r else "None"
            text = (r.text[:200] if r else (err or "Network error"))
            logger.error("Prompt failed (%s): %s", status_code, text)
            return f"Error: opencode returned status {status_code} - {text[:200]}"

        # Poll for response instead of blocking on /wait
        deadline = time.monotonic() + OPENCODE_POLL_TIMEOUT
        poll_client = httpx.AsyncClient(
            base_url=OPENCODE_BASE_URL,
            headers=HEADERS,
            timeout=10.0,
        )
        try:
            while time.monotonic() < deadline:
                await asyncio.sleep(OPENCODE_POLL_INTERVAL)
                try:
                    r = await poll_client.get(
                        f"/session/{session_id}/message",
                        params={"limit": "20"},
                    )
                    if r.status_code != 200:
                        continue
                    messages = r.json()
                    if not messages:
                        continue

                    # Only check the LATEST message to avoid returning old responses
                    msg = messages[-1]
                    info = msg.get("info", {})
                    role = info.get("role")
                    
                    # Skip if latest message is not from assistant
                    if role != "assistant":
                        continue
                        
                    parts = msg.get("parts", [])
                    
                    # Check completion status
                    has_finish = any(p.get("type") == "step-finish" for p in parts)
                    finish_reason = info.get("finish")  # "stop", "error", etc.
                    is_complete = has_finish or finish_reason in ("stop", "error", "length")
                    
                    # Check for error parts
                    error_parts = [p for p in parts if p.get("type") == "error"]
                    if error_parts:
                        error_msg = error_parts[0].get("message", "Unknown error")
                        logger.error("Model returned error: %s", error_msg)
                        return f"Error: {error_msg}"
                    
                    # Extract text from text-type parts only (not reasoning)
                    text_parts = []
                    for p in parts:
                        p_type = p.get("type")
                        p_text = p.get("text", "")
                        p_synthetic = p.get("synthetic")
                        
                        # Skip synthetic parts
                        if p_synthetic:
                            continue
                            
                        # Only collect actual text parts (not reasoning/step markers)
                        if p_type == "text" and p_text:
                            text_parts.append(p_text)
                    
                    text = "\n".join(text_parts).strip()
                    
                    # Return if we have text and response is complete
                    if text and is_complete:
                        logger.info("Response received (%d chars, finish=%s)", len(text), finish_reason or "step-finish")
                        return text
                    elif text:
                        logger.debug("Response partial (%d chars, waiting for completion)", len(text))

                except httpx.TimeoutException:
                    continue
                except Exception as e:
                    logger.debug("Poll error (non-fatal): %s", e)
                    continue

            logger.error("Polling timed out after %ds", OPENCODE_POLL_TIMEOUT)
            return "Error: Request timed out after 30 minutes."
        finally:
            await poll_client.aclose()

    async def get_session_status(self, session_id: str) -> str:
        """Fetch current assistant status from the session. Returns an HTML status string."""
        try:
            # Get more messages to see the flow of tool calls/responses
            r = await self._http.get(f"/session/{session_id}/message", params={"limit": "10"})
            if r.status_code != 200:
                return "🧠 <i>BMO is thinking...</i>"
            
            msgs = r.json()
            if not msgs:
                return "🧠 <i>BMO is preparing...</i>"

            # Only check the LATEST message for status
            msg = msgs[-1]
            info = msg.get("info", {})
            role = info.get("role")
            parts = msg.get("parts", [])
            
            if role == "tool":
                # This is a tool response message
                for p in parts:
                    if p.get("type") == "tool_response":
                        name = p.get("name", "tool")
                        is_error = p.get("isError", False)
                        status = "❌ Failed" if is_error else "✅ Done"
                        status_str = f"⚙️ <b>{name}</b>: {status}"
                        
                        # Log to terminal if changed
                        if not hasattr(self, '_last_log') or self._last_log != status_str:
                            self._last_log = status_str
                            print(f"[BMO STATUS] {status_str.replace('<b>','').replace('</b>','')}")
                            
                        return status_str
            
            if role == "assistant":
                if not parts:
                    return "🧠 <i>BMO is thinking...</i>"
                
                # Check for tool calls first (highest priority for status)
                for p in reversed(parts):
                    if p.get("type") == "tool_call":
                        name = p.get("name")
                        args = p.get("arguments", "")
                        # Truncate args for display
                        arg_snippet = str(args)[:40] + "..." if len(str(args)) > 40 else str(args)
                        status_str = f"🛠️ <b>Using {name}</b>\n<code>{arg_snippet}</code>"
                        
                        # Log to terminal if changed
                        if not hasattr(self, '_last_log') or self._last_log != status_str:
                            self._last_log = status_str
                            print(f"[BMO TOOL] {name}({arg_snippet})")
                            
                        return status_str
                
                # Show reasoning text for UX (user wants to see what model is thinking)
                for p in reversed(parts):
                    if p.get("type") == "reasoning" and p.get("text"):
                        snippet = p.get("text").strip()[:80].replace("\n", " ")
                        status_str = f"🧠 <i>{snippet}...</i>"
                        return status_str
                
                # Fallback to text snippets
                for p in reversed(parts):
                    if p.get("type") == "text" and p.get("text") and not p.get("synthetic"):
                        snippet = p.get("text").strip()[:60].replace("\n", " ")
                        status_str = f"✍️ <i>{snippet}...</i>"
                        return status_str

            return "🧠 <i>BMO is thinking...</i>"
        except Exception as e:
            logger.debug("Error in get_session_status: %s", e)
            return "🧠 <i>BMO is thinking...</i>"

    # ── Provider API verification map ──────────────────────────────────────────
    # Maps provider_id → (api_base_url, env_key_name, test_endpoint, auth_header)
    PROVIDER_API_MAP = {
        "groq":       ("https://api.groq.com/openai/v1",        "GROQ_API_KEY",       "/models",        "Bearer"),
        "openai":     ("https://api.openai.com/v1",              "OPENAI_API_KEY",     "/models",        "Bearer"),
        "anthropic":  ("https://api.anthropic.com/v1",           "ANTHROPIC_API_KEY",  "/models",        "x-api-key"),
        "deepseek":   ("https://api.deepseek.com/v1",            "DEEPSEEK_API_KEY",   "/models",        "Bearer"),
        "openrouter": ("https://openrouter.ai/api/v1",           "OPENROUTER_API_KEY", "/models",        "Bearer"),
        "google":     ("https://generativelanguage.googleapis.com", "GOOGLE_API_KEY",   "/v1beta/models", "query"),
        "mistral":    ("https://api.mistral.ai/v1",              "MISTRAL_API_KEY",    "/models",        "Bearer"),
        "xai":        ("https://api.x.ai/v1",                    "XAI_API_KEY",        "/models",        "Bearer"),
    }

    async def _verify_key_directly(self, provider_id: str, env: dict) -> tuple[bool, str]:
        """Try to verify an API key via a direct call to the provider's API."""
        provider_cfg = self.PROVIDER_API_MAP.get(provider_id)
        if not provider_cfg:
            return False, ""  # No direct verification method for this provider

        base_url, key_name, endpoint, auth_type = provider_cfg
        api_key = env.get(key_name)
        if not api_key:
            return False, ""

        try:
            headers = {"Content-Type": "application/json"}
            url = f"{base_url}{endpoint}"

            if auth_type == "query":
                url = f"{url}?key={api_key}"
            elif auth_type == "x-api-key":
                headers["x-api-key"] = api_key
            else:
                headers["Authorization"] = f"{auth_type} {api_key}"

            async with httpx.AsyncClient(timeout=15.0) as client:
                r = await client.get(url, headers=headers)
                if r.status_code == 200:
                    return True, "Verification successful."
                elif r.status_code == 401:
                    return False, "Invalid API key — provider returned 401 Unauthorized."
                elif r.status_code == 403:
                    return False, "API key lacks permission — provider returned 403 Forbidden."
                else:
                    return False, f"Provider returned HTTP {r.status_code}."
        except httpx.TimeoutException:
            return False, "Provider API timed out."
        except Exception as e:
            logger.debug("Direct key verification failed for %s: %s", provider_id, e)
            return False, ""

    async def test_provider_key(self, provider_id: str, model_id: str, env: dict) -> tuple[bool, str]:
        """
        Tests an API key by making a minimal request with a short timeout.
        Returns (is_success, detail_message)
        """
        # 1. Try direct provider API verification first (more reliable)
        direct_success, direct_message = await self._verify_key_directly(provider_id, env)
        if direct_message:
            return direct_success, direct_message

        # 2. Fall back to OpenCode session-based test
        try:
            session_id = await self.create_session(provider_id, model_id, env=env)
            if not session_id:
                return False, "Failed to initialize test session on OpenCode server."

            payload = {
                "parts": [{"type": "text", "text": "respond only with 'ok'"}]
            }
            r = await self._http.post(f"/session/{session_id}/message", json=payload, timeout=30.0)
            if r.status_code not in (200, 201):
                return False, f"Server error ({r.status_code}): {r.text[:100]}"

            for _ in range(60):
                await asyncio.sleep(1)
                r_poll = await self._http.get(f"/session/{session_id}/message", params={"limit": "5"})
                if r_poll.status_code == 200:
                    msgs = r_poll.json()
                    for m in msgs:
                        role = m.get("info", {}).get("role")
                        if role == "assistant":
                            parts = m.get("parts", [])
                            text_parts = [p.get("text", "") for p in parts if p.get("type") == "text" and not p.get("synthetic")]
                            if any(text_parts):
                                return True, "Verification successful."

                            error_parts = [p.get("message", "Unknown error") for p in parts if p.get("type") == "error"]
                            if error_parts:
                                return False, f"Provider Error: {error_parts[0]}"

            return False, "Test timed out. The provider took too long to respond (likely invalid key or network issue)."

        except httpx.TimeoutException:
            return False, "Request timed out. Check your internet connection or the API key status."
        except Exception as e:
            logger.error("Test key error: %s", e)
            return False, f"System error during test: {str(e)}"

    async def close(self):
        await self._http.aclose()

