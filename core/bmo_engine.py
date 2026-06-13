"""
Unified business logic engine for all BMO interfaces (CLI, Telegram, Webchat).
Centralizes session management, model selection, SQLite DB queries, and OpenCode client calls.
"""

import asyncio
import logging
import os
import time
import json
from uuid import uuid4
from datetime import datetime
from typing import Optional, List, Dict, AsyncGenerator

from config.settings import OPENCODE_BASE_URL, BFP_RELAY_URL, BFP_TRANSPORT_PORT, BFP_A2A_PORT, OWNER_ID, ALLOWED_USER_IDS, BMO_HOME
from core.bot_client import OpenCodeBotClient
from core.worker_manager import WorkerManager
from models.chat_models import ChatSession, ChatMessage
from storage.storage import get_storage
from core.bfp_agent import BFPAgent

logger = logging.getLogger(__name__)

# Load shared system prompt config
_config_path = os.path.join(os.path.dirname(__file__), "..", "config", "system-prompt.json")
try:
    with open(_config_path, "r", encoding="utf-8") as _f:
        _system_config = json.load(_f)
except Exception as e:
    logger.error("Failed to load system prompt config: %s", e)
    _system_config = {}


class BMOEngine:
    def __init__(self, db_path: Optional[str] = None, worker_backend: Optional[str] = None, user_cwd: Optional[str] = None):
        if db_path:
            from storage.sqlite_storage import SQLiteStorage
            self.storage = SQLiteStorage(db_path=db_path)
        else:
            self.storage = get_storage()
        self.storage.sync_session_groups(OWNER_ID, ALLOWED_USER_IDS)
        self.client = OpenCodeBotClient()
        self.worker_manager = WorkerManager(backend=worker_backend)
        self.client.set_worker_manager(self.worker_manager)
        self._running_tasks: Dict[str, asyncio.Task] = {}
        self._worker_started = False
        self._bfp_started = False
        self.bfp = BFPAgent()
        self.user_cwd = user_cwd
        self._features_dir = BMO_HOME / "data" / "features"
        self._features_dir.mkdir(parents=True, exist_ok=True)
        self._setup_bfp_handler()

    def _setup_bfp_handler(self):
        """Register the BFP task handler so incoming delegated tasks are processed through OpenCode."""
        async def _bfp_task_handler(source_did: str, task_data: dict) -> str:
            query = task_data.get("query", str(task_data))
            action = task_data.get("action", "delegate")
            logger.info("BFP task from %s (action=%s): %s", source_did, action, query[:200])
            try:
                result = await self.client.send_simple_query(query, timeout=300.0)
                return result
            except Exception as e:
                logger.error("BFP task processing failed: %s", e)
                return f"Error processing task: {e}"
        self.bfp.set_task_handler(_bfp_task_handler)

    async def ensure_worker(self):
        # Don't start the worker if the client has it disabled (e.g. CLI uses
        # `_send_direct` and sets `client._worker = None` to skip the worker).
        # Starting it anyway would spawn a stranded subprocess whose pipes
        # fire `__del__` warnings on interpreter shutdown.
        if not self.client._worker:
            return
        # Start the worker if not started yet, OR if it was killed by a
        # previous cancel (e.g. SIGINT in CLI). is_alive is the source of
        # truth for whether the subprocess is actually running.
        if not self._worker_started or not self.worker_manager.is_alive:
            await self.worker_manager.start()
            self._worker_started = True
        if not self._bfp_started:
            _connect_did = os.environ.get("BFP_CONNECT_DID", "").strip()
            if _connect_did:
                asyncio.create_task(self.bfp.start(
                    bfp_port=BFP_TRANSPORT_PORT,
                    a2a_port=BFP_A2A_PORT,
                ))
            else:
                asyncio.create_task(self.bfp.start(
                    bfp_port=BFP_TRANSPORT_PORT,
                    a2a_port=BFP_A2A_PORT,
                    relay_url=BFP_RELAY_URL,
                ))
            self._bfp_started = True

    async def get_or_create_session(self, chat_id: int, user_id: int, username: Optional[str] = None) -> ChatSession:
        """Loads the active session for a chat. If none exists, creates a fresh one."""
        session = self.storage.load_session(chat_id)
        if session:
            return session
        return await self.create_new_session(chat_id, user_id, username)

    async def create_new_session(self, chat_id: int, user_id: int, username: Optional[str] = None) -> ChatSession:
        """Creates a brand new session, auto-summarizing the previous one if applicable.

        Soft-reset semantics (matches Claude Code / OpenCode CLI /new behavior):
          - Local DB history is wiped (fresh chat row, empty messages).
          - Backend model context is wiped via delete_messages (fresh turn 1).
          - opencode_session_id is PRESERVED so the system prompt stays warm.
          - Provider/model/agent/mode inherit from the previous session if available.
        """
        # 1. Summarize old session if it contains messages and hasn't been summarized
        old_session = self.storage.load_session(chat_id)

        # 2. Capture preserved backend state from the old session (if any)
        # NOTE: opencode_session_id is deliberately NOT preserved.
        # delete_messages() only clears the message list on the backend,
        # NOT the model provider's cached context window. Preserving the
        # session ID causes the model to remember old conversations after /new.
        # Setting it to None forces a fresh backend session with zero context.
        preserved_opencode_sid = None
        inherited_provider = os.getenv("OPENCODE_PROVIDER", "opencode")
        inherited_model = os.getenv("OPENCODE_MODEL", "big-pickle")
        inherited_mode = "execute"
        inherited_agent = "default"

        if old_session:
            inherited_provider = old_session.metadata.get("provider_id", inherited_provider)
            inherited_model = old_session.metadata.get("model_id", inherited_model)
            inherited_mode = old_session.metadata.get("active_mode", inherited_mode)
            inherited_agent = old_session.metadata.get("active_agent", inherited_agent)

            if old_session.messages and not old_session.get_summary():
                await self.summarize_session(old_session.session_id)

        # 3. Clear client-side cached session ID so next request creates fresh backend context
        self.client.last_session_id = None

        # 4. Setup a new ChatSession row, inheriting backend + provider state
        now = time.time()
        new_sid = str(uuid4())

        new_session = ChatSession(
            chat_id=chat_id,
            user_id=user_id,
            username=username,
            created_at=now,
            updated_at=now,
            messages=[],
            metadata={
                "opencode_session_id": preserved_opencode_sid,
                "provider_id": inherited_provider,
                "model_id": inherited_model,
                "active_mode": inherited_mode,
                "active_agent": inherited_agent
            },
            title=f"Session {datetime.now().strftime('%y%m%d_%H%M')}",
            session_id=new_sid
        )

        self.storage.save_session(new_session)
        self.storage.set_active_session(chat_id, new_sid)
        return new_session

    async def reset_opencode_session(self, chat_id: int):
        """Clears the cached OpenCode session ID so the next request gets a fresh server-side session.
        Call this after a Ctrl+C cancellation to avoid queuing behind the abandoned request."""
        session = self.storage.load_session(chat_id)
        if session:
            session.metadata["opencode_session_id"] = None
            self.storage.save_session(session)
        # Also clear client-side cache
        self.client.last_session_id = None

    async def switch_session(self, chat_id: int, session_id: str) -> Optional[ChatSession]:
        """Switches the active session pointer for a chat."""
        if self.storage.switch_session(chat_id, session_id):
            # Load active session from active_sessions table
            session = self.storage.load_session(chat_id)
            if session:
                # Update global active sessions table
                self._update_global_active_session(chat_id, session_id)
                return session
        return None

    def _update_global_active_session(self, chat_id: int, session_id: str):
        """Internal helper to update active_sessions metadata for global synchronization."""
        try:
            conn = self.storage._conn()
            # Ensure table has interface and updated_at (it should have been migrated in sqlite_storage.py)
            conn.execute(
                "INSERT OR REPLACE INTO active_sessions (chat_id, session_id) VALUES (?, ?)",
                (chat_id, session_id)
            )
            conn.commit()
        except Exception as e:
            logger.error("Failed to sync active session metadata: %s", e)

    def shutdown(self):
        """Shut down the worker manager and BFP agent (sync - fire-and-forget for atexit)."""
        if hasattr(self, 'bfp') and self.bfp.is_running:
            try:
                loop = asyncio.get_event_loop()
                if loop.is_running():
                    loop.create_task(self.bfp.stop())
                else:
                    loop.run_until_complete(self.bfp.stop())
            except RuntimeError:
                loop = asyncio.new_event_loop()
                loop.run_until_complete(self.bfp.stop())
                loop.close()
        if hasattr(self, 'worker_manager'):
            try:
                loop = asyncio.get_event_loop()
                if loop.is_running():
                    loop.create_task(self.worker_manager.shutdown())
                else:
                    loop.run_until_complete(self.worker_manager.shutdown())
            except RuntimeError:
                loop = asyncio.new_event_loop()
                loop.run_until_complete(self.worker_manager.shutdown())
                loop.close()

    async def list_sessions(self, chat_id: int) -> List[dict]:
        """Lists all saved sessions for a chat."""
        return self.storage.list_chat_sessions(chat_id)

    async def set_session_title(self, session_id: str, title: str) -> bool:
        """Sets a custom title for a session."""
        try:
            self.storage.update_session_title(session_id, title)
            return True
        except Exception as e:
            logger.error("Failed to set session title: %s", e)
            return False

    async def clear_session_history(self, chat_id: int) -> bool:
        """Resets the history of the active session in DB and OpenCode server."""
        session = self.storage.load_session(chat_id)
        if not session:
            return False

        # Clear backend messages if OpenCode session is active
        opencode_sid = session.metadata.get("opencode_session_id")
        if opencode_sid:
            await self.client.delete_messages(opencode_sid)

        # Remove local database messages for the session
        try:
            conn = self.storage._conn()
            conn.execute("DELETE FROM messages WHERE session_id = ?", (session.session_id,))
            conn.commit()
            return True
        except Exception as e:
            logger.error("Failed to clear DB history: %s", e)
            return False

    async def get_model_info(self, chat_id: int) -> tuple[str, str]:
        """Gets active model's provider and ID."""
        session = self.storage.load_session(chat_id)
        if not session:
            env_p = os.getenv("OPENCODE_PROVIDER", "opencode")
            env_m = os.getenv("OPENCODE_MODEL", "big-pickle")
            return env_p, env_m

        p_id = session.metadata.get("provider_id")
        m_id = session.metadata.get("model_id")
        if p_id and m_id:
            return p_id, m_id

        env_p = os.getenv("OPENCODE_PROVIDER")
        env_m = os.getenv("OPENCODE_MODEL")
        if env_p and env_m:
            return env_p, env_m

        return ("opencode", "big-pickle")

    async def set_model(self, chat_id: int, provider_id: str, model_id: str) -> bool:
        """Switches the active model for a chat session."""
        session = self.storage.load_session(chat_id)
        if not session:
            return False

        session.metadata["provider_id"] = provider_id
        session.metadata["model_id"] = model_id
        session.metadata["opencode_session_id"] = None  # Force fresh backend session creation
        self.storage.save_session(session)

        # Sync to environment for global consistency
        os.environ["OPENCODE_PROVIDER"] = provider_id
        os.environ["OPENCODE_MODEL"] = model_id
        return True

    async def list_models(self) -> List[dict]:
        """Fetches available providers and models from OpenCode serve."""
        providers_data = await self.client.get_providers()
        return providers_data.get("all", [])

    async def get_status(self) -> dict:
        """Returns OpenCode server connection status and stats."""
        is_up = await self.client.is_alive()
        stats = self.storage.get_stats()
        return {
            "connected": is_up,
            "stats": stats,
            "base_url": OPENCODE_BASE_URL
        }

    async def compact_session(self, chat_id: int, keep_recent: int = 6) -> str:
        """Summarizes older messages and keeps recent ones in full to reduce context window usage.
        
        Creates a fresh OpenCode backend session and injects compacted history.
        Returns a user-facing status message.
        """
        session = await self.get_or_create_session(chat_id, "system")
        if not session.messages or len(session.messages) <= keep_recent + 2:
            return "Session is short enough — no compaction needed."

        all_msgs = [ChatMessage.from_dict(m) for m in session.messages]
        recent = all_msgs[-keep_recent:]
        older = all_msgs[:-keep_recent]

        # Build summary of older messages
        history_lines = []
        for m in older:
            prefix = "You:" if m.sender == "user" else "Assistant:"
            content = m.content[:500]
            history_lines.append(f"{prefix} {content}")
        history_text = "\n".join(history_lines)

        summary_prompt = (
            "Summarize this conversation concisely in English. "
            "Capture: the main goal, key decisions, code written, and any unresolved items. "
            f"Keep it under 300 words.\n\n{history_text}"
        )

        # Use the session's active model for summarization, not the default big-pickle
        provider_id = session.metadata.get("provider_id")
        model_id = session.metadata.get("model_id")

        try:
            summary = await asyncio.wait_for(
                self.client.send_simple_query(summary_prompt, timeout=120.0,
                                              provider_id=provider_id, model_id=model_id),
                timeout=120.0
            )
        except Exception as e:
            logger.warning("Compact summarization failed: %s", e)
            return f"Could not summarize older messages: {e}"

        # Build compacted context
        recent_lines = []
        for m in recent:
            prefix = "You:" if m.sender == "user" else "Assistant:"
            recent_lines.append(f"{prefix} {m.content}")
        recent_text = "\n".join(recent_lines)

        compact_context = (
            "[COMPACTED SESSION HISTORY]\n"
            "Earlier conversation summary:\n"
            f"{summary}\n\n"
            "[RECENT MESSAGES (kept in full)]\n"
            f"{recent_text}\n"
            "[END COMPACTED HISTORY]"
        )

        # Force a fresh backend session with the compacted context as the first message
        session.metadata["opencode_session_id"] = None
        session.metadata["compacted_context"] = compact_context
        self.storage.save_session(session)
        self.client.last_session_id = None

        # Inject compacted context as a system-level note on next query
        # by adding it as a synthetic assistant message
        self.storage.add_message(
            chat_id, "assistant",
            f"[Session compacted — older messages summarized below]\n{summary}",
            interface="system"
        )

        return f"Session compacted. {len(older)} older messages summarized, {len(recent)} recent messages kept in full."

    async def get_agents(self) -> List[dict]:
        """Fetches available agents/skills from the server."""
        return await self.client.get_agents()

    async def summarize_session(self, session_id: str) -> str:
        """Generates an AI summary of a session and saves it."""
        session = self.storage.get_session_by_id(session_id)
        if not session or not session.messages:
            return ""

        history = session.get_context_text(max_messages=100)
        prompt = (
            "لخص هذه الجلسة البرمجية بشكل احترافي ومفصل باللغة العربية.\n"
            "يجب أن يتضمن الملخص:\n"
            "1. الهدف الرئيسي من الجلسة.\n"
            "2. المشاكل التي تم حلها والكود الذي تم كتابته.\n"
            "3. القرارات التقنية المهمة.\n"
            "4. ما الذي يجب إكماله في الجلسة القادمة.\n\n"
            f"السياق:\n{history}"
        )

        # Use the session's active model for summarization
        p_id = session.metadata.get("provider_id")
        m_id = session.metadata.get("model_id")

        try:
            summary = await asyncio.wait_for(
                self.client.send_query(prompt, active_mode="ask", chat_id=session.chat_id,
                                       provider_id=p_id, model_id=m_id),
                timeout=30.0
            )
            if summary and not summary.startswith("Error"):
                self.storage.update_session_summary(session_id, summary)
                return summary
        except Exception as e:
            logger.warning("Summarization failed for session %s: %s", session_id, e)
        return ""

    async def send_message(
        self,
        chat_id: int,
        user_id: int,
        user_message: str,
        interface: str = "cli",
        files: Optional[list] = None
    ) -> str:
        """Sends a query to OpenCode and records conversation history in SQLite."""
        return await self.send_message_streaming(
            chat_id=chat_id,
            user_id=user_id,
            user_message=user_message,
            interface=interface,
            files=files,
            on_token=None,
        )

    async def send_message_in_session(
        self,
        session: ChatSession,
        user_message: str,
        interface: str = "cli",
        files: Optional[list] = None,
    ) -> str:
        """Like send_message but sends to a specific session object (bypasses get_or_create_session).

        Used by GoalRunner to dispatch subtasks to isolated sessions concurrently.
        """
        self.storage.add_message(session.chat_id, "user", user_message, interface=interface)

        history_messages = session.get_context_text(max_messages=5)
        active_agent = session.metadata.get("active_agent", "default")
        agent_info = None
        if active_agent.startswith("custom_"):
            try:
                agent_id = int(active_agent.split("_")[1])
                agent_info = self.storage.get_custom_agent_by_id(agent_id)
            except Exception:
                pass

        query_with_context = f"[Message Source: {interface.upper()}]\nPrevious context for continuity:\n{history_messages}\n\nLatest User Message: {user_message}"
        if agent_info:
            query_with_context = f"[CUSTOM AGENT SYSTEM PROMPT: {agent_info['system_prompt']}]\n\n{query_with_context}"

        if interface == "cli":
            cli_ctx = _system_config.get("cli_context", "")
            if cli_ctx:
                query_with_context = cli_ctx + "\n\n" + query_with_context
            user_cwd_str = self.user_cwd or os.getcwd()
            path_ctx = (
                f"\n\n[WORKING DIRECTORY]\n"
                f"Local (current project): {user_cwd_str}\n"
                f"Global (BMO features): {self._features_dir}\n"
                f"When creating new files or features, ask the user whether to save them "
                f"to the Local (project) or Global (BMO) path, then write to the chosen absolute path.\n"
                f"[END WORKING DIRECTORY]"
            )
            query_with_context = path_ctx + "\n\n" + query_with_context

        opencode_sid = session.metadata.get("opencode_session_id")
        provider_id, model_id = await self.get_model_info(session.chat_id)
        active_mode = session.metadata.get("active_mode", "execute")

        response = await self.client.send_query(
            query=query_with_context,
            session_id=opencode_sid,
            provider_id=provider_id,
            model_id=model_id,
            active_mode=active_mode,
            active_agent=active_agent,
            chat_id=session.chat_id,
            session_uuid=session.session_id,
            files=files,
        )

        if self.client.last_session_id:
            session.metadata["opencode_session_id"] = self.client.last_session_id
            self.storage.save_session(session)

        self.storage.add_message(session.chat_id, "assistant", response, interface=interface)

        return response

    async def send_message_streaming(
        self,
        chat_id: int,
        user_id: int,
        user_message: str,
        interface: str = "cli",
        files: Optional[list] = None,
        on_token=None,
        on_activity=None,
        on_permission=None,
        on_question=None,
    ) -> str:
        """Sends a query with optional callbacks: on_token(partial_text), on_activity(kind, detail), on_permission(perm_id, perm_type, patterns) -> reply, on_question(call_id, question_data) -> reply."""
        await self.ensure_worker()
        from typing import Callable
        session = await self.get_or_create_session(chat_id, user_id)

        # 1. Save user message to database
        self.storage.add_message(chat_id, "user", user_message, interface=interface)

        # 2. Build history context for continuity
        history_messages = session.get_context_text(max_messages=5)
        active_agent = session.metadata.get("active_agent", "default")
        agent_info = None
        if active_agent.startswith("custom_"):
            try:
                agent_id = int(active_agent.split("_")[1])
                agent_info = self.storage.get_custom_agent_by_id(agent_id)
            except Exception:
                pass

        query_with_context = f"[Message Source: {interface.upper()}]\nPrevious context for continuity:\n{history_messages}\n\nLatest User Message: {user_message}"
        if agent_info:
            query_with_context = f"[CUSTOM AGENT SYSTEM PROMPT: {agent_info['system_prompt']}]\n\n{query_with_context}"

        # Inject CLI interface context to lock BMO identity when using the terminal
        if interface == "cli":
            cli_ctx = _system_config.get("cli_context", "")
            if cli_ctx:
                query_with_context = cli_ctx + "\n\n" + query_with_context
            user_cwd_str = self.user_cwd or os.getcwd()
            path_ctx = (
                f"\n\n[WORKING DIRECTORY]\n"
                f"Local (current project): {user_cwd_str}\n"
                f"Global (BMO features): {self._features_dir}\n"
                f"When creating new files or features, ask the user whether to save them "
                f"to the Local (project) or Global (BMO) path, then write to the chosen absolute path.\n"
                f"[END WORKING DIRECTORY]"
            )
            query_with_context = path_ctx + "\n\n" + query_with_context

        opencode_sid = session.metadata.get("opencode_session_id")
        provider_id, model_id = await self.get_model_info(chat_id)
        active_mode = session.metadata.get("active_mode", "execute")

        # 3. Call OpenCode server with optional streaming callback
        response = await self.client.send_query(
            query=query_with_context,
            session_id=opencode_sid,
            provider_id=provider_id,
            model_id=model_id,
            active_mode=active_mode,
            active_agent=active_agent,
            chat_id=chat_id,
            session_uuid=session.session_id,
            files=files,
            on_token=on_token,
            on_activity=on_activity,
            on_permission=on_permission,
            on_question=on_question,
        )

        # 4. Save backend session ID if it was created/changed
        if self.client.last_session_id:
            session.metadata["opencode_session_id"] = self.client.last_session_id
            self.storage.save_session(session)

        # 5. Save assistant response to database
        self.storage.add_message(chat_id, "assistant", response, interface=interface)

        return response
