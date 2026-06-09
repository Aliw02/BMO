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
from typing import Optional, Callable, Awaitable

from core.worker_manager import WorkerManager

from config.settings import OPENCODE_BASE_URL, OPENCODE_TIMEOUT, OPENCODE_POLL_INTERVAL, OPENCODE_POLL_TIMEOUT, MEMORY_FILE, BMO_FILE, USER_FILE

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
            trust_env=False,
        )
        self._memory_cache: Optional[str] = None
        self._memory_cache_time: float = 0
        self._agents_cache: Optional[list] = None
        self._agents_cache_time: float = 0
        self._memory_path = str(MEMORY_FILE)
        self._bmo_cache: Optional[str] = None
        self._bmo_cache_time: float = 0
        self._user_cache: Optional[str] = None
        self._user_cache_time: float = 0
        self._worker: Optional[WorkerManager] = None
        self._last_alive_error: Optional[str] = None

    def set_worker_manager(self, worker: WorkerManager):
        self._worker = worker

    async def _check_port_open(self, host: str = "127.0.0.1", port: int = 4800, timeout: float = 2.0) -> bool:
        """Raw socket check — lightweight, no HTTP dependency."""
        try:
            import socket as _sock
            _, _, port_str = OPENCODE_BASE_URL.rpartition(":")
            port = int(port_str) if port_str.isdigit() else port
            _, _, host_str = OPENCODE_BASE_URL.rstrip("/").rpartition("//")
            host = host_str.split(":")[0] or host
            s = _sock.socket(_sock.AF_INET, _sock.SOCK_STREAM)
            s.settimeout(timeout)
            result = s.connect_ex((host, port))
            s.close()
            return result == 0
        except Exception:
            return False

    async def _check_http_alive(self, timeout: float = 5.0) -> tuple[bool, Optional[str]]:
        """HTTP health check. Returns (is_up, error_message)."""
        for attempt in range(3):
            try:
                r = await self._http.get("/session", timeout=timeout)
                if r.status_code < 500:
                    return True, None
                return False, f"Server returned HTTP {r.status_code}"
            except httpx.ConnectError as e:
                if attempt < 2:
                    await asyncio.sleep(1)
                    continue
                return False, f"Connection refused ({e})"
            except httpx.TimeoutException as e:
                if attempt < 2:
                    await asyncio.sleep(1)
                    continue
                return False, f"Connection timed out ({e})"
            except Exception as e:
                if attempt < 2:
                    await asyncio.sleep(1)
                    continue
                return False, f"{type(e).__name__}: {e}"
        return False, "All retries exhausted"

    async def is_alive(self) -> bool:
        """Lightweight check if the server is responding.
        
        Strategy: HTTP health check (3 retries) → raw socket fallback.
        """
        http_up, err = await self._check_http_alive()
        if http_up:
            self.is_connected = True
            self._last_alive_error = None
            return True

        # Fallback: if port is open but HTTP failed, keep the error for debugging
        port_open = await self._check_port_open()
        if port_open:
            self._last_alive_error = err
        else:
            self._last_alive_error = "Port not open — server is not running"
        logger.error("is_alive check failed for %s: %s", OPENCODE_BASE_URL, self._last_alive_error)
        self.is_connected = False
        return False

    async def connect(self) -> bool:
        """Verify that opencode serve is reachable."""
        http_up, err = await self._check_http_alive(timeout=10.0)
        if http_up:
            self.is_connected = True
            self._last_alive_error = None
            logger.info("Connected to opencode serve at %s", OPENCODE_BASE_URL)
        else:
            self._last_alive_error = err
            logger.error("Cannot reach opencode serve: %s", err)
            self.is_connected = False
        return self.is_connected

    async def diagnose_connection(self) -> dict:
        """Full connection diagnostics. Tries multiple endpoints and gathers all info."""
        result = {
            "base_url": OPENCODE_BASE_URL,
            "port_open": False,
            "endpoints": {},
            "error": None,
        }
        # 1. Raw socket check
        port_open = await self._check_port_open()
        result["port_open"] = port_open
        
        # 2. Try multiple endpoints
        endpoints = ["/session", "/provider", "/agent", "/", "/health"]
        for endpoint in endpoints:
            for attempt in range(2):
                try:
                    r = await self._http.get(endpoint, timeout=3.0)
                    result["endpoints"][endpoint] = {
                        "status": r.status_code,
                        "body_preview": r.text[:200],
                    }
                    break
                except httpx.ConnectError as e:
                    result["endpoints"][endpoint] = {"error": f"Connection refused: {e}"}
                    break
                except httpx.TimeoutException:
                    if attempt == 0:
                        await asyncio.sleep(0.5)
                        continue
                    result["endpoints"][endpoint] = {"error": "Timed out after retry"}
                except Exception as e:
                    result["endpoints"][endpoint] = {"error": f"{type(e).__name__}: {e}"}
                    break
        
        if not port_open and all("error" in v for v in result["endpoints"].values()):
            result["error"] = "Port closed — OpenCode server is not running"
        elif all("error" in v for v in result["endpoints"].values()):
            # Port open but all HTTP fail
            first_err = next((v["error"] for v in result["endpoints"].values() if "error" in v), "Unknown")
            result["error"] = f"Port open but HTTP fails: {first_err}"
        else:
            working = [ep for ep, v in result["endpoints"].items() if v.get("status", 0) < 500]
            if working:
                result["error"] = None
            else:
                codes = {ep: v.get("status") for ep, v in result["endpoints"].items() if "status" in v}
                result["error"] = f"All endpoints returned error status: {codes}"
        
        return result

    async def create_session(self, provider_id: Optional[str] = None, model_id: Optional[str] = None, env: Optional[dict] = None, agent: str = "build") -> Optional[str]:
        """Create a new opencode session and return its ID."""
        try:
            # Force defaults if None/Empty
            p_id = provider_id if (provider_id and str(provider_id) != "None") else "opencode"
            m_id = model_id if (model_id and str(model_id) != "None") else "big-pickle"
            
            # Map 'default' to 'build' to prevent server-side agent switching errors
            agent_to_use = "build" if (not agent or agent == "default") else agent

            # The server expects a nested 'model' object and optional 'agent'
            payload = {
                "model": {
                    "id": m_id,
                    "providerID": p_id
                },
                "agent": agent_to_use
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
            logger.info("--- OPENCODE SESSION CREATED: %s (Model: %s, Agent: %s) ---", session_id, actual_model, agent_to_use)
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

    async def abort_session(self, session_id: str) -> bool:
        """Send abort signal to the server to stop the current in-progress LLM stream."""
        try:
            r = await self._http.post(f"/session/{session_id}/abort", timeout=5.0)
            return r.status_code < 400
        except Exception as e:
            logger.warning("abort_session failed (non-fatal): %s", e)
            return False

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

    async def _get_bmo_identity(self) -> str:
        """Read BMO.md with caching."""
        now = time.monotonic()
        if hasattr(self, '_bmo_cache') and self._bmo_cache is not None and (now - self._bmo_cache_time) < self._CACHE_TTL:
            return self._bmo_cache
        try:
            bmo_path = str(BMO_FILE)
            if os.path.exists(bmo_path):
                with open(bmo_path, "r", encoding="utf-8") as f:
                    self._bmo_cache = f.read()
            else:
                self._bmo_cache = ""
        except Exception as e:
            logger.warning("Could not read BMO.md: %s", e)
            self._bmo_cache = ""
        self._bmo_cache_time = now
        return self._bmo_cache

    async def _get_user_profile(self) -> str:
        """Read USER.md with caching."""
        now = time.monotonic()
        if hasattr(self, '_user_cache') and self._user_cache is not None and (now - self._user_cache_time) < self._CACHE_TTL:
            return self._user_cache
        try:
            user_path = str(USER_FILE)
            if os.path.exists(user_path):
                with open(user_path, "r", encoding="utf-8") as f:
                    self._user_cache = f.read()
            else:
                self._user_cache = ""
        except Exception as e:
            logger.warning("Could not read USER.md: %s", e)
            self._user_cache = ""
        self._user_cache_time = now
        return self._user_cache

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
        session_uuid: Optional[str] = None,
        on_token: Optional[Callable[[str], None]] = None,
        on_activity: Optional[Callable[[str, str], None]] = None,
        on_permission: Optional[Callable[[str, str, list], str]] = None,
        on_question: Optional[Callable[[str, dict], Awaitable[list]]] = None,
    ) -> str:
        """Send a query via worker process, falling back to direct HTTP."""
        if self._worker and self._worker.is_alive:
            return await self._send_via_worker(
                query=query, session_id=session_id, files=files,
                provider_id=provider_id, model_id=model_id,
                active_mode=active_mode, active_agent=active_agent,
                provider_env=provider_env, chat_id=chat_id,
                session_uuid=session_uuid, on_token=on_token,
            )
        return await self._send_direct(
            query=query, session_id=session_id, files=files,
            provider_id=provider_id, model_id=model_id,
            active_mode=active_mode, active_agent=active_agent,
            provider_env=provider_env, chat_id=chat_id,
            session_uuid=session_uuid, on_token=on_token,
            on_activity=on_activity,
            on_permission=on_permission,
            on_question=on_question,
        )

    async def _send_direct(
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
        session_uuid: Optional[str] = None,
        on_token: Optional[Callable[[str], None]] = None,
        on_activity: Optional[Callable[[str, str], None]] = None,
        on_permission: Optional[Callable[[str, str, list], str]] = None,
        on_question: Optional[Callable[[str, dict], Awaitable[list]]] = None,
    ) -> str:
        """
        Send a query directly via HTTP. Fallback when worker is unavailable.
        Creates a new session if session_id is None.
        Accepts optional files list: [{"path": "...", "mime": "..."}]
        Returns the assistant's Telegram-HTML-formatted response.
        """
        if not self.is_connected:
            connected = await self.connect()
            if not connected:
                return "❌ Error: Could not connect to OpenCode backend. Is the server running?"

        if not session_id:
            # Inject keys if provided
            session_id = await self.create_session(provider_id, model_id, env=provider_env, agent=active_agent)
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

        bmo_content = await self._get_bmo_identity()
        bmo_block = f"\n\n[BMO IDENTITY]\n{bmo_content}\n[END BMO IDENTITY]" if bmo_content else ""
        identity_instruction = _system_config.get("identity_instruction", "")

        user_content = await self._get_user_profile()
        user_block = f"\n\n[USER PROFILE]\n{user_content}\n[END USER PROFILE]" if user_content else ""
        profile_instruction = _system_config.get("profile_instruction", "")

        full_system_context = system_base + memory_instruction + identity_instruction + bmo_block + profile_instruction + user_block + tool_instruction + chat_context + skills_block + memory_block + security_warning + tool_context + f"\n\nCURRENT PROTOCOL: {mode_instruction}" + _system_config["anti_loop"] + agent_instruction

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

                r = await asyncio.wait_for(
                    self._http.post(f"/session/{sid}/message", json=payload),
                    timeout=float(OPENCODE_TIMEOUT),
                )
                return r, None
            except asyncio.TimeoutError as e:
                logger.error("Hard timeout (asyncio.wait_for) sending to session %s", sid)
                return None, f"Request hard-cancelled after {OPENCODE_TIMEOUT}s (asyncio.wait_for)"
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
            session_id = await self.create_session(provider_id, model_id, env=provider_env, agent=active_agent)
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
            trust_env=False,
        )
        # Track which part indices we've already emitted activity for (non-thinking parts only)
        _seen_part_indices: set = set()
        # Track last thinking text emitted to avoid redundant fires
        _last_thinking: str = ""
        _answered_questions = set()
        stop_event = asyncio.Event()

        async def bg_poller():
            poll_client_bg = httpx.AsyncClient(
                base_url=OPENCODE_BASE_URL,
                headers=HEADERS,
                timeout=10.0,
                trust_env=False,
            )
            try:
                while not stop_event.is_set():
                    # 1. Check for pending MCP tool permissions matching this chat_id
                    from core.shared_state import pending_permissions
                    cli_keys = [k for k in list(pending_permissions.keys()) if k[0] == chat_id]
                    if cli_keys and on_permission:
                        for key in cli_keys:
                            if pending_permissions.get(key) and pending_permissions[key]["result"] is None:
                                scope = key[1]
                                logger.info("Handling pending permission for %s in CLI (bg)", scope)
                                ans = await on_permission(str(key), "mcp_tool_permission", [scope])
                                pending_permissions[key]["result"] = "granted" if ans in ("allow", "always") else "denied"
                                pending_permissions[key]["event"].set()

                    # 2. Check for interactive questions
                    try:
                        r = await poll_client_bg.get(
                            f"/session/{session_id}/message",
                            params={"limit": "20"},
                        )
                        if r.status_code == 200:
                            messages = r.json()
                            if messages:
                                msg = messages[-1]
                                info = msg.get("info", {})
                                role = info.get("role")
                                if role == "assistant":
                                    parts = msg.get("parts", [])
                                    for p in parts:
                                        if p.get("type") == "tool" and p.get("name") == "question":
                                            call_id = p.get("callID")
                                            state = p.get("state", {})
                                            if state.get("status") == "running" and call_id not in _answered_questions and on_question:
                                                logger.info("Interactive question received: %s (bg)", call_id)
                                                _answered_questions.add(call_id)
                                                try:
                                                    answers = await on_question(call_id, state)
                                                    response_payload = {
                                                        "parts": [
                                                            {
                                                                "type": "tool-response",
                                                                "callID": call_id,
                                                                "name": "question",
                                                                "content": json.dumps({"answers": answers})
                                                            }
                                                        ]
                                                    }
                                                    r_post = await poll_client_bg.post(
                                                        f"/session/{session_id}/message",
                                                        json=response_payload
                                                    )
                                                    r_post.raise_for_status()
                                                except Exception as qe:
                                                    logger.error("Failed to prompt or send question response (bg): %s", qe)
                                                break
                    except Exception as e:
                        logger.debug("BG poller request failed (non-fatal): %s", e)

                    # Poll every 500ms
                    try:
                        await asyncio.wait_for(stop_event.wait(), timeout=0.5)
                    except asyncio.TimeoutError:
                        continue
            finally:
                await poll_client_bg.aclose()

        bg_task = asyncio.create_task(bg_poller())

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
                    
                    # --- Activity tracking: emit events for new parts ---
                    if on_activity:
                        # Accumulate full thinking text across all reasoning parts on this poll
                        accumulated_thinking = "\n\n".join(
                            (p.get("text", "") or "").strip()
                            for p in parts
                            if p.get("type") in ("reasoning", "thinking")
                            and not p.get("synthetic")
                            and (p.get("text", "") or "").strip()
                        )
                        if accumulated_thinking and accumulated_thinking != _last_thinking:
                            _last_thinking = accumulated_thinking
                            on_activity("thinking", accumulated_thinking)

                        for idx, p in enumerate(parts):
                            if idx in _seen_part_indices:
                                continue
                            _seen_part_indices.add(idx)
                            p_type = p.get("type", "")
                            if p.get("synthetic"):
                                continue

                            if p_type == "step-start":
                                on_activity("step", "🔄 New reasoning step")
                            elif p_type == "tool-use" or p_type == "tool_use":
                                tool_name = p.get("name") or p.get("tool", {}).get("name", "unknown")
                                tool_input = p.get("input") or p.get("tool", {}).get("input", {})
                                detail = str(tool_input)[:120].replace("\n", " ") if tool_input else ""
                                on_activity("tool_call", f"🔧 {tool_name}({detail})")
                            elif p_type == "tool-result" or p_type == "tool_response":
                                tool_name = p.get("name", "tool")
                                is_error = p.get("isError", False)
                                icon = "❌" if is_error else "✅"
                                on_activity("tool_result", f"{icon} {tool_name} done")
                            elif p_type == "text" and not p.get("synthetic"):
                                on_activity("text_start", "✍️ Writing response...")
                    
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
                    
                    # Fire streaming callback with partial text on every poll
                    if text and on_token:
                        on_token(text)

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
            # Abort server-side stream on timeout to prevent the server from
            # continuing to process a request nobody is listening to anymore.
            if session_id:
                try:
                    await self.abort_session(session_id)
                except Exception:
                    pass
            return "Error: Request timed out after 30 minutes."
        finally:
            stop_event.set()
            try:
                await bg_task
            except Exception:
                pass
            await poll_client.aclose()

    async def _send_via_worker(
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
        session_uuid: Optional[str] = None,
        on_token: Optional[Callable[[str], None]] = None,
    ) -> str:
        """Send query via worker process (fully async, never blocks the event loop)."""
        if not self.is_connected:
            connected = await self.connect()
            if not connected:
                return "❌ Error: Could not connect to OpenCode backend. Is the server running?"

        if not session_id:
            session_id = await self.create_session(provider_id, model_id, env=provider_env, agent=active_agent)
            if not session_id:
                return "Error: Could not create OpenCode session."
        
        self.last_session_id = session_id

        memory_content = await self._get_memory_content()

        mode_prompts = _system_config["mode_prompts"]
        mode_instruction = mode_prompts.get(active_mode, mode_prompts["execute"])
        anti_loop = _system_config["anti_loop"]
        agent_prompts = _system_config["agent_prompts"]
        agent_instruction = agent_prompts.get(active_agent, "")

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

        chat_context = ""
        if chat_id:
            chat_context = f"\n\n[USER_CONTEXT]\nCURRENT_CHAT_ID: {chat_id}\nCURRENT_SESSION_ID: {session_uuid or 'unknown'}\n[END USER_CONTEXT]"
            chat_context += "\n\n<b>FILE STORAGE</b>: When creating files, save them inside <code>data/files/</code>. Use date-based subfolders: <code>data/files/{{YYYY-MM-DD}}/{{CURRENT_SESSION_ID}}_{{HHMMSS}}_{{filename}}</code> so files are linked to sessions and dates."
        
        tool_instruction = _system_config["tool_instruction"]

        project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
        system_base = TELEGRAM_SYSTEM_PROMPT.replace("{PROJECT_ROOT}", project_root)

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

        tool_context = await self._build_tool_context()
        memory_content = await self._get_memory_content()
        memory_block = f"\n\n[LONG-TERM MEMORY]\n{memory_content}\n[END MEMORY]" if memory_content else ""
        memory_instruction = _system_config["memory_instruction"]

        bmo_content = await self._get_bmo_identity()
        bmo_block = f"\n\n[BMO IDENTITY]\n{bmo_content}\n[END BMO IDENTITY]" if bmo_content else ""
        identity_instruction = _system_config.get("identity_instruction", "")

        user_content = await self._get_user_profile()
        user_block = f"\n\n[USER PROFILE]\n{user_content}\n[END USER PROFILE]" if user_content else ""
        profile_instruction = _system_config.get("profile_instruction", "")

        full_system_context = system_base + memory_instruction + identity_instruction + bmo_block + profile_instruction + user_block + tool_instruction + chat_context + skills_block + memory_block + security_warning + tool_context + f"\n\nCURRENT PROTOCOL: {mode_instruction}" + anti_loop + agent_instruction

        payload = {
            "parts": [
                {
                    "type": "text",
                    "text": full_system_context,
                    "synthetic": True,
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

        base_url = OPENCODE_BASE_URL.rstrip("/")

        return await self._worker.send_query(
            session_id=session_id,
            payload=payload,
            base_url=base_url,
            callback=on_token,
        )

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

    async def send_simple_query(self, query: str, timeout: float = 120.0) -> str:
        """Send a minimal query WITHOUT the full BMO system context.

        Used for lightweight tasks like summarization where the full
        system prompt + memory + tools would be wasteful overhead.
        Creates a temp session, sends just the query, polls, returns text.
        """
        if not self.is_connected:
            connected = await self.connect()
            if not connected:
                raise ConnectionError("Could not connect to OpenCode backend")

        session_id = await self.create_session()
        if not session_id:
            raise RuntimeError("Could not create OpenCode session")

        payload = {"parts": [{"type": "text", "text": query}]}

        r = await self._http.post(f"/session/{session_id}/message", json=payload, timeout=timeout)
        if r.status_code not in (200, 201):
            raise RuntimeError(f"Server returned {r.status_code}: {r.text[:200]}")

        deadline = time.monotonic() + timeout
        poll_client = httpx.AsyncClient(
            base_url=OPENCODE_BASE_URL,
            headers=HEADERS,
            timeout=10.0,
            trust_env=False,
        )
        try:
            while time.monotonic() < deadline:
                await asyncio.sleep(OPENCODE_POLL_INTERVAL)
                try:
                    r = await poll_client.get(f"/session/{session_id}/message", params={"limit": "20"})
                    if r.status_code != 200:
                        continue
                    messages = r.json()
                    if not messages:
                        continue
                    msg = messages[-1]
                    info = msg.get("info", {})
                    role = info.get("role")
                    if role != "assistant":
                        continue
                    parts = msg.get("parts", [])
                    has_finish = any(p.get("type") == "step-finish" for p in parts)
                    finish_reason = info.get("finish")
                    is_complete = has_finish or finish_reason in ("stop", "error", "length")

                    error_parts = [p for p in parts if p.get("type") == "error"]
                    if error_parts:
                        raise RuntimeError(error_parts[0].get("message", "Unknown error"))

                    text_parts = [p.get("text", "") for p in parts if p.get("type") == "text" and not p.get("synthetic")]
                    text = "\n".join(text_parts).strip()

                    if text and is_complete:
                        self.last_session_id = session_id
                        return text
                except httpx.TimeoutException:
                    continue
                except Exception as e:
                    logger.debug("Poll error in send_simple_query: %s", e)
                    continue

            raise TimeoutError(f"Simple query timed out after {timeout}s")
        finally:
            await poll_client.aclose()

    async def close(self):
        await self._http.aclose()

