"""
BMO Session Summary Tool
Built-in alternative to MCP get_session_summaries
Usage: python get_session_summaries.py <chat_id> [count]
"""
import sqlite3
import sys
import os

DB_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "bot.db")

def get_session_summaries(chat_id, count=5):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    cursor.execute("""
        SELECT id, created_at, summary 
        FROM sessions 
        WHERE chat_id = ? 
        ORDER BY created_at DESC 
        LIMIT ?
    """, (str(chat_id), count))
    
    results = cursor.fetchall()
    conn.close()
    
    if not results:
        return f"No sessions found for chat_id: {chat_id}"
    
    output = []
    for session_id, created_at, summary in results:
        import datetime
        dt = datetime.datetime.fromtimestamp(created_at)
        output.append(f"📅 {dt.strftime('%Y-%m-%d %H:%M')} | ID: {session_id[:8]}...")
        if summary:
            output.append(f"   {summary[:200]}...")
        output.append("")
    
    return "\n".join(output)

if __name__ == "__main__":
    chat_id = sys.argv[1] if len(sys.argv) > 1 else "732356803"
    count = int(sys.argv[2]) if len(sys.argv) > 2 else 5
    print(get_session_summaries(chat_id, count))