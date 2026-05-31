const Database = require('better-sqlite3');
const path = require('path');

const DB_PATH = path.join(__dirname, '..', 'data', 'bot.db');
const db = new Database(DB_PATH, { fileMustExist: false });
db.pragma('journal_mode = WAL');
db.pragma('foreign_keys = ON');

const listSessions = db.prepare(`
  SELECT s.id, s.title, s.summary, s.updated_at,
    (SELECT COUNT(*) FROM messages WHERE session_id = s.id) as msg_count,
    (SELECT session_id FROM active_sessions WHERE chat_id = ?) as active_id
  FROM sessions s WHERE s.chat_id = ? ORDER BY s.updated_at DESC
`);

const getSessionById = db.prepare('SELECT * FROM sessions WHERE id = ?');
const getActiveSession = db.prepare('SELECT session_id FROM active_sessions WHERE chat_id = ?');

const getMessages = db.prepare(
  'SELECT sender, content, timestamp, message_id, interface FROM messages WHERE session_id = ? ORDER BY timestamp ASC LIMIT ?'
);

const createSession = db.prepare(`
  INSERT INTO sessions (id, chat_id, user_id, username, title, created_at, updated_at, metadata)
  VALUES (?, ?, ?, ?, '', ?, ?, '{}')
`);

const setActiveSession = db.prepare(
  'INSERT OR REPLACE INTO active_sessions (chat_id, session_id) VALUES (?, ?)'
);

const switchSession = db.prepare(
  'INSERT OR REPLACE INTO active_sessions (chat_id, session_id) VALUES (?, ?)'
);

const addMessageStmt = db.prepare(
  'INSERT INTO messages (session_id, sender, content, timestamp, interface) VALUES (?, ?, ?, ?, ?)'
);

const updateSessionTime = db.prepare('UPDATE sessions SET updated_at = ? WHERE id = ?');

const getOpenCodeSessionId = db.prepare('SELECT opencode_session_id FROM sessions WHERE id = ?');
const setOpenCodeSessionId = db.prepare('UPDATE sessions SET opencode_session_id = ? WHERE id = ?');

const updateSummary = db.prepare('UPDATE sessions SET summary = ?, updated_at = ? WHERE id = ?');
const deleteMessages = db.prepare('DELETE FROM messages WHERE session_id = ?');
const updateTitle = db.prepare('UPDATE sessions SET title = ?, updated_at = ? WHERE id = ?');

module.exports = {
  db, listSessions, getSessionById, getActiveSession, getMessages,
  createSession, setActiveSession, switchSession,
  addMessageStmt, updateSessionTime, getOpenCodeSessionId, setOpenCodeSessionId,
  updateSummary, deleteMessages, updateTitle,
};
