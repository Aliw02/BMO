"""
Storage layer for chat history, sessions, and memory.
Supports JSONL backend with multi-session per chat.
"""

import json
from uuid import uuid4
from datetime import datetime
from typing import Optional, List, Dict
from abc import ABC, abstractmethod

from models.chat_models import ChatSession, UserMemory
from config.settings import (
    STORAGE_TYPE, SESSIONS_FILE, ACTIVE_SESSIONS_FILE,
    USER_MEMORY_FILE, MAX_HISTORY_PER_CHAT, DATABASE_FILE,
)


class StorageBackend(ABC):
    @abstractmethod
    def save_session(self, session: ChatSession) -> bool:
        pass

    @abstractmethod
    def load_session(self, chat_id: int) -> Optional[ChatSession]:
        pass

    @abstractmethod
    def delete_session(self, chat_id: int) -> bool:
        pass

    @abstractmethod
    def add_message(self, chat_id: int, sender: str, content: str) -> bool:
        pass

    @abstractmethod
    def save_memory(self, memory: UserMemory) -> bool:
        pass

    @abstractmethod
    def load_memory(self, chat_id: int) -> Optional[UserMemory]:
        pass


class JSONLStorage(StorageBackend):
    def __init__(self):
        self.sessions_file = SESSIONS_FILE
        self.active_file = ACTIVE_SESSIONS_FILE
        self.memory_file = USER_MEMORY_FILE
        self.sessions_file.parent.mkdir(parents=True, exist_ok=True)
        self.memory_file.parent.mkdir(parents=True, exist_ok=True)
        self._migrate_from_json()

    # ── Migration ─────────────────────────────────────────────────────────────

    def _migrate_from_json(self):
        old_file = self.sessions_file.with_suffix(".json")
        if not self.sessions_file.exists() and old_file.exists():
            try:
                with open(old_file, "r") as f:
                    data = json.load(f)
                if data:
                    self._save_all_sessions(data)
                print(f"Migrated sessions from {old_file} to {self.sessions_file}")
            except Exception as e:
                print(f"Migration error: {e}")

    # ── Sessions file (JSONL) ──────────────────────────────────────────────
    # Internal format: {session_id: session_dict}

    def _load_all_sessions(self) -> Dict[str, dict]:
        if not self.sessions_file.exists():
            return {}
        data = {}
        try:
            with open(self.sessions_file, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        obj = json.loads(line)
                        sid = obj.get("session_id") or obj.get("chat_id")
                        if sid:
                            data[str(sid)] = obj
                    except json.JSONDecodeError:
                        continue
        except Exception:
            return {}
        return data

    def _save_all_sessions(self, data: Dict[str, dict]):
        with open(self.sessions_file, "w", encoding="utf-8") as f:
            for sid in sorted(data.keys()):
                f.write(json.dumps(data[sid], ensure_ascii=False) + "\n")

    # ── Active sessions map ─────────────────────────────────────────────────
    # Format: {chat_id: active_session_id}

    def _load_active_map(self) -> Dict[str, str]:
        if self.active_file.exists():
            try:
                with open(self.active_file, "r") as f:
                    return json.load(f)
            except Exception:
                return {}
        return {}

    def _save_active_map(self, data: Dict[str, str]):
        with open(self.active_file, "w") as f:
            json.dump(data, f, indent=2)

    # ── Session CRUD (multi-session aware) ──────────────────────────────────

    def save_session(self, session: ChatSession) -> bool:
        try:
            if not session.session_id:
                session.session_id = str(uuid4())
            db = self._load_all_sessions()
            db[session.session_id] = session.to_dict()
            self._save_all_sessions(db)
            active = self._load_active_map()
            active[str(session.chat_id)] = session.session_id
            self._save_active_map(active)
            return True
        except Exception as e:
            print(f"Error saving session: {e}")
            return False

    def load_session(self, chat_id: int) -> Optional[ChatSession]:
        try:
            active = self._load_active_map()
            session_id = active.get(str(chat_id))
            db = self._load_all_sessions()

            if session_id and session_id in db:
                return ChatSession.from_dict(db[session_id])

            # Auto-migrate: find any session for this chat, pick newest
            candidates = []
            for sid, data in db.items():
                if data.get("chat_id") == chat_id:
                    candidates.append((sid, data))
            if candidates:
                candidates.sort(key=lambda x: x[1].get("updated_at", 0), reverse=True)
                best_sid, best_data = candidates[0]
                if not best_data.get("session_id"):
                    best_data["session_id"] = best_sid
                    db[best_sid] = best_data
                    self._save_all_sessions(db)
                active[str(chat_id)] = best_sid
                self._save_active_map(active)
                return ChatSession.from_dict(best_data)

            return None
        except Exception as e:
            print(f"Error loading session: {e}")
            return None

    def delete_session(self, chat_id: int) -> bool:
        try:
            active = self._load_active_map()
            session_id = active.pop(str(chat_id), None)
            if session_id:
                db = self._load_all_sessions()
                db.pop(session_id, None)
                self._save_all_sessions(db)
            self._save_active_map(active)
            return True
        except Exception as e:
            print(f"Error deleting session: {e}")
            return False

    def add_message(self, chat_id: int, sender: str, content: str) -> bool:
        try:
            session = self.load_session(chat_id)
            if session:
                session.add_message(sender, content)
                if len(session.messages) > MAX_HISTORY_PER_CHAT:
                    session.messages = session.messages[-MAX_HISTORY_PER_CHAT:]
                return self.save_session(session)
            return False
        except Exception as e:
            print(f"Error adding message: {e}")
            return False

    def get_session_by_id(self, session_id: str) -> Optional[ChatSession]:
        try:
            db = self._load_all_sessions()
            data = db.get(session_id)
            return ChatSession.from_dict(data) if data else None
        except Exception as e:
            print(f"Error loading session by ID: {e}")
            return None

    def list_chat_sessions(self, chat_id: int) -> List[dict]:
        db = self._load_all_sessions()
        active = self._load_active_map()
        active_id = active.get(str(chat_id))
        result = []
        for sid, data in db.items():
            if data.get("chat_id") == chat_id:
                result.append({
                    "session_id": sid,
                    "title": data.get("title", "") or sid[:8],
                    "msg_count": len(data.get("messages", [])),
                    "updated_at": data.get("updated_at", 0),
                    "is_active": sid == active_id,
                })
        result.sort(key=lambda x: x["updated_at"], reverse=True)
        return result

    def switch_session(self, chat_id: int, session_id: str) -> bool:
        db = self._load_all_sessions()
        if session_id not in db:
            return False
        active = self._load_active_map()
        active[str(chat_id)] = session_id
        self._save_active_map(active)
        return True

    # ── Memory ────────────────────────────────────────────────────────────────

    def _load_memory_db(self) -> Dict:
        if self.memory_file.exists():
            try:
                with open(self.memory_file, "r") as f:
                    return json.load(f)
            except Exception:
                return {}
        return {}

    def _save_memory_db(self, data: Dict):
        with open(self.memory_file, "w") as f:
            json.dump(data, f, indent=2)

    def save_memory(self, memory: UserMemory) -> bool:
        try:
            db = self._load_memory_db()
            db[str(memory.chat_id)] = memory.to_dict()
            self._save_memory_db(db)
            return True
        except Exception as e:
            print(f"Error saving memory: {e}")
            return False

    def load_memory(self, chat_id: int) -> Optional[UserMemory]:
        try:
            db = self._load_memory_db()
            data = db.get(str(chat_id))
            if data:
                return UserMemory.from_dict(data)
            return None
        except Exception as e:
            print(f"Error loading memory: {e}")
            return None

    def list_sessions(self) -> List[int]:
        db = self._load_all_sessions()
        return list({d.get("chat_id") for d in db.values() if d.get("chat_id")})


def get_storage() -> StorageBackend:
    from storage.sqlite_storage import SQLiteStorage
    return SQLiteStorage(db_path=str(DATABASE_FILE))
