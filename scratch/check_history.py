
import sqlite3
import os

db_path = "data/bot.db"
if os.path.exists(db_path):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    
    # Get the last active session
    cursor.execute("SELECT session_id FROM active_sessions ORDER BY rowid DESC LIMIT 1")
    active = cursor.fetchone()
    if active:
        sid = active['session_id']
        cursor.execute("SELECT chat_id FROM sessions WHERE id = ?", (sid,))
        chat_row = cursor.fetchone()
        cid = chat_row['chat_id'] if chat_row else "unknown"
        print(f"Checking session: {sid} (Chat ID: {cid})")
        cursor.execute("SELECT sender, content, timestamp FROM messages WHERE session_id = ? ORDER BY timestamp DESC LIMIT 10", (sid,))
        messages = cursor.fetchall()
        for msg in reversed(messages):
            print(f"[{msg['sender']} at {msg['timestamp']}]: {msg['content'][:200]}...")
    else:
        print("No active session found.")
    conn.close()
else:
    print("Database not found.")
