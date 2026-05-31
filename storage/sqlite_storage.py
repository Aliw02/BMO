"""
SQLite storage backend for sessions, messages, and memory.
Supports per-session isolation, summaries, and full message history.
"""

import json
import sqlite3
import threading
from uuid import uuid4
from datetime import datetime
from typing import Optional, List, Dict

from models.chat_models import ChatSession, UserMemory
from config.settings import DATABASE_FILE, MAX_HISTORY_PER_CHAT, SESSIONS_FILE


_local = threading.local()


def _get_conn(db_path: str) -> sqlite3.Connection:
    if not hasattr(_local, "conn") or _local.conn is None:
        _local.conn = sqlite3.connect(db_path, timeout=30.0)
        _local.conn.row_factory = sqlite3.Row
        _local.conn.execute("PRAGMA journal_mode=WAL")
        _local.conn.execute("PRAGMA foreign_keys=ON")
    return _local.conn


class SQLiteStorage:
    def __init__(self, db_path: str = None):
        self.db_path = db_path or str(DATABASE_FILE)
        self._init_db()
        self._migrate_schema()
        self._migrate_from_jsonl()

    def _conn(self) -> sqlite3.Connection:
        return _get_conn(self.db_path)

    def _init_db(self):
        conn = self._conn()
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS sessions (
                id TEXT PRIMARY KEY,
                chat_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                username TEXT,
                title TEXT DEFAULT '',
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL,
                summary TEXT DEFAULT '',
                opencode_session_id TEXT,
                metadata TEXT DEFAULT '{}'
            );

            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL,
                sender TEXT NOT NULL,
                content TEXT NOT NULL,
                timestamp REAL NOT NULL,
                message_id INTEGER,
                interface TEXT NOT NULL DEFAULT 'telegram',
                FOREIGN KEY (session_id) REFERENCES sessions(id)
            );

            CREATE INDEX IF NOT EXISTS idx_messages_session
                ON messages(session_id, timestamp);

            CREATE INDEX IF NOT EXISTS idx_sessions_chat
                ON sessions(chat_id, updated_at);

            CREATE TABLE IF NOT EXISTS active_sessions (
                chat_id INTEGER PRIMARY KEY,
                session_id TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS user_memory (
                chat_id INTEGER PRIMARY KEY,
                user_id INTEGER NOT NULL,
                preferences TEXT DEFAULT '{}',
                knowledge TEXT DEFAULT '{}',
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL
            );

            CREATE TABLE IF NOT EXISTS user_agents (
                agent_id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                name TEXT NOT NULL,
                description TEXT,
                system_prompt TEXT NOT NULL,
                created_at REAL NOT NULL
            );

            CREATE TABLE IF NOT EXISTS provider_keys (
                provider_id TEXT NOT NULL,
                provider_name TEXT,
                key_name TEXT NOT NULL,
                encrypted_value TEXT NOT NULL,
                expires_at REAL,
                created_at REAL NOT NULL,
                PRIMARY KEY (provider_id, key_name)
            );

            CREATE TABLE IF NOT EXISTS permissions (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id    INTEGER NOT NULL,
                scope      TEXT NOT NULL,
                granted    INTEGER DEFAULT 1,
                created_at REAL NOT NULL,
                UNIQUE(user_id, scope)
            );
        """)
        conn.commit()

    def _migrate_schema(self):
        try:
            self._exec("ALTER TABLE messages ADD COLUMN interface TEXT NOT NULL DEFAULT 'telegram'")
            self._conn().commit()
        except Exception:
            pass

    def _exec(self, sql: str, params: tuple = ()) -> sqlite3.Cursor:
        return self._conn().execute(sql, params)

    def _fetchone(self, sql: str, params: tuple = ()) -> Optional[sqlite3.Row]:
        return self._conn().execute(sql, params).fetchone()

    def _fetchall(self, sql: str, params: tuple = ()) -> List[sqlite3.Row]:
        return self._conn().execute(sql, params).fetchall()

    # ── Migration from JSONL ──────────────────────────────────────────────

    def _migrate_from_jsonl(self):
        old_path = SESSIONS_FILE
        if not old_path.exists():
            return

        existing = self._fetchone("SELECT COUNT(*) as c FROM sessions")
        if existing and existing["c"] > 0:
            # Already migrated — but fix any broken active_sessions pointers.
            self._fix_active_sessions()
            return

        try:
            with open(old_path, "r", encoding="utf-8") as f:
                lines = [l.strip() for l in f if l.strip()]
        except Exception:
            return

        if not lines:
            return

        conn = self._conn()
        # Track best session per chat (most messages = most relevant)
        best_per_chat: dict = {}  # chat_id -> (sid, msg_count)

        for line in lines:
            try:
                data = json.loads(line)
                sid = data.get("session_id") or str(uuid4())
                chat_id = data.get("chat_id", 0)
                user_id = data.get("user_id", 0)
                metadata = data.get("metadata", {})
                messages = data.get("messages", [])

                conn.execute(
                    """INSERT OR IGNORE INTO sessions
                       (id, chat_id, user_id, username, title, created_at, updated_at, opencode_session_id, metadata)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        sid, chat_id, user_id, data.get("username"),
                        data.get("title", ""),
                        data.get("created_at", 0), data.get("updated_at", 0),
                        metadata.get("opencode_session_id"),
                        json.dumps(metadata, ensure_ascii=False),
                    ),
                )

                for msg in messages:
                    conn.execute(
                        """INSERT INTO messages
                           (session_id, sender, content, timestamp, message_id)
                           VALUES (?, ?, ?, ?, ?)""",
                        (
                            sid, msg.get("sender", "user"),
                            msg.get("content", ""),
                            msg.get("timestamp", 0),
                            msg.get("message_id"),
                        ),
                    )

                # Track session with most messages as "best" active candidate
                prev = best_per_chat.get(chat_id)
                if prev is None or len(messages) > prev[1]:
                    best_per_chat[chat_id] = (sid, len(messages))

            except Exception as e:
                print(f"Migration skip: {e}")

        # Set active session to the best (most messages) session per chat
        for chat_id, (sid, _) in best_per_chat.items():
            conn.execute(
                "INSERT OR REPLACE INTO active_sessions (chat_id, session_id) VALUES (?, ?)",
                (chat_id, sid),
            )

        conn.commit()
        print(f"Migrated {len(lines)} sessions from JSONL to SQLite")

    def _fix_active_sessions(self):
        """Ensure active_sessions points to the session with the most messages per chat.
        This self-heals any active pointer that points to an empty ghost session."""
        try:
            chat_rows = self._fetchall("SELECT DISTINCT chat_id FROM sessions")
            for chat_row in chat_rows:
                chat_id = chat_row["chat_id"]
                active = self._fetchone(
                    "SELECT session_id FROM active_sessions WHERE chat_id = ?",
                    (chat_id,),
                )
                if not active:
                    continue
                active_sid = active["session_id"]
                active_msg_count = self._fetchone(
                    "SELECT COUNT(*) as c FROM messages WHERE session_id = ?",
                    (active_sid,),
                )
                # Only fix if active session is empty (ghost session)
                if active_msg_count and active_msg_count["c"] == 0:
                    best = self._fetchone(
                        """SELECT s.id FROM sessions s
                           LEFT JOIN messages m ON m.session_id = s.id
                           WHERE s.chat_id = ?
                           GROUP BY s.id
                           ORDER BY COUNT(m.id) DESC, s.updated_at DESC
                           LIMIT 1""",
                        (chat_id,),
                    )
                    if best and best["id"] != active_sid:
                        self._exec(
                            "UPDATE active_sessions SET session_id = ? WHERE chat_id = ?",
                            (best["id"], chat_id),
                        )
                        print(f"Auto-fixed active session for chat {chat_id} -> {best['id']}")
            self._conn().commit()
        except Exception as e:
            print(f"_fix_active_sessions error: {e}")

    # ── Session CRUD ──────────────────────────────────────────────────────

    def save_session(self, session: ChatSession) -> bool:
        """
        Upserts session metadata ONLY (title, summary, opencode_session_id, metadata).
        Does NOT touch the messages table — use add_message() for that.
        This prevents stale in-memory session objects from overwriting DB messages.
        """
        try:
            if not session.session_id:
                session.session_id = str(uuid4())

            metadata = session.metadata.copy()
            # Sync the column with the metadata entry to avoid discrepancies
            oc_sid = metadata.get("opencode_session_id")
            
            metadata_json = json.dumps(metadata, ensure_ascii=False)
            summary = metadata.get("session_summary", "")

            self._exec(
                """INSERT INTO sessions
                   (id, chat_id, user_id, username, title, created_at, updated_at, summary, opencode_session_id, metadata)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(id) DO UPDATE SET
                       title=excluded.title,
                       updated_at=excluded.updated_at,
                       summary=excluded.summary,
                       opencode_session_id=excluded.opencode_session_id,
                       metadata=excluded.metadata""",
                (
                    session.session_id, session.chat_id, session.user_id,
                    session.username, session.title,
                    session.created_at, session.updated_at,
                    summary, oc_sid,
                    metadata_json,
                ),
            )

            # Only set active session pointer if none exists yet for this chat.
            # This prevents save_session from overwriting a manual session switch.
            existing_active = self._fetchone(
                "SELECT session_id FROM active_sessions WHERE chat_id = ?",
                (session.chat_id,),
            )
            if not existing_active:
                self._exec(
                    "INSERT OR REPLACE INTO active_sessions (chat_id, session_id) VALUES (?, ?)",
                    (session.chat_id, session.session_id),
                )

            self._conn().commit()
            return True
        except Exception as e:
            print(f"Error saving session: {e}")
            return False

    def load_session(self, chat_id: int) -> Optional[ChatSession]:
        try:
            active = self._fetchone(
                "SELECT session_id FROM active_sessions WHERE chat_id = ?",
                (chat_id,),
            )
            session_id = active["session_id"] if active else None

            if session_id:
                row = self._fetchone(
                    "SELECT * FROM sessions WHERE id = ?", (session_id,)
                )
                if row:
                    return self._row_to_session(row)

            candidates = self._fetchall(
                "SELECT * FROM sessions WHERE chat_id = ? ORDER BY updated_at DESC LIMIT 1",
                (chat_id,),
            )
            if candidates:
                row = candidates[0]
                self._exec(
                    "INSERT OR REPLACE INTO active_sessions (chat_id, session_id) VALUES (?, ?)",
                    (chat_id, dict(row)["id"]),
                )
                self._conn().commit()
                return self._row_to_session(row)
            return None
        except Exception as e:
            print(f"Error loading session: {e}")
            return None

            
    def set_active_session(self, chat_id: int, session_id: str) -> bool:
        """Explicitly switch the active session pointer for a chat."""
        try:
            self._exec(
                "INSERT OR REPLACE INTO active_sessions (chat_id, session_id) VALUES (?, ?)",
                (chat_id, session_id),
            )
            self._conn().commit()
            return True
        except Exception as e:
            print(f"Error setting active session: {e}")
            return False

    def delete_session(self, chat_id: int) -> bool:
        try:
            active = self._fetchone(
                "SELECT session_id FROM active_sessions WHERE chat_id = ?",
                (chat_id,),
            )
            if active:
                self._exec("DELETE FROM messages WHERE session_id = ?", (active["session_id"],))
                self._exec("DELETE FROM sessions WHERE id = ?", (active["session_id"],))
            self._exec("DELETE FROM active_sessions WHERE chat_id = ?", (chat_id,))
            self._conn().commit()
            return True
        except Exception as e:
            print(f"Error deleting session: {e}")
            return False

    def add_message(self, chat_id: int, sender: str, content: str, interface: str = 'telegram') -> bool:
        """Insert a single message directly into DB without rewriting the whole session."""
        try:
            active = self._fetchone(
                "SELECT session_id FROM active_sessions WHERE chat_id = ?",
                (chat_id,),
            )
            if not active:
                return False
            session_id = active["session_id"]

            self._exec(
                "INSERT INTO messages (session_id, sender, content, timestamp, interface) VALUES (?, ?, ?, ?, ?)",
                (session_id, sender, content, datetime.now().timestamp(), interface),
            )
            self._exec(
                "UPDATE sessions SET updated_at = ? WHERE id = ?",
                (datetime.now().timestamp(), session_id),
            )
            self._conn().commit()
            return True
        except Exception as e:
            print(f"Error adding message: {e}")
            return False

    def get_session_by_id(self, session_id: str) -> Optional[ChatSession]:
        try:
            row = self._fetchone(
                "SELECT * FROM sessions WHERE id = ?", (session_id,)
            )
            return self._row_to_session(row) if row else None
        except Exception as e:
            print(f"Error loading session by ID: {e}")
            return None

    def get_messages(self, session_id: str, limit: int = 50) -> List[dict]:
        rows = self._fetchall(
            "SELECT sender, content, timestamp, message_id FROM messages WHERE session_id = ? ORDER BY timestamp DESC LIMIT ?",
            (session_id, limit),
        )
        return [dict(r) for r in reversed(rows)]

    def list_chat_sessions(self, chat_id: int) -> List[dict]:
        rows = self._fetchall(
            """SELECT id, title, summary,
                      (SELECT COUNT(*) FROM messages WHERE session_id = sessions.id) as msg_count,
                      updated_at,
                      (SELECT session_id FROM active_sessions WHERE chat_id = ?) as active_id
               FROM sessions WHERE chat_id = ? ORDER BY updated_at DESC""",
            (chat_id, chat_id),
        )
        return [
            {
                "session_id": r["id"],
                "title": r["title"] or r["id"][:8],
                "summary": r["summary"][:100] if r["summary"] else "",
                "msg_count": r["msg_count"],
                "updated_at": r["updated_at"],
                "is_active": r["id"] == r["active_id"],
            }
            for r in rows
        ]

    def switch_session(self, chat_id: int, session_id: str) -> bool:
        exists = self._fetchone(
            "SELECT id FROM sessions WHERE id = ?", (session_id,)
        )
        if not exists:
            return False
        self._exec(
            "INSERT OR REPLACE INTO active_sessions (chat_id, session_id) VALUES (?, ?)",
            (chat_id, session_id),
        )
        self._conn().commit()
        return True

    def update_session_summary(self, session_id: str, summary: str):
        self._exec(
            "UPDATE sessions SET summary = ?, updated_at = ? WHERE id = ?",
            (summary, datetime.now().timestamp(), session_id),
        )
        self._conn().commit()

    def update_session_title(self, session_id: str, title: str):
        self._exec(
            "UPDATE sessions SET title = ?, updated_at = ? WHERE id = ?",
            (title, datetime.now().timestamp(), session_id),
        )
        self._conn().commit()

    # ── Memory ────────────────────────────────────────────────────────────

    def save_memory(self, memory: UserMemory) -> bool:
        try:
            self._exec(
                """INSERT INTO user_memory (chat_id, user_id, preferences, knowledge, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?)
                   ON CONFLICT(chat_id) DO UPDATE SET
                       user_id=excluded.user_id,
                       preferences=excluded.preferences,
                       knowledge=excluded.knowledge,
                       updated_at=excluded.updated_at""",
                (
                    memory.chat_id, memory.user_id,
                    json.dumps(memory.preferences, ensure_ascii=False),
                    json.dumps(memory.knowledge, ensure_ascii=False),
                    memory.created_at, memory.updated_at,
                ),
            )
            self._conn().commit()
            return True
        except Exception as e:
            print(f"Error saving memory: {e}")
            return False

    def load_memory(self, chat_id: int) -> Optional[UserMemory]:
        try:
            row = self._fetchone(
                "SELECT * FROM user_memory WHERE chat_id = ?", (chat_id,)
            )
            if row:
                return UserMemory(
                    user_id=row["user_id"],
                    chat_id=row["chat_id"],
                    preferences=json.loads(row["preferences"]),
                    knowledge=json.loads(row["knowledge"]),
                    created_at=row["created_at"],
                    updated_at=row["updated_at"],
                )
            return None
        except Exception as e:
            print(f"Error loading memory: {e}")
            return None

    def list_sessions(self) -> List[int]:
        rows = self._fetchall("SELECT DISTINCT chat_id FROM sessions")
        return [r["chat_id"] for r in rows]

    # ── Helpers ───────────────────────────────────────────────────────────

    def _row_to_session(self, row: sqlite3.Row) -> ChatSession:
        row_dict = dict(row)  # Convert to plain dict so .get() works safely
        metadata = json.loads(row_dict["metadata"]) if row_dict.get("metadata") else {}
        if row_dict.get("summary"):
            metadata["session_summary"] = row_dict["summary"]
        # Always sync the column with metadata to ensure the column is the source of truth
        metadata["opencode_session_id"] = row_dict.get("opencode_session_id")

        msg_rows = self._fetchall(
            "SELECT sender, content, timestamp, message_id FROM messages WHERE session_id = ? ORDER BY timestamp ASC",
            (row_dict["id"],),
        )
        messages = [
            {
                "sender": m["sender"],
                "content": m["content"],
                "timestamp": m["timestamp"],
                "message_id": m["message_id"],
            }
            for m in msg_rows
        ]

        return ChatSession(
            chat_id=row_dict["chat_id"],
            user_id=row_dict["user_id"],
            username=row_dict["username"],
            created_at=row_dict["created_at"],
            updated_at=row_dict["updated_at"],
            messages=messages,
            metadata=metadata,
            title=row_dict["title"] or "",
            session_id=row_dict["id"],
        )

    def get_stats(self) -> dict:
        """Returns global statistics for the admin."""
        user_count = self._fetchone("SELECT COUNT(DISTINCT chat_id) FROM sessions")[0]
        session_count = self._fetchone("SELECT COUNT(*) FROM sessions")[0]
        message_count = self._fetchone("SELECT COUNT(*) FROM messages")[0]
        return {
            "users": user_count or 0,
            "sessions": session_count or 0,
            "messages": message_count or 0
        }

    # ── Provider Keys (Encrypted) ──────────────────────────────────────────

    def save_provider_key(self, provider_id: str, provider_name: str, key_name: str, value: str, ttl_hours: Optional[int] = None):
        """Encrypts and saves an API key for a provider."""
        from core.security import encrypt_value
        encrypted = encrypt_value(value)
        expires_at = None
        if ttl_hours:
            expires_at = datetime.now().timestamp() + (ttl_hours * 3600)
        
        self._exec(
            """INSERT OR REPLACE INTO provider_keys (provider_id, provider_name, key_name, encrypted_value, expires_at, created_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (provider_id, provider_name, key_name, encrypted, expires_at, datetime.now().timestamp())
        )
        self._conn().commit()

    def get_provider_keys(self, provider_id: str) -> Dict[str, str]:
        """Returns all valid (not expired) keys for a provider, decrypted."""
        from core.security import decrypt_value
        now = datetime.now().timestamp()
        rows = self._fetchall(
            "SELECT key_name, encrypted_value FROM provider_keys WHERE provider_id = ? AND (expires_at IS NULL OR expires_at > ?)",
            (provider_id, now)
        )
        return {r["key_name"]: decrypt_value(r["encrypted_value"]) for r in rows}

    def list_provider_keys(self) -> List[dict]:
        """Returns a list of all providers that have saved keys."""
        rows = self._fetchall(
            "SELECT DISTINCT provider_id, provider_name, expires_at FROM provider_keys"
        )
        return [dict(r) for r in rows]

    def delete_expired_keys(self):
        """Removes all keys that have passed their expiration time."""
        now = datetime.now().timestamp()
        self._exec("DELETE FROM provider_keys WHERE expires_at IS NOT NULL AND expires_at <= ?", (now,))
        self._conn().commit()

    # ── Permissions ─────────────────────────────────────────────────────────

    def check_permission(self, user_id: int, scope: str) -> Optional[int]:
        """Returns 1 if granted, 0 if denied, None if not set."""
        row = self._fetchone(
            "SELECT granted FROM permissions WHERE user_id = ? AND scope = ?",
            (user_id, scope)
        )
        return row["granted"] if row else None

    def save_permission(self, user_id: int, scope: str, granted: int):
        """Saves a permanent permission (always allow/deny)."""
        now = datetime.now().timestamp()
        self._exec(
            "INSERT OR REPLACE INTO permissions (user_id, scope, granted, created_at) VALUES (?, ?, ?, ?)",
            (user_id, scope, granted, now)
        )
        self._conn().commit()

    def list_permissions(self, user_id: int) -> List[dict]:
        """Lists all saved permissions for a user."""
        rows = self._fetchall(
            "SELECT scope, granted, created_at FROM permissions WHERE user_id = ?",
            (user_id,)
        )
        return [dict(r) for r in rows]

    def list_sessions(self, chat_id: int) -> List[dict]:
        """Lists all sessions for a chat, sorted by update time."""
        rows = self._fetchall(
            "SELECT * FROM sessions WHERE chat_id = ? ORDER BY updated_at DESC",
            (chat_id,)
        )
        return [dict(r) for r in rows]
    def save_custom_agent(self, chat_id: int, user_id: int, name: str, description: str, system_prompt: str):
        import time
        self._exec(
            "INSERT INTO user_agents (chat_id, user_id, name, description, system_prompt, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (chat_id, user_id, name, description, system_prompt, time.time())
        )
        self._conn().commit()

    def get_custom_agents(self, chat_id: int) -> list[dict]:
        rows = self._fetchall(
            "SELECT agent_id, name, description, system_prompt FROM user_agents WHERE chat_id = ? ORDER BY created_at DESC",
            (chat_id,)
        )
        return [dict(row) for row in rows]

    def delete_custom_agent(self, agent_id: int):
        self._exec("DELETE FROM user_agents WHERE agent_id = ?", (agent_id,))
        self._conn().commit()

    def get_custom_agent_by_id(self, agent_id: int) -> Optional[dict]:
        row = self._fetchone(
            "SELECT name, system_prompt FROM user_agents WHERE agent_id = ?",
            (agent_id,)
        )
        return dict(row) if row else None
