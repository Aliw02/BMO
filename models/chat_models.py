"""
Data models for chat messages and sessions
"""

from dataclasses import dataclass, asdict
from datetime import datetime
from typing import Optional, List
import json


@dataclass
class ChatMessage:
    sender: str
    content: str
    timestamp: float
    message_id: Optional[int] = None

    def to_dict(self):
        return asdict(self)

    @classmethod
    def from_dict(cls, data):
        return cls(**data)


@dataclass
class ChatSession:
    chat_id: int
    user_id: int
    username: Optional[str]
    created_at: float
    updated_at: float
    messages: List[dict]
    metadata: dict
    title: str = ""
    session_id: str = ""

    def to_dict(self):
        return {
            "chat_id": self.chat_id,
            "user_id": self.user_id,
            "username": self.username,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "messages": self.messages,
            "metadata": self.metadata,
            "title": self.title,
            "session_id": self.session_id,
        }

    @classmethod
    def from_dict(cls, data):
        for field in ("title", "session_id"):
            if field not in data:
                data[field] = ""
        return cls(**data)

    def add_message(self, sender: str, content: str, message_id: Optional[int] = None):
        message = ChatMessage(
            sender=sender,
            content=content,
            timestamp=datetime.now().timestamp(),
            message_id=message_id,
        )
        self.messages.append(message.to_dict())
        self.updated_at = datetime.now().timestamp()

    def get_last_n_messages(self, n: int) -> List[ChatMessage]:
        return [ChatMessage.from_dict(m) for m in self.messages[-n:]]

    def get_context_text(self, max_messages: int = 10) -> str:
        recent = self.get_last_n_messages(max_messages)
        context = []
        for msg in recent:
            prefix = "You:" if msg.sender == "user" else "Assistant:"
            context.append(f"{prefix} {msg.content}")
        return "\n".join(context)

    def set_title(self, title: str):
        self.title = title
        self.updated_at = datetime.now().timestamp()

    def get_title(self) -> str:
        return self.title

    def set_summary(self, summary: str):
        self.metadata["session_summary"] = summary
        self.updated_at = datetime.now().timestamp()

    def get_summary(self) -> Optional[str]:
        return self.metadata.get("session_summary")

    def set_description(self, description: str):
        self.metadata["session_description"] = description
        self.updated_at = datetime.now().timestamp()

    def get_description(self) -> Optional[str]:
        return self.metadata.get("session_description")

    def get_message_counter(self) -> int:
        return self.metadata.get("message_counter", 0)

    def increment_message_counter(self) -> int:
        count = self.metadata.get("message_counter", 0) + 1
        self.metadata["message_counter"] = count
        self.updated_at = datetime.now().timestamp()
        return count

    def reset_message_counter(self):
        self.metadata["message_counter"] = 0
        self.updated_at = datetime.now().timestamp()


@dataclass
class UserMemory:
    user_id: int
    chat_id: int
    preferences: dict
    knowledge: dict
    created_at: float
    updated_at: float

    def to_dict(self):
        return {
            "user_id": self.user_id,
            "chat_id": self.chat_id,
            "preferences": self.preferences,
            "knowledge": self.knowledge,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, data):
        return cls(**data)

    def update_preference(self, key: str, value):
        self.preferences[key] = value
        self.updated_at = datetime.now().timestamp()

    def add_knowledge(self, key: str, value):
        self.knowledge[key] = value
        self.updated_at = datetime.now().timestamp()