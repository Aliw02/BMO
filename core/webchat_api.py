"""
BMO Webchat API — single-source-of-truth HTTP backend for the Node.js webchat.

Eliminates better-sqlite3 native binding entirely. The webchat becomes a pure
HTTP client, while this server owns the SQLite database exclusively.

Run standalone:  python core/webchat_api.py [--port PORT]
Or from FastAPI: uvicorn core.webchat_api:app --port 4098
"""

import argparse
import sqlite3
import uuid
from datetime import datetime
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from config.settings import DATABASE_FILE

app = FastAPI(title="BMO Webchat API", version="2.1.16")


def get_db() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DATABASE_FILE), timeout=30.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


# ── Models ────────────────────────────────────────────────────────────────────


class NewSession(BaseModel):
    chat_id: int
    user_id: Optional[int] = None
    username: str = ""


class SwitchSession(BaseModel):
    chat_id: int
    session_id: str


class SetSession(BaseModel):
    chat_id: int
    session_id: str


class NewMessage(BaseModel):
    sender: str
    content: str
    interface: str = "webchat"


class SetTitle(BaseModel):
    title: str


class SetSummary(BaseModel):
    summary: str


class SetOpenCodeSession(BaseModel):
    opencode_session_id: str


class NewAgent(BaseModel):
    chat_id: int
    name: str
    description: str = ""
    system_prompt: str = ""


class UpdateAgent(BaseModel):
    name: str
    description: str = ""
    system_prompt: str = ""


# ── Helpers ────────────────────────────────────────────────────────────────────


def row_to_dict(row: sqlite3.Row) -> dict:
    return dict(row)


# ── Health ─────────────────────────────────────────────────────────────────────


@app.get("/api/health")
def health():
    return {"status": "ok"}


# ── Chat resolution ────────────────────────────────────────────────────────────


@app.get("/api/chat/resolve")
def resolve_chat_id():
    conn = get_db()
    row = conn.execute(
        "SELECT DISTINCT chat_id FROM sessions ORDER BY updated_at DESC LIMIT 1"
    ).fetchone()
    conn.close()
    if row:
        return {"chat_id": row["chat_id"]}
    return {"chat_id": None}


# ── Sessions ───────────────────────────────────────────────────────────────────


@app.get("/api/sessions")
def list_sessions(chat_id: int):
    conn = get_db()
    rows = conn.execute(
        """SELECT s.id, s.title, s.summary, s.updated_at,
                  (SELECT COUNT(*) FROM messages WHERE session_id = s.id) as msg_count,
                  (SELECT session_id FROM active_sessions WHERE chat_id = ?) as active_id
           FROM sessions s WHERE s.chat_id = ? ORDER BY s.updated_at DESC""",
        (chat_id, chat_id),
    ).fetchall()
    conn.close()
    active_row = None
    if rows:
        active_row = rows[0]
    return [
        {
            "session_id": r["id"],
            "title": r["title"] or r["id"][:8],
            "summary": (r["summary"] or "")[:100],
            "msg_count": r["msg_count"],
            "updated_at": r["updated_at"],
            "is_active": r["id"] == (active_row["active_id"] if active_row else None),
        }
        for r in rows
    ]


@app.get("/api/sessions/{session_id}")
def get_session(session_id: str):
    conn = get_db()
    row = conn.execute("SELECT * FROM sessions WHERE id = ?", (session_id,)).fetchone()
    conn.close()
    if not row:
        raise HTTPException(status_code=404, detail="Session not found")
    return row_to_dict(row)


@app.post("/api/sessions")
def create_session(data: NewSession):
    sid = str(uuid.uuid4())
    now = datetime.utcnow().timestamp()
    conn = get_db()
    conn.execute(
        """INSERT INTO sessions (id, chat_id, user_id, username, title, created_at, updated_at, metadata)
           VALUES (?, ?, ?, ?, '', ?, ?, '{}')""",
        (sid, data.chat_id, data.user_id or data.chat_id, data.username, now, now),
    )
    conn.execute(
        "INSERT OR REPLACE INTO active_sessions (chat_id, session_id) VALUES (?, ?)",
        (data.chat_id, sid),
    )
    conn.commit()
    conn.close()
    return {"session_id": sid}


@app.post("/api/sessions/{session_id}/activate")
def activate_session(session_id: str, data: SwitchSession):
    conn = get_db()
    row = conn.execute("SELECT id FROM sessions WHERE id = ?", (session_id,)).fetchone()
    if not row:
        conn.close()
        raise HTTPException(status_code=404, detail="Session not found")
    conn.execute(
        "INSERT OR REPLACE INTO active_sessions (chat_id, session_id) VALUES (?, ?)",
        (data.chat_id, session_id),
    )
    conn.commit()
    conn.close()
    return {"ok": True}


@app.post("/api/sessions/switch")
def switch_session(data: SwitchSession):
    conn = get_db()
    conn.execute(
        "INSERT OR REPLACE INTO active_sessions (chat_id, session_id) VALUES (?, ?)",
        (data.chat_id, data.session_id),
    )
    conn.commit()
    conn.close()
    return {"ok": True}


@app.post("/api/sessions/set")
def set_session(data: SetSession):
    conn = get_db()
    row = conn.execute(
        "SELECT * FROM sessions WHERE id = ?", (data.session_id,)
    ).fetchone()
    if not row:
        conn.close()
        raise HTTPException(status_code=404, detail="Session not found")
    conn.execute(
        "INSERT OR REPLACE INTO active_sessions (chat_id, session_id) VALUES (?, ?)",
        (data.chat_id, data.session_id),
    )
    oc_row = conn.execute(
        "SELECT opencode_session_id FROM sessions WHERE id = ?", (data.session_id,)
    ).fetchone()
    opencode_session_id = oc_row["opencode_session_id"] if oc_row else None
    if not opencode_session_id:
        tel_row = conn.execute(
            """SELECT opencode_session_id FROM sessions
               WHERE chat_id = ? AND opencode_session_id IS NOT NULL
               ORDER BY updated_at DESC LIMIT 1""",
            (data.chat_id,),
        ).fetchone()
        if tel_row and tel_row["opencode_session_id"]:
            conn.execute(
                "UPDATE sessions SET opencode_session_id = ? WHERE id = ?",
                (tel_row["opencode_session_id"], data.session_id),
            )
            opencode_session_id = tel_row["opencode_session_id"]
    conn.commit()
    conn.close()
    return {
        "opencode_session_id": opencode_session_id,
        "session": {
            "id": row["id"],
            "title": row["title"],
            "chat_id": row["chat_id"],
        },
    }


@app.get("/api/sessions/{session_id}/messages")
def get_messages(session_id: str, limit: int = 50):
    conn = get_db()
    rows = conn.execute(
        """SELECT sender, content, timestamp, message_id, interface
           FROM messages WHERE session_id = ? ORDER BY timestamp ASC LIMIT ?""",
        (session_id, limit),
    ).fetchall()
    conn.close()
    return [row_to_dict(r) for r in rows]


@app.post("/api/sessions/{session_id}/messages")
def add_message(session_id: str, data: NewMessage):
    conn = get_db()
    now = datetime.utcnow().timestamp()
    conn.execute(
        """INSERT INTO messages (session_id, sender, content, timestamp, interface)
           VALUES (?, ?, ?, ?, ?)""",
        (session_id, data.sender, data.content, now, data.interface),
    )
    conn.execute("UPDATE sessions SET updated_at = ? WHERE id = ?", (now, session_id))
    conn.commit()
    conn.close()
    return {"ok": True}


@app.post("/api/sessions/{session_id}/title")
def set_title(session_id: str, data: SetTitle):
    conn = get_db()
    now = datetime.utcnow().timestamp()
    conn.execute(
        "UPDATE sessions SET title = ?, updated_at = ? WHERE id = ?",
        (data.title, now, session_id),
    )
    conn.commit()
    conn.close()
    return {"ok": True}


@app.post("/api/sessions/{session_id}/summary")
def set_summary(session_id: str, data: SetSummary):
    conn = get_db()
    now = datetime.utcnow().timestamp()
    conn.execute(
        "UPDATE sessions SET summary = ?, updated_at = ? WHERE id = ?",
        (data.summary, now, session_id),
    )
    conn.commit()
    conn.close()
    return {"ok": True}


@app.post("/api/sessions/{session_id}/clear")
def clear_messages(session_id: str):
    conn = get_db()
    conn.execute("DELETE FROM messages WHERE session_id = ?", (session_id,))
    conn.commit()
    conn.close()
    return {"ok": True}


@app.post("/api/sessions/{session_id}/opencode-session")
def set_opencode_session(session_id: str, data: SetOpenCodeSession):
    conn = get_db()
    conn.execute(
        "UPDATE sessions SET opencode_session_id = ?, updated_at = ? WHERE id = ?",
        (data.opencode_session_id, datetime.utcnow().timestamp(), session_id),
    )
    conn.commit()
    conn.close()
    return {"ok": True}


@app.get("/api/sessions/{session_id}/opencode-session")
def get_opencode_session(session_id: str):
    conn = get_db()
    row = conn.execute(
        "SELECT opencode_session_id FROM sessions WHERE id = ?", (session_id,)
    ).fetchone()
    conn.close()
    return {"opencode_session_id": row["opencode_session_id"] if row else None}


@app.get("/api/sessions/{session_id}/msg-count")
def get_message_count(session_id: str):
    conn = get_db()
    row = conn.execute(
        "SELECT COUNT(*) as cnt FROM messages WHERE session_id = ?", (session_id,)
    ).fetchone()
    conn.close()
    return {"cnt": row["cnt"] if row else 0}


@app.post("/api/sessions/{session_id}/switch-model")
def switch_model(session_id: str, data: SetOpenCodeSession):
    conn = get_db()
    now = datetime.utcnow().timestamp()
    conn.execute(
        "UPDATE sessions SET opencode_session_id = ?, updated_at = ? WHERE id = ?",
        (data.opencode_session_id, now, session_id),
    )
    conn.commit()
    conn.close()
    return {"ok": True}


# ── Agents ─────────────────────────────────────────────────────────────────────


@app.get("/api/agents")
def list_agents(chat_id: int):
    conn = get_db()
    rows = conn.execute(
        "SELECT agent_id, name, description, system_prompt FROM user_agents WHERE chat_id = ? ORDER BY created_at DESC",
        (chat_id,),
    ).fetchall()
    conn.close()
    return [
        {
            "agent_id": r["agent_id"],
            "name": r["name"],
            "description": r["description"] or "",
            "system_prompt": r["system_prompt"] or "",
        }
        for r in rows
    ]


@app.post("/api/agents")
def create_agent(data: NewAgent):
    now = datetime.utcnow().timestamp()
    conn = get_db()
    conn.execute(
        """INSERT INTO user_agents (chat_id, user_id, name, description, system_prompt, created_at)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (data.chat_id, data.chat_id, data.name, data.description, data.system_prompt, now),
    )
    conn.commit()
    conn.close()
    return {"ok": True}


@app.put("/api/agents/{agent_id}")
def update_agent(agent_id: int, data: UpdateAgent):
    conn = get_db()
    conn.execute(
        "UPDATE user_agents SET name = ?, description = ?, system_prompt = ? WHERE agent_id = ?",
        (data.name, data.description, data.system_prompt, agent_id),
    )
    conn.commit()
    conn.close()
    return {"ok": True}


@app.delete("/api/agents/{agent_id}")
def delete_agent(agent_id: int):
    conn = get_db()
    conn.execute("DELETE FROM user_agents WHERE agent_id = ?", (agent_id,))
    conn.commit()
    conn.close()
    return {"ok": True}


# ── Entry point ────────────────────────────────────────────────────────────────


def main():
    parser = argparse.ArgumentParser(description="BMO Webchat API server")
    parser.add_argument("--port", type=int, default=4098, help="Port to listen on")
    parser.add_argument("--host", type=str, default="127.0.0.1", help="Host to bind")
    args = parser.parse_args()

    import uvicorn
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()
