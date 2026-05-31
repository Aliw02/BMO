"""


















Telegram message handlers: auth, OpenCode integration, Document handling.
Uses persistent ReplyKeyboard instead of slash commands.
"""

import asyncio
import logging
import httpx
import os
import time
from datetime import datetime
from pathlib import Path

from telegram import Update, Message, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, KeyboardButton
from telegram.constants import ParseMode
from telegram.ext import ContextTypes, CallbackQueryHandler

from config.settings import CHUNK_SIZE, ALLOWED_USER_IDS, OPENCODE_BASE_URL, DATA_DIR
from core.bot_client import OpenCodeBotClient
from models.chat_models import ChatSession
from storage.storage import get_storage
from core.shared_state import pending_permissions


logger = logging.getLogger(__name__)

opencode_client = OpenCodeBotClient()
storage = get_storage()

# Ensure downloads directory exists
DOWNLOADS_DIR = DATA_DIR / "downloads"
DOWNLOADS_DIR.mkdir(exist_ok=True)

# ── Persistent Global State ──────────────────
_chat_model: dict[int, tuple[str, str]] = {}

def _get_current_model(chat_id: int, session: ChatSession) -> tuple[str, str]:
    # 1. Try environment variables (Global Sync)
    env_p = os.getenv("OPENCODE_PROVIDER")
    env_m = os.getenv("OPENCODE_MODEL")
    if env_p and env_m:
        return env_p, env_m

    # 2. Try session metadata
    if session:
        p_id = session.metadata.get("provider_id")
        m_id = session.metadata.get("model_id")
        if p_id and m_id and p_id != "None" and m_id != "None":
            _chat_model[chat_id] = (p_id, m_id) # Update cache
            return p_id, m_id
            
    # 3. Default fallback
    return ("opencode", "big-pickle")

# ── Persistent Main Menu Keyboard (Clean Redesign) ───────────────────────────
# Structure: Primary Actions | Session | History | System

# Row 1: Primary Actions (Softer, fewer buttons)
BTN_NEW_SESSION  = "🆕 New"
BTN_SAVE        = "💾 Save"
BTN_SUMMARY     = "📝 Summary"

# Row 2: Session Management
BTN_SESSIONS    = "📋 All Sessions"
BTN_SET_TITLE   = "🏷️ Set Title"
BTN_RESET_HISTORY = "🗑️ Reset History"

# Row 3: History & Tools
BTN_HISTORY     = "📜 History"
BTN_LOAD        = "📂 Load"
BTN_CHOOSE_MODEL = "🤖 Model"

# Row 4: Tools & System
BTN_AGENTS       = "🎭 Agents"
BTN_SKILLS      = "🛠️ Skills"
BTN_RELOAD       = "🔄 Reload"

# Row 5: System (minimal)
BTN_STATUS       = "ℹ️ Status"
BTN_MODE         = "⚡ Mode"
BTN_MENU         = "📋 Menu"
BTN_SETTINGS     = "⚙️ Settings"

# Legacy Aliases (to prevent NameErrors)
BTN_VIEW_HIST   = "📜 History"
BTN_SAVE_SESSION = "💾 Save"
BTN_LOAD_SESSION = "📂 Load"
BTN_USE_SKILL   = "🛠️ Skills"
BTN_CLEAR_HIST  = "🗑️ Reset History"

BTNS_ROW1 = [BTN_NEW_SESSION, BTN_SAVE, BTN_SUMMARY]
BTNS_ROW2 = [BTN_SESSIONS, BTN_SET_TITLE, BTN_RESET_HISTORY]
BTNS_ROW3 = [BTN_HISTORY, BTN_LOAD, BTN_CHOOSE_MODEL]
BTNS_ROW4 = [BTN_AGENTS, BTN_SKILLS, BTN_RELOAD]
BTNS_ROW5 = [BTN_STATUS, BTN_MODE, BTN_MENU]
BTNS_ROW6 = [BTN_SETTINGS]

MAIN_MENU_KEYBOARD = ReplyKeyboardMarkup(
    [BTNS_ROW1, BTNS_ROW2, BTNS_ROW3, BTNS_ROW4, BTNS_ROW5, BTNS_ROW6],
    resize_keyboard=True,
    is_persistent=True
)

# ── Inline Menu Keyboard (Modern UI with Session Title) ──────────────────────────────────────────
def build_inline_menu(session: ChatSession = None) -> InlineKeyboardMarkup:
    """Build inline keyboard menu with session-aware buttons."""
    kb = []
    
    # Row 1: Session Title Display (prominent)
    session_title = "Untitled Session"
    if session and session.title:
        session_title = session.title[:25] + "..." if len(session.title) > 25 else session.title
    elif session and session.session_id:
        session_title = f"Session #{session.session_id[:6]}"
    
    kb.append([
        InlineKeyboardButton(f"📌 {session_title}", callback_data="action:show_session_info")
    ])
    
    # Row 2: Session Actions
    kb.append([
        InlineKeyboardButton("🆕 New", callback_data="action:new_session"),
        InlineKeyboardButton("💾 Save", callback_data="action:save_session"),
        InlineKeyboardButton("📂 Load", callback_data="action:list_sessions"),
    ])
    
    # Row 3: Title & History
    kb.append([
        InlineKeyboardButton("🏷️ Set Title", callback_data="action:set_title"),
        InlineKeyboardButton("📜 History", callback_data="action:view_history"),
        InlineKeyboardButton("🗑️ Clear", callback_data="action:clear_history"),
    ])
    
    # Row 4: Models & Mode
    kb.append([
        InlineKeyboardButton("🤖 Models", callback_data="action:choose_model"),
        InlineKeyboardButton("⚡ Mode", callback_data="action:choose_mode"),
    ])
    
    # Row 5: Tools
    kb.append([
        InlineKeyboardButton("🎭 Agents", callback_data="action:agents"),
        InlineKeyboardButton("🛠️ Skills", callback_data="action:use_skill"),
    ])
    
    # Row 6: Web chat (dynamic — shows launch or stop based on port status)
    from tools.task_registry import check_port_conflict
    conflict = check_port_conflict(3456)
    webchat_btn = (
        InlineKeyboardButton("⏹ Stop Webchat", callback_data="action:stop_webchat")
        if conflict
        else InlineKeyboardButton("🌐 Chat on Web", callback_data="action:launch_webchat")
    )
    kb.append([webchat_btn])
    
    # Row 7: System
    kb.append([
        InlineKeyboardButton("ℹ️ Status", callback_data="action:status"),
        InlineKeyboardButton("⚙️ Settings", callback_data="action:settings"),
    ])
    
    return InlineKeyboardMarkup(kb)


def build_recent_sessions_message(sessions: list, max_show: int = 3) -> str:
    """Build a preview of recent sessions for display."""
    if not sessions:
        return ""
    
    preview = "📜 <b>Recent Sessions:</b>\n"
    for i, s in enumerate(sessions[:max_show]):
        title = (s.get("title") or f"Session #{s.get('session_id', 'unknown')[:6]}")
        title = title[:35]
        preview += f"  {i+1}. {title}\n"
    
    if len(sessions) > max_show:
        preview += f"  ... +{len(sessions) - max_show} more"
    
    return preview


# ── Session History Inline Keyboard ────────────────────────────────────────────
def build_session_history_kb(sessions: list, page: int = 0, per_page: int = 8) -> InlineKeyboardMarkup:
    """Build inline keyboard for session history navigation."""
    kb = []
    
    start = page * per_page
    end = min(start + per_page, len(sessions))
    page_sessions = sessions[start:end]
    
    for idx, s in enumerate(page_sessions):
        session_num = start + idx + 1
        # Title (truncated to 30 chars), date, msg count
        title = (s.get("title") or s.get("session_id", "unknown")[:8])[:30]
        created = datetime.fromtimestamp(s.get("created_at", 0)).strftime("%m/%d %H:%M")
        msg_count = s.get("message_count", 0)
        
        display = f"#{session_num} {title}"
        kb.append([InlineKeyboardButton(display, callback_data=f"session_load:{s['session_id']}")])
    
    # Pagination controls
    nav_row = []
    if page > 0:
        nav_row.append(InlineKeyboardButton("⬅️ Prev", callback_data=f"history_page:{page-1}"))
    if end < len(sessions):
        nav_row.append(InlineKeyboardButton("Next ➡️", callback_data=f"history_page:{page+1}"))
    if nav_row:
        kb.append(nav_row)
    
    # Back button
    kb.append([InlineKeyboardButton("🔙 Back to Menu", callback_data="back_to_menu")])
    
    return InlineKeyboardMarkup(kb)

MODES = {
    "plan": ("📝 Plan Mode", "BMO will only outline steps and design solutions without executing code."),
    "ask": ("❓ Ask Mode", "BMO will ask clarifying questions and gather details before proceeding."),
    "execute": ("🚀 Execute Mode", "BMO will actively write code, run commands, and apply changes."),
}

PERSONAS = {
    "default": ("🦾 BMO Default", "The standard helpful superhero assistant."),
    "document": ("📄 The Document", "BMO becomes the uploaded file content. Ask it questions as if it IS the file."),
    "architect": ("🏗️ Python Architect", "Focus on clean, professional, and efficient coding solutions."),
    "security": ("🛡️ Security Auditor", "Identify vulnerabilities and security risks in code or systems."),
}

# ── Running tasks per chat (for Cancel) ───────────────────────────────────────
_running_tasks: dict[int, asyncio.Task] = {}
_file_tasks: dict[int, list[asyncio.Task]] = {} # Track background file analyses
_awaiting_title: set[int] = set()
_awaiting_key: dict[int, dict] = {} # chat_id -> {provider_id, provider_name, env_key}
_onboarding_step: dict[int, int] = {} # chat_id -> step (1: name, 2: prefs)
_agent_creation: dict[int, dict] = {} # chat_id -> {step, mode, name, prompt}

async def handle_settings(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Shows the settings menu."""
    user = update.effective_user
    kb = [
        [InlineKeyboardButton("🔑 Manage API Keys", callback_data="set_keys")],
        [InlineKeyboardButton("📊 System Statistics", callback_data="admin_stats")]
    ]
    
    # Only show admin stats to owner (Aliwi)
    if user.id != 732356803:
        kb = [kb[0]]
        
    await update.message.reply_text(
        "⚙️ <b>BMO Settings</b>\n\nConfigure your API providers and security preferences below:",
        reply_markup=InlineKeyboardMarkup(kb),
        parse_mode=ParseMode.HTML
    )

async def agent_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles agent selection."""
    query = update.callback_query
    data = query.data
    if data.startswith("agent_set:"):
        agent_name = data.split(":")[1]
        await query.answer(f"Selected: {agent_name}")
        await query.edit_message_text(f"🎭 <b>Agent Selected:</b> {agent_name}\n\nWhat would you like {agent_name} to do?", parse_mode=ParseMode.HTML)


# ── Inline Menu Action Handler ──────────────────────────────────────────────
async def inline_action_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles all inline menu button clicks."""
    query = update.callback_query
    data = query.data
    await query.answer()
    
    chat_id = query.message.chat_id
    user = query.from_user
    
    if not _is_authorized(user.id):
        return
    
    # Get current session for inline menu building
    session = storage.load_session(chat_id)
    
    if data == "action:new_session":
        await handle_new_session(update, context)
    elif data == "action:list_sessions":
        await handle_list_sessions_inline(query, session)
    elif data == "action:set_title":
        _awaiting_title.add(chat_id)
        current = ""
        if session and session.title:
            current = f" (current: <b>{session.title}</b>)"
        await query.edit_message_text(
            f"🏷️ <b>Send the session title you want</b>{current}:",
            parse_mode=ParseMode.HTML
        )
    elif data == "action:clear_history":
        await _handle_clear_inline(query, session)
    elif data == "action:choose_model":
        await handle_choose_model(update, context)
    elif data == "action:choose_mode":
        await handle_choose_mode(update, context)
    elif data == "action:agents":
        await handle_agents(update, context)
    elif data == "action:use_skill":
        await handle_use_skill(update, context)
    elif data == "action:status":
        await _handle_status_inline(query, session)
    elif data == "action:settings":
        await handle_settings(update, context)
    elif data == "action:view_history":
        await _handle_view_history_inline(query, session)
    elif data == "action:save_session":
        await _handle_save_session_inline(query, session)
    elif data == "action:launch_webchat":
        await _handle_launch_webchat(update, context)
        return
    elif data == "action:stop_webchat":
        await _handle_stop_webchat(update, context)
        return
    elif data == "back_to_menu":
        await _show_inline_menu(query, session)
    elif data.startswith("history_page:"):
        page = int(data.split(":")[1])
        await handle_list_sessions_inline(query, session, page=page)
    elif data.startswith("session_load:"):
        sid = data.split(":")[1]
        await _handle_load_session(query, sid)


async def handle_list_sessions_inline(query, session, page: int = 0):
    """Show sessions as inline buttons with pagination."""
    chat_id = query.message.chat_id
    all_sessions = storage.list_chat_sessions(chat_id)
    
    if not all_sessions:
        await query.edit_message_text(
            "📋 <b>No Sessions Found</b>\n\nStart a conversation to create your first session!",
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data="back_to_menu")]])
        )
        return
    
    # Sort by created_at desc
    all_sessions.sort(key=lambda x: x.get("created_at", 0), reverse=True)
    
    # Show current session first if exists
    current_sid = session.session_id if session else None
    
    kb = []
    start = page * 8
    end = min(start + 8, len(all_sessions))
    
    for idx, s in enumerate(all_sessions[start:end]):
        session_num = start + idx + 1
        title = (s.get("title") or s.get("session_id", "unknown")[:8])[:35]
        created = datetime.fromtimestamp(s.get("created_at", 0)).strftime("%m/%d %H:%M")
        msg_count = s.get("message_count", 0)
        is_current = " ✅" if s.get("session_id") == current_sid else ""
        display = f"#{session_num} {title}{is_current}"
        kb.append([InlineKeyboardButton(display, callback_data=f"session_load:{s['session_id']}")])
    
    # Pagination
    nav_row = []
    if page > 0:
        nav_row.append(InlineKeyboardButton("⬅️ Prev", callback_data=f"history_page:{page-1}"))
    if end < len(all_sessions):
        nav_row.append(InlineKeyboardButton("Next ➡️", callback_data=f"history_page:{page+1}"))
    if nav_row:
        kb.append(nav_row)
    
    kb.append([InlineKeyboardButton("🔙 Back to Menu", callback_data="back_to_menu")])
    
    text = f"📋 <b>Session History</b> ({len(all_sessions)} total)\n\nSelect a session to load:"
    
    await query.edit_message_text(
        text,
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(kb)
    )


async def _handle_clear_inline(query, session):
    """Handle clear history from inline menu."""
    chat_id = query.message.chat_id
    
    old_title = session.title if session else None
    if old_title:
        msg_count = len(session.messages) if session else 0
        await query.edit_message_text(
            f"📋 <b>Session Cleared</b>\n<b>{old_title}</b>\n📊 Messages: {msg_count}",
            parse_mode=ParseMode.HTML
        )
    
    storage.delete_session(chat_id)
    _ensure_session(chat_id, query.from_user.id, query.from_user.username)
    await query.message.reply_text("🗑️ History cleared! Ready for a fresh start.", reply_markup=MAIN_MENU_KEYBOARD)


async def _handle_status_inline(query, session):
    """Show status from inline menu."""
    if not session:
        session = _ensure_session(query.message.chat_id, query.from_user.id, query.from_user.username)
    
    active_mode = session.metadata.get("active_mode", "execute")
    title = session.title or "Untitled Session"
    msg_count = len(session.messages)
    p_id, m_id = _get_current_model(query.message.chat_id, session)
    
    text = (
        f"ℹ️ <b>BMO System Status</b>\n\n"
        f"🛠️ <b>Mode:</b> <code>{active_mode}</code>\n"
        f"💬 <b>Session:</b> {title}\n"
        f"📊 <b>Messages:</b> {msg_count}\n"
        f"🤖 <b>Model:</b> <code>{m_id}</code> ({p_id})"
    )
    
    await query.edit_message_text(
        text,
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data="back_to_menu")]])
    )


async def _handle_load_session(query, session_id: str):
    """Load a specific session from history."""
    chat_id = query.message.chat_id
    user = query.from_user
    
    # Load the target session
    target_session = storage.get_session_by_id(session_id)
    if not target_session:
        await query.edit_message_text("❌ Session not found.", parse_mode=ParseMode.HTML)
        return
    
    # Save current session first
    current = storage.load_session(chat_id)
    if current and current.messages:
        storage.save_session(current)
    
    # Create a new session with loaded messages (don't overwrite - clone)
    from uuid import uuid4
    now = datetime.now().timestamp()
    new_session = ChatSession(
        chat_id=chat_id,
        user_id=user.id,
        username=user.username,
        created_at=now,
        updated_at=now,
        messages=target_session.messages.copy(),
        metadata={
            "opencode_session_id": None,
            "provider_id": target_session.metadata.get("provider_id", "opencode"),
            "model_id": target_session.metadata.get("model_id", "big-pickle")
        },
        session_id=str(uuid4())
    )
    new_session.title = f"📜 {target_session.title or 'Loaded Session'}"
    storage.save_session(new_session)
    storage.set_active_session(chat_id, new_session.session_id)
    
    await query.edit_message_text(
        f"📜 <b>Session Loaded!</b>\n\n"
        f"🏷️ <b>{new_session.title}</b>\n"
        f"📊 {len(new_session.messages)} messages restored.\n\n"
        f"<i>You can continue where you left off.</i>",
        parse_mode=ParseMode.HTML,
        reply_markup=build_inline_menu(new_session)
    )


async def _show_inline_menu(query, session):
    """Show the inline menu."""
    await query.edit_message_text(
        "⚡ <b>BMO Menu</b>\n\nSelect an action:",
        parse_mode=ParseMode.HTML,
        reply_markup=build_inline_menu(session)
    )


async def _handle_stop_webchat(update, context):
    """Stop the running webchat server and its tunnel."""
    from tools.task_registry import get_active_tasks, remove_task, check_port_conflict
    import psutil

    query = update.callback_query
    await query.edit_message_text(
        "⏹ <b>Stopping Webchat...</b>",
        parse_mode=ParseMode.HTML,
    )

    conflict = check_port_conflict(3456)
    if not conflict:
        await query.edit_message_text(
            "⚠️ <b>No webchat running.</b>",
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data="back_to_menu")]])
        )
        return

    tasks = get_active_tasks()
    stopped = []
    for task in tasks:
        if task.get("port") == 3456 or task.get("type") in ("webchat", "tunnel"):
            try:
                proc = psutil.Process(task["pid"])
                for child in proc.children(recursive=True):
                    child.kill()
                proc.kill()
                remove_task(task["pid"])
                stopped.append(task.get("type", "unknown"))
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                remove_task(task["pid"])
                stopped.append(task.get("type", "unknown"))

    await query.edit_message_text(
        f"⏹ <b>Webchat stopped.</b>\n\nStopped: {', '.join(stopped) if stopped else 'nothing found'}",
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data="back_to_menu")]])
    )


async def _handle_launch_webchat(update, context):
    import re, time, asyncio, subprocess
    from pathlib import Path
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup
    from tools.task_registry import register_task, check_port_conflict
    from tools.mcp_server import _start_detached

    query = update.callback_query
    await query.answer()
    chat_id = query.message.chat_id
    message_id = query.message.message_id

    conflict = check_port_conflict(3456)
    if conflict:
        url = conflict.get("url", "")
        await query.edit_message_text(
            f"✅ <b>Web Chat Already Running!</b>\n\n"
            f"PID: {conflict['pid']}\n"
            + (f"🔗 <a href='{url}'>Open Web Chat</a>\n\n" if url else "")
            + f"Use the Stop button to shut it down.",
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=True,
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data="back_to_menu")]])
        )
        return

    base_dir = Path(__file__).resolve().parent.parent
    webchat_dir = base_dir / 'webchat'
    logs_dir = base_dir / "logs"
    logs_dir.mkdir(exist_ok=True)

    server_port = 3456
    server_log = str(logs_dir / "webchat_server.log")

    server_cmd = "node server.js"
    server_proc = _start_detached(server_cmd, server_log, cwd=str(webchat_dir))
    register_task(
        pid=server_proc.pid,
        command=server_cmd,
        port=server_port,
        task_type="webchat",
        description="Webchat server",
        log_file=server_log,
    )

    await query.edit_message_text(
        "🌐 <b>Starting Web Chat...</b>\n\n"
        "Launching server and tunnel in background. "
        "This usually takes 15-30 seconds. Your bot remains responsive!",
        parse_mode=ParseMode.HTML,
    )

    await asyncio.sleep(2)

    CREATE_NO_WINDOW = 0x08000000
    tunnel_proc = subprocess.Popen(
        ["cloudflared", "tunnel", "--url", f"http://localhost:{server_port}"],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        creationflags=CREATE_NO_WINDOW,
    )
    register_task(
        pid=tunnel_proc.pid,
        command=f"cloudflared tunnel --url http://localhost:{server_port}",
        port=server_port,
        task_type="tunnel",
        description=f"Tunnel for webchat on port {server_port}",
    )

    async def _read_tunnel_url(chat_id, message_id, tunnel_proc, session_id):
        loop = asyncio.get_event_loop()
        start = time.time()
        url = None

        while time.time() - start < 75:
            try:
                line = await asyncio.wait_for(
                    loop.run_in_executor(None, tunnel_proc.stdout.readline),
                    timeout=3,
                )
            except asyncio.TimeoutError:
                continue

            if not line:
                break

            text = line.decode("utf-8", errors="ignore")
            match = re.search(r"https://[a-zA-Z0-9-]+\.trycloudflare\.com", text)
            if match:
                url = match.group(0)
                break

        if url:
            webchat_url = f"{url}?chat_id={chat_id}&session_id={session_id}"
            from tools.task_registry import update_task
            update_task(tunnel_proc.pid, url=webchat_url)

            try:
                import httpx
                async with httpx.AsyncClient(timeout=5) as client:
                    await client.post(
                        f"http://localhost:{server_port}/api/set-session",
                        json={"chat_id": chat_id, "session_id": session_id}
                    )
            except Exception:
                pass
            await context.bot.edit_message_text(
                chat_id=chat_id,
                message_id=message_id,
                text=(
                    f"✅ <b>Web Chat Online!</b>\n\n"
                    f"🌍 Public: <a href='{webchat_url}'>Open Web Chat</a>\n"
                    f"💻 Local: http://localhost:{server_port}\n\n"
                    f"<i>The webchat is using your current session. "
                    f"Open the URL to continue the conversation.</i>"
                ),
                parse_mode=ParseMode.HTML,
                disable_web_page_preview=True,
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🔙 Back", callback_data="back_to_menu")]
                ])
            )

            storage.add_message(
                chat_id, "assistant",
                f"The user moved their conversation to the webchat.\n"
                f"🔗 {webchat_url}\n\n"
                "Same OpenCode session. Reply here to continue on Telegram.",
                interface='telegram'
            )
        else:
            await context.bot.edit_message_text(
                chat_id=chat_id,
                message_id=message_id,
                text=(
                    f"⚠️ <b>Tunnel Still Connecting...</b>\n\n"
                    f"Server PID: {server_proc.pid}\n"
                    f"Tunnel PID: {tunnel_proc.pid}\n\n"
                    f"The tunnel is taking longer than expected. "
                    f"Check status in a moment using the inline menu.",
                ),
                parse_mode=ParseMode.HTML,
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🔙 Back", callback_data="back_to_menu")]
                ])
            )

    session = storage.load_session(chat_id)
    session_id = session.session_id if session else ""
    asyncio.create_task(_read_tunnel_url(chat_id, message_id, tunnel_proc, session_id))


async def _handle_view_history_inline(query, session):
    """Show session history in inline format."""
    chat_id = query.message.chat_id
    all_sessions = storage.list_sessions(chat_id)
    
    if not all_sessions:
        await query.edit_message_text(
            "📜 <b>No History Found</b>\n\nStart a conversation to build your session history!",
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data="back_to_menu")]])
        )
        return
    
    all_sessions.sort(key=lambda x: x.get("created_at", 0), reverse=True)
    
    kb = []
    for s in all_sessions[:12]:
        title = (s.get("title") or s.get("session_id", "unknown")[:8])[:35]
        kb.append([InlineKeyboardButton(f"📂 {title}", callback_data=f"session_load:{s['session_id']}")])
    
    kb.append([InlineKeyboardButton("🔙 Back to Menu", callback_data="back_to_menu")])
    
    text = f"📜 <b>Session History</b> ({len(all_sessions)} total)\n\nSelect a session to load:"
    await query.edit_message_text(text, parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup(kb))


async def _handle_save_session_inline(query, session):
    """Save current session inline."""
    if not session or not session.messages:
        await query.edit_message_text(
            "📭 <b>Nothing to Save</b>\n\nStart a conversation first!",
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data="back_to_menu")]])
        )
        return
    
    msg_count = len(session.messages)
    title = session.title or f"Session {session.session_id[:8]}"
    
    await query.edit_message_text(
        f"💾 <b>Session Saved!</b>\n\n"
        f"🏷️ <b>{title}</b>\n"
        f"📊 {msg_count} messages\n\n"
        "Your session is safely archived.",
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data="back_to_menu")]])
    )


# ── Session Select Callback (existing) ──────────────────────────────────────
async def session_select_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle session selection and loading."""
    query = update.callback_query
    await query.answer()
    
    user = query.from_user
    if not _is_authorized(user.id):
        return
        
    data = query.data
    chat_id = query.message.chat_id

    # CASE 1: Direct switch (from /sessions list)
    if data.startswith("session:"):
        sid = data.split(":")[1]
        if storage.switch_session(chat_id, sid):
            session = storage.load_session(chat_id)
            title = session.title if session and session.title else "untitled"
            msg_count = len(session.messages) if session else 0
            await query.edit_message_text(
                f"✅ <b>Switched to session:</b>\n"
                f"🏷️ <b>{title}</b>\n"
                f"📊 <i>{msg_count} messages in history</i>",
                parse_mode=ParseMode.HTML,
                reply_markup=build_inline_menu(session) if session else None
            )
        else:
            await query.edit_message_text("❌ <b>Session not found.</b>", parse_mode=ParseMode.HTML)
        return

    # CASE 2: Load/Clone (from inline history)
    if data.startswith("session_select:"):
        sid = data.split(":")[1]
        
        # Load the target session
        target_session = storage.get_session_by_id(sid)
        if not target_session:
            await query.edit_message_text("❌ Session not found.", parse_mode=ParseMode.HTML)
            return
            
        from uuid import uuid4
        now = datetime.now().timestamp()
        new_session = ChatSession(
            chat_id=chat_id,
            user_id=user.id,
            username=user.username,
            created_at=now,
            updated_at=now,
            messages=target_session.messages.copy(),
            metadata={
                "opencode_session_id": None,
                "provider_id": target_session.metadata.get("provider_id", "opencode"),
                "model_id": target_session.metadata.get("model_id", "big-pickle")
            },
            session_id=str(uuid4())
        )
        new_session.title = f"📜 {target_session.title or 'Loaded Session'}"
        storage.save_session(new_session)
        storage.set_active_session(chat_id, new_session.session_id)
        
        await query.edit_message_text(
            f"📜 <b>Session Loaded!</b>\n\n"
            f"🏷️ <b>{new_session.title}</b>\n"
            f"📊 {len(new_session.messages)} messages restored.",
            parse_mode=ParseMode.HTML,
            reply_markup=build_inline_menu(new_session)
        )

async def settings_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles settings navigation and key setup."""
    query = update.callback_query
    data = query.data
    chat_id = query.message.chat_id
    user_id = query.from_user.id
    await query.answer()
    
    if data.startswith("perm_"):
        action_parts = data.split("|", 1)
        action = action_parts[0]
        scope = action_parts[1] if len(action_parts) > 1 else "unknown"
        key = (chat_id, scope)
        
        if key not in pending_permissions:
            await query.edit_message_text("❌ This request has expired or already been handled.")
            return
            
        result = "denied"
        status_text = "❌ Permission Denied."
        
        if action == "perm_yes":
            result = "granted"
            status_text = "✅ Permission Granted (One-time)."
        elif action == "perm_no":
            result = "denied"
            status_text = "❌ Permission Denied."
        elif action == "perm_always":
            result = "granted"
            status_text = "🔒 Permission Granted Always (Saved to Vault)."
            storage.save_permission(user_id, scope, 1)

        # Resolve the pending event in the MCP server
        pending_permissions[key]["result"] = result
        pending_permissions[key]["event"].set()
        
        await query.edit_message_text(
            f"🏁 <b>Permission {result.title()}</b>\n"
            f"📁 Scope: <code>{scope}</code>\n\n"
            f"{status_text}", 
            parse_mode=ParseMode.HTML
        )
        return
    
    if data == "set_keys":
        await handle_key_management(update, context)
    elif data == "admin_stats":
        if user_id != 732356803: return
        stats = storage.get_stats()
        is_oc_up = "✅ Connected" if opencode_client.is_connected else "❌ Disconnected"
        text = (
            "📊 <b>BMO System Statistics</b>\n\n"
            f"👤 <b>Total Users:</b> {stats['users']}\n"
            f"📋 <b>Total Sessions:</b> {stats['sessions']}\n"
            f"💬 <b>Total Messages:</b> {stats['messages']}\n"
            f"🔗 <b>OpenCode Server:</b> {is_oc_up}\n\n"
            "<i>Only you can see this message.</i>"
        )
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data="back_settings")]]), parse_mode=ParseMode.HTML)
    elif data == "back_settings":
        kb = [[InlineKeyboardButton("🔑 Manage API Keys", callback_data="set_keys")], [InlineKeyboardButton("📊 System Statistics", callback_data="admin_stats")]]
        if user_id != 732356803: kb = [kb[0]]
        await query.edit_message_text("⚙️ <b>BMO Settings</b>", reply_markup=InlineKeyboardMarkup(kb), parse_mode=ParseMode.HTML)
    elif data.startswith("setup_p_"):
        provider_id = data.replace("setup_p_", "")
        providers_data = await opencode_client.get_providers()
        all_providers = providers_data.get("all", [])
        provider = next((p for p in all_providers if p["id"] == provider_id), None)
        
        if not provider:
            await query.edit_message_text("❌ Provider not found.")
            return
            
        env_keys = provider.get("env", [])
        if not env_keys:
            await query.edit_message_text(f"ℹ️ {provider['name']} does not require an API Key.")
            return
            
        # For simplicity, we assume one key (e.g. ANTHROPIC_API_KEY)
        key_name = env_keys[0]
        _awaiting_key[chat_id] = {
            "provider_id": provider_id,
            "provider_name": provider["name"],
            "key_name": key_name
        }
        
        await query.edit_message_text(
            f"🔑 <b>Setup {provider['name']}</b>\n\n"
            f"Please send your <code>{key_name}</code> now.\n\n"
            "🛡️ <i>Your key will be encrypted and stored in BMO's secure vault.</i>",
            parse_mode=ParseMode.HTML
        )
    elif data.startswith("ttl_"):
        hours = int(data.replace("ttl_", ""))
        pending = context.user_data.get("pending_key")
        if not pending:
            await query.edit_message_text("❌ Session expired. Please try adding the key again.")
            return
            
        ttl = hours if hours > 0 else None
        
        # ── Global Sync for Forever Keys ──
        sync_status = ""
        if hours == 0:
            try:
                if _sync_to_env(pending["key_name"], pending["value"]):
                    sync_status = "\n🌐 <b>System Sync:</b> Key added to global environment."
            except Exception as e:
                logger.error("Env sync failed: %s", e)
                sync_status = f"\n⚠️ <b>Sync Warning:</b> Could not update .env file."

        storage.save_provider_key(
            pending["provider_id"], 
            pending["provider_name"], 
            pending["key_name"], 
            pending["value"], 
            ttl_hours=ttl
        )
        context.user_data.pop("pending_key", None)
        
        # Now fetch models for this specific provider to show them
        providers_data = await opencode_client.get_providers()
        all_p = providers_data.get("all", [])
        p_info = next((p for p in all_p if p["id"] == pending["provider_id"]), None)
        
        duration = f"{hours} hours" if hours > 0 else "Forever 🔒"
        text = (
            f"✅ <b>{pending['provider_name']} Key Saved!</b>\n"
            f"Vault duration: <b>{duration}</b>"
            f"{sync_status}\n\n"
            "🤖 <b>Available Models for this Provider:</b>"
        )
        
        kb = []
        if p_info and p_info.get("models"):
            for m_id, m_data in p_info["models"].items():
                display_name = m_data.get("name", m_id)
                display_name = display_name.replace("-latest", "").replace("-pro", " Pro").replace("-lite", " Lite").replace("-flash", " Flash")
                kb.append([InlineKeyboardButton(f"📡 {display_name}", callback_data=f"m:{pending['provider_id']}:{m_id}")])
        
        kb.append([InlineKeyboardButton("🔙 Back to Settings", callback_data="set_keys")])
        
        await query.edit_message_text(
            text,
            reply_markup=InlineKeyboardMarkup(kb),
            parse_mode=ParseMode.HTML
        )

def _sync_to_env(key_name: str, value: str) -> bool:
    """Writes or updates a key in the project's .env file."""
    env_path = os.path.join(os.path.dirname(__file__), "..", ".env")
    lines = []
    found = False
    
    if os.path.exists(env_path):
        with open(env_path, "r", encoding="utf-8") as f:
            lines = f.readlines()
            
    new_line = f"{key_name}={value}\n"
    
    for i, line in enumerate(lines):
        if line.strip().startswith(f"{key_name}="):
            lines[i] = new_line
            found = True
            break
            
    if not found:
        # Add newline if file doesn't end with one
        if lines and not lines[-1].endswith("\n"):
            lines[-1] += "\n"
        lines.append(new_line)
        
    with open(env_path, "w", encoding="utf-8") as f:
        f.writelines(lines)
    
    # Also update current process environment so Bot picks it up immediately
    os.environ[key_name] = value
    return True

async def handle_key_management(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Shows current keys and allows adding new ones."""
    query = update.callback_query
    providers_data = await opencode_client.get_providers()
    all_providers = providers_data.get("all", [])
    connected_ids = providers_data.get("connected", [])
    
    saved_keys = storage.list_provider_keys()
    
    text = "🔑 <b>Manage API Providers</b>\n\n"
    if saved_keys:
        text += "<b>Decrypted & Active in BMO Vault:</b>\n"
        for k in saved_keys:
            status = "✅"
            text += f"- {k['provider_name']} ({k['provider_id']}) {status}\n"
        text += "\n"
    
    text += "Select a provider below to add or update its API Key:"
    
    # Show top providers
    kb = []
    popular = ["anthropic", "openai", "google", "deepseek", "openrouter", "groq"]
    
    row = []
    for p in all_providers:
        if p["id"] in popular:
            btn_text = f"{p['name']}"
            if p["id"] in connected_ids: btn_text = "🔄 " + btn_text
            row.append(InlineKeyboardButton(btn_text, callback_data=f"setup_p_{p['id']}"))
            if len(row) == 2:
                kb.append(row)
                row = []
    if row: kb.append(row)
    
    kb.append([InlineKeyboardButton("🔙 Back", callback_data="back_settings")])
    
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(kb), parse_mode=ParseMode.HTML)

# ── Auto Summary ──────────────────────────────────────────────────────────────
INACTIVITY_TIMEOUT = 300  # 5 minutes idle → auto summary
_summary_timers: dict[int, asyncio.Task] = {}

CANCEL_KEYBOARD = InlineKeyboardMarkup([
    [InlineKeyboardButton("❌ Cancel", callback_data="cancel")]
])


async def _status_poller(
    bot,
    chat_id: int,
    message_id: int,
    client,
    session_id: str,
    stop_event: asyncio.Event,
):
    """Background task: polls opencode session status and updates Telegram message live."""
    start_time = time.time()
    while not stop_event.is_set():
        elapsed = int(time.time() - start_time)
        
        # Priority: explicit session_id -> client's last active session
        sid = session_id or client.last_session_id
        
        if sid:
            try:
                status = await client.get_session_status(sid)
                # Clean HTML to prevent Telegram parse errors (e.g. from raw snippets with < or &)
                status = _clean_telegram_html(status)
            except Exception as e:
                logger.debug("Error getting status for %s: %s", sid, e)
                status = "🧠 <i>BMO is thinking...</i>"
        else:
            status = "🔗 <i>Connecting...</i>"
            
        text = f"{status} ⏱️ {elapsed}s"
        try:
            await bot.edit_message_text(
                text,
                chat_id=chat_id,
                message_id=message_id,
                parse_mode=ParseMode.HTML,
                reply_markup=CANCEL_KEYBOARD,
            )
        except Exception as e:
            # Common error: "Message is not modified" - we can ignore this
            if "not modified" not in str(e).lower():
                logger.debug("Status poller edit error: %s", e)
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=1.5)
        except asyncio.TimeoutError:
            continue


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _is_authorized(user_id: int) -> bool:
    if not ALLOWED_USER_IDS:
        return True
    return user_id in ALLOWED_USER_IDS


def _chunk_text(text: str, chunk_size: int = CHUNK_SIZE) -> list[str]:
    if len(text) <= chunk_size:
        return [text]
    chunks = []
    while text:
        if len(text) <= chunk_size:
            chunks.append(text)
            break
        split_at = text.rfind("\n", 0, chunk_size)
        if split_at == -1:
            split_at = text.rfind(" ", 0, chunk_size)
        if split_at == -1:
            split_at = chunk_size
        chunks.append(text[:split_at])
        text = text[split_at:].lstrip("\n ")
    return chunks


def _generate_title(text: str, max_len: int = 60) -> str:
    text = text.strip().replace("\n", " ").replace("\r", "")
    if len(text) > max_len:
        text = text[:max_len].rstrip() + "…"
    return text


def _ensure_session(chat_id: int, user_id: int, username: str | None) -> ChatSession:
    session = storage.load_session(chat_id)
    if session is None:
        now = datetime.now().timestamp()
        session = ChatSession(
            chat_id=chat_id,
            user_id=user_id,
            username=username,
            created_at=now,
            updated_at=now,
            messages=[],
            metadata={
                "opencode_session_id": None,
                "provider_id": "opencode",
                "model_id": "big-pickle"
            },
        )
        storage.save_session(session)
    return session


def _convert_md_to_html(text: str) -> str:
    import re

    # 1. Protect code blocks from inner conversion
    code_blocks = []
    def _save_code_block(m):
        code_blocks.append(m.group(2))
        return f"\u00abCODEBLOCK{len(code_blocks)-1}\u00bb"

    text = re.sub(r'```(\w*)\n?(.*?)```', _save_code_block, text, flags=re.DOTALL)

    # 2. Protect inline code
    inline_codes = []
    def _save_inline_code(m):
        inline_codes.append(m.group(1))
        return f"\u00abINLINECODE{len(inline_codes)-1}\u00bb"

    text = re.sub(r'`([^`]+)`', _save_inline_code, text)

    # 3. Convert **bold** (before *italic*)
    text = re.sub(r'\*\*(.+?)\*\*', r'<b>\1</b>', text)

    # 4. Convert *italic* (remaining single asterisks)
    text = re.sub(r'\*(.+?)\*', r'<i>\1</i>', text)

    # 5. Headings -> bold
    text = re.sub(r'^#{1,6}\s+(.+?)$', r'<b>\1</b>', text, flags=re.MULTILINE)

    # 6. Horizontal rules
    text = re.sub(r'^[-*_]{3,}\s*$', '\n---\n', text, flags=re.MULTILINE)

    # 7. List markers
    text = re.sub(r'^[-*]\s+', '• ', text, flags=re.MULTILINE)
    text = re.sub(r'^\d+\.\s+', '', text, flags=re.MULTILINE)

    # 8. Blockquote markers
    text = re.sub(r'^>\s+', '', text, flags=re.MULTILINE)

    # 9. Restore code blocks as <pre>
    for i, block in enumerate(code_blocks):
        text = text.replace(f"\u00abCODEBLOCK{i}\u00bb", f"<pre>{block.strip()}</pre>")

    # 10. Restore inline code as <code>
    for i, code in enumerate(inline_codes):
        text = text.replace(f"\u00abINLINECODE{i}\u00bb", f"<code>{code}</code>")

    return text


def _clean_telegram_html(text: str) -> str:
    """Fix common HTML tag hallucinations from models to ensure Telegram accepts them."""
    import re
    # Fix invented/broken tags
    text = text.replace("<//t>", "</b>")
    text = text.replace("<br/>", "\n").replace("<br>", "\n")
    text = text.replace("<hr/>", "\n---\n")
    
    # Telegram DOES NOT support <ul>, <li>, <ol>, <h3>, etc.
    # Convert them to plain text or supported tags
    text = re.sub(r'</?ul>', '', text, flags=re.IGNORECASE)
    text = re.sub(r'</?ol>', '', text, flags=re.IGNORECASE)
    text = re.sub(r'<li>', '• ', text, flags=re.IGNORECASE)
    text = re.sub(r'</li>', '\n', text, flags=re.IGNORECASE)
    
    # Convert Headings to Bold
    text = re.sub(r'<h[1-6]>', '<b>', text, flags=re.IGNORECASE)
    text = re.sub(r'</h[1-6]>', '</b>\n', text, flags=re.IGNORECASE)

    # Remove markdown backticks that might be wrapping <pre> blocks
    text = re.sub(r'```(?:html)?\n?(<pre>.*?</pre>)\n?```', r'\1', text, flags=re.DOTALL)
    
    # Auto-close unclosed tags (basic logic)
    for tag in ["b", "i", "code", "pre"]:
        open_count = len(re.findall(rf"<{tag}>", text))
        close_count = len(re.findall(rf"</{tag}>", text))
        if open_count > close_count:
            text += f"</{tag}>"
            
    return text


def _sanitize_for_telegram(text: str) -> str:
    return _clean_telegram_html(_convert_md_to_html(text))


async def _send_safe(send_func, text: str, parse_mode=None):
    """Try MarkdownV2 first, then HTML, then plain text."""
    if parse_mode:
        try:
            await send_func(text, parse_mode=parse_mode)
            return
        except Exception:
            pass
    if not parse_mode or parse_mode != ParseMode.MARKDOWN_V2:
        try:
            await send_func(text, parse_mode=ParseMode.MARKDOWN_V2)
            return
        except Exception:
            pass
    try:
        safe = _sanitize_for_telegram(text)
        await send_func(safe, parse_mode=ParseMode.HTML)
    except Exception:
        await send_func(text)


async def _send_chunks(update: Update, context: ContextTypes.DEFAULT_TYPE, waiting_msg, text: str) -> None:
    chunks = _chunk_text(text)
    edit = waiting_msg.edit_text
    reply = update.message.reply_text

    async def _send(first_chunk, send_func, parse_mode):
        if parse_mode:
            try:
                await send_func(first_chunk, parse_mode=parse_mode)
                return True
            except Exception:
                return False
        else:
            await send_func(first_chunk)
            return True

    if not await _send(chunks[0], edit, ParseMode.MARKDOWN_V2):
        safe = _sanitize_for_telegram(chunks[0])
        if not await _send(safe, edit, ParseMode.HTML):
            await _send(chunks[0], edit, None)
    for chunk in chunks[1:]:
        if not await _send(chunk, reply, ParseMode.MARKDOWN_V2):
            safe = _sanitize_for_telegram(chunk)
            if not await _send(safe, reply, ParseMode.HTML):
                await _send(chunk, reply, None)
    
    # Check if the text contains a file path to send
    await _check_and_send_files(update, context, text)


async def _check_and_send_files(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str) -> None:
    """Scans text for potential file paths and sends them as documents if they exist."""
    import re, json, time
    from datetime import datetime
    # Look for paths in backticks, <code> tags, quotes, or as absolute paths
    # Matches patterns like `C:\path\to\file`, <code>C:\path\to\file</code>, /home/user/file.ext, or File saved at: path
    paths = re.findall(r'(?:[`"\'\s>]|code>)([a-zA-Z]:\\[^`"\'\s\n<>]+|/[^`"\'\s\n<>]+)', text)
    
    # Also check for explicit tool-like patterns
    paths += re.findall(r'File saved at:?\s*[`"\'\s]?([^`"\'\s\n]+)', text)
    
    # Get current session ID
    chat_id = update.effective_chat.id
    session = storage.load_session(chat_id)
    session_uuid = session.session_id if session else "unknown"
    
    # Files directory — date-based subfolders
    files_dir = Path(__file__).resolve().parent.parent / "data" / "files"
    files_dir.mkdir(parents=True, exist_ok=True)
    
    today = datetime.now().strftime("%Y-%m-%d")
    day_dir = files_dir / today
    day_dir.mkdir(parents=True, exist_ok=True)
    
    # File index
    index_path = files_dir / "file_index.json"
    file_index = {}
    if index_path.exists():
        try:
            file_index = json.loads(index_path.read_text(encoding="utf-8"))
        except Exception:
            file_index = {}
    
    sent_paths = set()
    for path_str in paths:
        path_str = path_str.strip('`"\' ')
        try:
            p = Path(path_str)
            if p.is_file() and path_str not in sent_paths:
                import shutil
                ts = datetime.now().strftime("%H%M%S")
                archived_name = f"{session_uuid}_{ts}_{p.name}"
                archived_path = day_dir / archived_name
                
                shutil.copy2(str(p), str(archived_path))
                
                # Update index
                file_index[archived_name] = {
                    "session_id": session_uuid,
                    "date": today,
                    "time": ts,
                    "chat_id": chat_id,
                    "original_path": path_str,
                    "archived_path": str(archived_path),
                    "filename": p.name
                }
                index_path.write_text(json.dumps(file_index, ensure_ascii=False, indent=2), encoding="utf-8")
                
                logger.info("Auto-sending file detected in response: %s → %s", path_str, archived_name)
                await update.message.reply_document(
                    document=open(archived_path, 'rb'),
                    filename=p.name,
                    caption=f"📄 {p.name}"
                )
                sent_paths.add(path_str)
        except Exception as e:
            logger.error("Failed to auto-send file %s: %s", path_str, e)


# ─────────────────────────────────────────────────────────────────────────────
# Handlers for Command Buttons
# ─────────────────────────────────────────────────────────────────────────────

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    user = update.effective_user
    
    if not _is_authorized(user.id):
        await update.message.reply_text("⛔ Not authorised.")
        return

    _ensure_session(chat_id, user.id, user.username)

    # Check if user has memory/profile
    memory = storage.load_memory(chat_id)
    if not memory:
        # Start Onboarding
        _onboarding_step[chat_id] = 1
        await update.message.reply_text(
            f"🦾 <b>أهلاً بك في عالم BMO!</b>\n\n"
            "أنا مساعدك الذكي المتحمس لمساعدتك في رحلتك البرمجية والتقنية.\n"
            "قبل ما نبدأ، حاب أتعرف عليك أكثر.. <b>شنو تحب أناديك؟</b> (الاسم المفضل)",
            parse_mode=ParseMode.HTML
        )
        return

    if not opencode_client.is_connected:
        await opencode_client.connect()

    # Normal Welcome for existing users
    name = memory.preferences.get("name", user.first_name)
    continuity = ""
    last_session = storage.load_session(chat_id)
    if last_session and last_session.get_summary():
        s = last_session.get_summary()[:200]
        continuity = f"\n\n📋 <b>موجز آخر جلسة:</b>\n<i>{s}...</i>\n"

    text = (
        f"⚡ <b>BMO online, {name}!</b>{continuity}\n\n"
        "أنا جاهز لمساعدتك، اطلب أي شي أو ارسل ملفاتك للتحليل!"
    )
    await update.message.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=MAIN_MENU_KEYBOARD)


async def menu_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show inline menu via /menu command."""
    chat_id = update.effective_chat.id
    user = update.effective_user
    
    if not _is_authorized(user.id):
        await update.message.reply_text("⛔ Not authorised.")
        return
    
    session = storage.load_session(chat_id)
    await update.message.reply_text(
        "⚡ <b>BMO Menu</b>\n\nSelect an action:",
        parse_mode=ParseMode.HTML,
        reply_markup=build_inline_menu(session)
    )


async def handle_new_session(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    user = update.effective_user

    # Auto-summarize the old session before archiving
    old_session = storage.load_session(chat_id)
    if old_session and old_session.messages and not old_session.get_summary():
        try:
            # Enhanced Summarization Prompt
            history = old_session.get_context_text(max_messages=100) # Give full history for summary
            sp = (
                "لخص هذه الجلسة البرمجية بشكل احترافي ومفصل باللغة العربية.\n"
                "يجب أن يتضمن الملخص:\n"
                "1. الهدف الرئيسي من الجلسة.\n"
                "2. المشاكل التي تم حلها والكود الذي تم كتابته.\n"
                "3. القرارات التقنية المهمة.\n"
                "4. ما الذي يجب إكماله في الجلسة القادمة.\n\n"
                "السياق:\n" + history
            )
            # Summarization with 30s timeout - won't block new session creation
            summary = await asyncio.wait_for(
                opencode_client.send_query(sp, active_mode="ask", chat_id=chat_id),
                timeout=30.0
            )
            if summary and not summary.startswith("Error"):
                old_session.set_summary(summary)
                storage.save_session(old_session)
        except asyncio.TimeoutError:
            logger.warning("Summarization timed out after 30s for chat %s", chat_id)
        except Exception as e:
            logger.warning("Summarization failed for chat %s: %s", chat_id, e)

    if old_session and old_session.messages:
        msg_count = len(old_session.messages)
        title = old_session.title or old_session.session_id[:8]
        await update.message.reply_text(
            f"📋 <b>نسدت المحادثة وانخزنت بالسجل</b>\n"
            f"<b>{title}</b>\n"
            f"📊 إجمالي الرسائل: {msg_count}",
            parse_mode=ParseMode.HTML,
        )

    # Handle both Message and CallbackQuery
    chat_id = update.effective_chat.id
    user = update.effective_user
    
    # 1. Clean up old session resources if any
    if chat_id in _running_tasks:
        task = _running_tasks.get(chat_id)
        if task and not task.done():
            task.cancel()
    
    # 2. Create a BRAND NEW session
    from uuid import uuid4
    now = datetime.now().timestamp()
    new_session = ChatSession(
        chat_id=chat_id,
        user_id=user.id,
        username=user.username,
        created_at=now,
        updated_at=now,
        messages=[],
        metadata={
            "opencode_session_id": None,
            "provider_id": "opencode",
            "model_id": "big-pickle"
        },
        session_id=str(uuid4())
    )
    storage.save_session(new_session)
    storage.set_active_session(chat_id, new_session.session_id)
    _chat_model[chat_id] = ("opencode", "big-pickle")
    
    msg_text = (
        "✨ <b>بدأنا جلسة جديدة!</b>\n"
        "تم تصفير الذاكرة وجاهز لمهمة جديدة. شنو نسوي اليوم؟"
    )
    
    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.edit_message_text(msg_text, reply_markup=build_inline_menu(new_session), parse_mode=ParseMode.HTML)
    else:
        await update.message.reply_text(msg_text, reply_markup=MAIN_MENU_KEYBOARD, parse_mode=ParseMode.HTML)

async def clear_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    user = update.effective_user

    old_session = storage.load_session(chat_id)
    if old_session and old_session.title:
        msg_count = len(old_session.messages)
        await update.message.reply_text(
            f"📋 <b>Session Archived</b>\n"
            f"<b>{old_session.title}</b>\n"
            f"📊 Total Messages: {msg_count}",
            parse_mode=ParseMode.HTML,
        )

    storage.delete_session(chat_id)
    _ensure_session(chat_id, user.id, user.username)
    await update.message.reply_text("🗑️ History cleared! Ready for a fresh start.", reply_markup=MAIN_MENU_KEYBOARD)


async def handle_clear_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Alias for clear_command for ReplyKeyboard."""
    await clear_command(update, context)


async def _handle_view_history(update: Update) -> None:
    """Show full session history with dates."""
    chat_id = update.effective_chat.id
    all_sessions = storage.list_sessions(chat_id)
    
    if not all_sessions:
        await update.message.reply_text(
            "📜 <b>No History Found</b>\n\nStart a conversation to build your session history!",
            parse_mode=ParseMode.HTML
        )
        return
    
    # Sort by created_at desc
    all_sessions.sort(key=lambda x: x.get("created_at", 0), reverse=True)
    
    text = f"📜 <b>Session History</b> ({len(all_sessions)} total)\n\n"
    
    for idx, s in enumerate(all_sessions[:10], 1):
        title = s.get("title") or "Untitled"
        created = datetime.fromtimestamp(s.get("created_at", 0))
        date_str = created.strftime("%Y-%m-%d %H:%M")
        msg_count = s.get("message_count", 0)
        
        # Show first 40 chars of title
        short_title = title[:40] + "..." if len(title) > 40 else title
        
        text += f"{idx}. <b>{short_title}</b>\n"
        text += f"   📅 {date_str} | 💬 {msg_count} msgs\n\n"
    
    if len(all_sessions) > 10:
        text += f"... and {len(all_sessions) - 10} more sessions."
    
    # Add inline keyboard for quick load
    kb = [[InlineKeyboardButton("📂 Load a Session", callback_data="action:list_sessions")]]
    await update.message.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup(kb))


async def _handle_save_session(update: Update) -> None:
    """Manually save and summarize current session."""
    chat_id = update.effective_chat.id
    session = storage.load_session(chat_id)
    
    if not session or not session.messages:
        await update.message.reply_text("📭 <b>Nothing to Save</b>\n\nStart a conversation first!")
        return
    
    msg_count = len(session.messages)
    title = session.title or f"Session {session.session_id[:8]}"
    
    # Create backup summary
    summary_text = session.get_summary()
    
    await update.message.reply_text(
        f"💾 <b>Session Saved!</b>\n\n"
        f"🏷️ <b>{title}</b>\n"
        f"📊 {msg_count} messages\n"
        f"📝 Summary: {summary_text[:100]}..." if summary_text else "📝 Summary: Pending...",
        parse_mode=ParseMode.HTML
    )


async def _handle_load_sessions(update: Update) -> None:
    """Show sessions available to load."""
    chat_id = update.effective_chat.id
    all_sessions = storage.list_sessions(chat_id)
    
    if not all_sessions:
        await update.message.reply_text(
            "📂 <b>No Sessions to Load</b>\n\nYour sessions will appear here.",
            parse_mode=ParseMode.HTML
        )
        return
    
    # Sort and show most recent first
    all_sessions.sort(key=lambda x: x.get("created_at", 0), reverse=True)
    
    # Build inline list
    kb = []
    for s in all_sessions[:15]:
        title = (s.get("title") or s.get("session_id", "unknown")[:8])[:35]
        kb.append([InlineKeyboardButton(f"📂 {title}", callback_data=f"session_load:{s['session_id']}")])
    
    kb.append([InlineKeyboardButton("🔙 Back to Menu", callback_data="back_to_menu")])
    
    text = f"📂 <b>Load a Session</b> ({len(all_sessions)} total)\n\nSelect a session to load:"
    
    await update.message.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup(kb))


async def handle_choose_model(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    waiting = await update.message.reply_text("🔍 <i>Scanning BMO's brain for available models...</i>", parse_mode=ParseMode.HTML)
    await _render_model_page(update, context, p_idx=0, m_page=0, target_msg=waiting)

async def _render_model_page(update: Update, context: ContextTypes.DEFAULT_TYPE, p_idx: int, m_page: int, target_msg=None) -> None:
    # 1. Fetch live providers
    providers_data = await opencode_client.get_providers()
    all_p = providers_data.get("all", [])
    connected_ids = providers_data.get("connected", [])
    saved_ids = [k["provider_id"] for k in storage.list_provider_keys()]
    
    # 2. Filter and Sort Providers
    valid_providers = []
    for p in all_p:
        p_id = p["id"]
        is_accessible = (p_id in connected_ids) or (p_id in saved_ids)
        models = p.get("models", {})
        
        provider_models = []
        for mid, m_data in models.items():
            is_free = (":free" in mid.lower()) or ("free" in m_data.get("name", "").lower()) or (p_id == "opencode")
            if is_accessible or is_free:
                label = m_data.get("name", mid)
                label = label.replace("-latest", "").replace("-pro", " Pro").replace("-lite", " Lite").replace("-flash", " Flash")
                prefix = "🆓" if is_free else "📡"
                if p_id in ["google", "openai", "anthropic", "deepseek"]: prefix = "✨"
                
                provider_models.append({
                    "pid": p_id,
                    "mid": mid,
                    "label": f"{prefix} {label}",
                    "is_free": is_free
                })
        
        if provider_models:
            provider_models.sort(key=lambda x: not x["is_free"]) # Free first
            valid_providers.append({
                "id": p_id,
                "name": p.get("name", p_id),
                "models": provider_models
            })

    # 2.5 Ensure 'opencode' is always the first provider (Zen)
    valid_providers.sort(key=lambda x: x["id"] != "opencode")

    if not valid_providers:
        text = "❌ No available models found. Try adding a provider key first."
        if update.callback_query: await update.callback_query.edit_message_text(text)
        elif target_msg: await target_msg.edit_text(text)
        return

    # 3. Handle Pagination Logic
    p_idx = max(0, min(p_idx, len(valid_providers) - 1))
    current_p = valid_providers[p_idx]
    
    models_per_page = 20
    max_m_page = (len(current_p["models"]) - 1) // models_per_page
    m_page = max(0, min(m_page, max_m_page))
    
    start = m_page * models_per_page
    end = start + models_per_page
    page_models = current_p["models"][start:end]
    
    # 4. Build Keyboard
    kb = []
    row = []
    for m in page_models:
        cb_data = f"m:{m['pid']}:{m['mid']}"
        if len(cb_data.encode('utf-8')) <= 64:
            row.append(InlineKeyboardButton(m["label"], callback_data=cb_data))
            if len(row) == 2:
                kb.append(row)
                row = []
    if row: kb.append(row)
    
    # Model Pagination Row
    m_nav = []
    if m_page > 0:
        m_nav.append(InlineKeyboardButton("⬅️ Prev Models", callback_data=f"mp:{p_idx}:{m_page-1}"))
    if m_page < max_m_page:
        m_nav.append(InlineKeyboardButton("Next Models ➡️", callback_data=f"mp:{p_idx}:{m_page+1}"))
    if m_nav: kb.append(m_nav)
    
    # Provider Pagination Row
    p_nav = []
    if p_idx > 0:
        p_nav.append(InlineKeyboardButton("⏮ Previous Provider", callback_data=f"mp:{p_idx-1}:0"))
    if p_idx < len(valid_providers) - 1:
        p_nav.append(InlineKeyboardButton("Next Provider ⏭", callback_data=f"mp:{p_idx+1}:0"))
    if p_nav: kb.append(p_nav)
    
    status_msg = (
        f"🤖 <b>Select a Model</b>\n"
        f"🏢 <b>Provider:</b> {current_p['name']} ({p_idx+1}/{len(valid_providers)})\n"
        f"📄 <b>Page:</b> {m_page+1}/{max_m_page+1}\n\n"
        "Free models 🆓 are shown first. ✨ are premium. 📡 use your keys."
    )
    
    try:
        if update.callback_query:
            await update.callback_query.edit_message_text(status_msg, reply_markup=InlineKeyboardMarkup(kb), parse_mode=ParseMode.HTML)
        elif target_msg:
            await target_msg.edit_text(status_msg, reply_markup=InlineKeyboardMarkup(kb), parse_mode=ParseMode.HTML)
    except Exception as e:
        logger.error("Error rendering model page: %s", e)


async def choose_model_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    
    user = query.from_user
    if not _is_authorized(user.id):
        return

    data = query.data
    chat_id = update.effective_chat.id
    
    # 1. Handle Pagination
    if data.startswith("mp:"):
        try:
            parts = data.split(":")
            if len(parts) == 3:
                p_idx, m_page = int(parts[1]), int(parts[2])
                await _render_model_page(update, context, p_idx, m_page)
        except Exception as e:
            logger.error("Pagination error: %s", e)
        return

    # 2. Handle Model Selection
    if data.startswith("m:"):
        try:
            _, provider_id, model_id = data.split(":", 2)
            
            # Ensure a session exists (Recovery)
            session = _ensure_session(chat_id, user.id, user.username)
            
            # Update session metadata for persistence
            if session:
                session.metadata["provider_id"] = provider_id
                session.metadata["model_id"] = model_id
                # HARD RESET: Force the next message to create a fresh backend session
                session.metadata["opencode_session_id"] = None
                storage.save_session(session)
            
            # Clear memory cache to ensure the bot uses the new model immediately
            _chat_model.pop(chat_id, None)
            
            # ── Global Environment Sync ──
            try:
                _sync_to_env("OPENCODE_PROVIDER", provider_id)
                _sync_to_env("OPENCODE_MODEL", model_id)
            except Exception as e:
                logger.error("Global model sync failed: %s", e)

            # Update memory cache
            _chat_model[chat_id] = (provider_id, model_id)

            # Clean label for confirmation message
            display_name = model_id.split("/")[-1] if "/" in model_id else model_id
            display_name = display_name.replace("-latest", "").replace("-pro", " Pro").replace("-lite", " Lite").replace("-flash", " Flash").title()
            
            # Sanitize for HTML
            safe_p_id = provider_id.replace("<", "").replace(">", "")
            safe_m_id = display_name.replace("<", "").replace(">", "")
            
            await query.edit_message_text(
                f"✅ <b>Model Activated!</b>\n\n"
                f"🏢 Provider: <code>{safe_p_id}</code>\n"
                f"🤖 Model: <code>{safe_m_id}</code>\n\n"
                "BMO is ready for your next message.",
                parse_mode=ParseMode.HTML
            )
        except Exception as e:
            logger.error("Model selection error for chat %s: %s", chat_id, e)
            await query.edit_message_text(
                f"❌ <b>Selection Error</b>\n\n<i>{str(e)}</i>",
                parse_mode=ParseMode.HTML
            )
        return


async def handle_use_skill(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        async with httpx.AsyncClient(base_url=OPENCODE_BASE_URL, timeout=10) as client:
            r = await client.get("/agent")
            agents = r.json() if r.status_code == 200 else []
    except Exception:
        agents = []

    if not agents:
        await update.message.reply_text("⚠️ Could not fetch agents.")
        return

    keyboard = [
        [InlineKeyboardButton(
            f"🔹 {a['name']}",
            callback_data=f"skill:{a['name']}"
        )]
        for a in agents[:12]
    ]
    
    text = "🛠️ <b>Available Skills & Agents:</b>\n"
    for a in agents[:12]:
        desc = a.get('description', 'No description')
        text += f"• <b>{a['name']}</b>: <i>{desc}</i>\n"

    await update.message.reply_text(
        text,
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode=ParseMode.HTML,
    )


async def use_skill_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    if not _is_authorized(query.from_user.id):
        return
    _, skill_name = query.data.split(":", 1)
    context.chat_data["active_skill"] = skill_name
    await query.edit_message_text(
        f"🛠️ Skill <b>{skill_name}</b> activated.\n"
        f"<i>Your next message will be handled by this agent.</i>",
        parse_mode=ParseMode.HTML,
    )


async def handle_choose_mode(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    session = _ensure_session(chat_id, update.effective_user.id, update.effective_user.username)
    current_mode = session.metadata.get("active_mode", "execute")
    
    keyboard = [
        [InlineKeyboardButton(f"{'✅ ' if m == current_mode else ''}{label}", callback_data=f"mode_set:{m}")]
        for m, (label, _) in MODES.items()
    ]
    
    text = "⚡ <b>Select Operation Mode:</b>\n\n"
    for m, (label, desc) in MODES.items():
        text += f"• <b>{label}</b>: {desc}\n"
    
    await update.message.reply_text(
        text,
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode=ParseMode.HTML,
    )

async def mode_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    if not _is_authorized(query.from_user.id):
        return

    _, mode_id = query.data.split(":", 1)
    chat_id = query.message.chat_id
    session = storage.load_session(chat_id)
    if session:
        session.metadata["active_mode"] = mode_id
        storage.save_session(session)
        
        label, _ = MODES.get(mode_id, ("Unknown", ""))
        await query.edit_message_text(
            f"⚡ Mode updated to <b>{label}</b>\n"
            f"<i>BMO will now follow these protocols.</i>",
            parse_mode=ParseMode.HTML,
        )


async def cancel_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    chat_id = query.message.chat_id
    await query.answer('Cancelling...')
    try:
        await query.edit_message_text('❌ <b>Cancelled</b>', parse_mode=ParseMode.HTML)
    except: pass
    task = _running_tasks.get(chat_id)
    if task and not task.done(): task.cancel()
    if chat_id in _file_tasks:
        for t in _file_tasks[chat_id]:
            if not t.done(): t.cancel()
        _file_tasks.pop(chat_id, None)


# ── Auto Summary ────────────────────────────────────────────────────────────

async def _auto_summary_task(chat_id: int, session_id: str, bot):
    """After inactivity, generate & store session summary and title."""
    try:
        await asyncio.sleep(INACTIVITY_TIMEOUT)
        session = storage.load_session(chat_id)
        if not session or session.session_id != session_id:
            return
        if len(session.messages) < 2 or session.get_summary():
            return

        history = session.get_context_text(max_messages=100)
        prompt = (
            "Based on this conversation history:\n"
            f"{history}\n\n"
            "1. Provide a concise summary of the work done.\n"
            "2. Suggest a short 3-4 word title for this session.\n\n"
            "Format your response as:\nSUMMARY: [summary text]\nTITLE: [suggested title]"
        )
        response = await opencode_client.send_query(prompt, active_mode="ask", chat_id=chat_id)
        if response and not response.startswith("Error"):
            summary = response
            title = None
            if "SUMMARY:" in response and "TITLE:" in response:
                parts = response.split("TITLE:")
                summary = parts[0].replace("SUMMARY:", "").strip()
                title = parts[1].strip()
            session.set_summary(summary)
            if title:
                session.set_title(title)
            storage.save_session(session)
            short = summary[:250] + "…" if len(summary) > 250 else summary
            title_info = f" 🏷️ <b>{title}</b>" if title else ""
            await _send_safe(
                lambda text, pm=None: bot.send_message(chat_id=chat_id, text=text, parse_mode=pm),
                f"📝 <b>Session Summary Saved</b>{title_info}\n\n{short}"
            )
    except asyncio.CancelledError:
        pass
    except Exception as e:
        logger.debug("Auto-summary: %s", e)


def _schedule_auto_summary(chat_id: int, session: ChatSession, bot):
    existing = _summary_timers.get(chat_id)
    if existing and not existing.done():
        existing.cancel()
    task = asyncio.create_task(_auto_summary_task(chat_id, session.session_id, bot))
    _summary_timers[chat_id] = task


# ── CORE HANDLERS ─────────────────────────────────────────────────────────────

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Main text message handler."""
    chat_id = update.effective_chat.id
    user = update.effective_user
    text = update.message.text

    if not _is_authorized(user.id):
        await update.message.reply_text("⛔ Not authorised.")
        return

    # Handle Menu Buttons
    if text == BTN_AGENTS:
        await handle_agents(update, context)
        return

    if text == BTN_NEW_SESSION:
        await handle_new_session(update, context)
        return
    elif text == BTN_CHOOSE_MODEL:
        await handle_choose_model(update, context)
        return
    elif text == BTN_SKILLS:
        await handle_use_skill(update, context)
        return
    elif text == "⚡ Choose Mode":
        await handle_choose_mode(update, context)
        return
    elif text == BTN_STATUS:
        session = _ensure_session(chat_id, user.id, user.username)
        active_mode = session.metadata.get("active_mode", "execute")
        title = session.title or "Untitled Session"
        msg_count = len(session.messages)
        p_id, m_id = _get_current_model(chat_id, session)
        
        # Verify with Server Truth
        server_model_id = "Unknown"
        opencode_sid = session.metadata.get("opencode_session_id")
        if opencode_sid:
            info = await opencode_client.get_session_info(opencode_sid)
            if info and "model" in info:
                server_model_id = info["model"].get("id", "Unknown")
        
        sync_status = "✅ Synced" if (server_model_id == "Unknown" or m_id in server_model_id or server_model_id in m_id) else "⚠️ Mismatch"
        
        status_text = (
            f"ℹ️ <b>BMO System Status</b>\n\n"
            f"🛠️ <b>Mode:</b> <code>{active_mode}</code>\n"
            f"💬 <b>Conversation:</b> {title}\n"
            f"📊 <b>Messages:</b> {msg_count}\n\n"
            f"🤖 <b>Bot Selected:</b> <code>{m_id}</code>\n"
            f"🖥️ <b>Server Active:</b> <code>{server_model_id}</code>\n"
            f"🔗 <b>Sync Status:</b> {sync_status}"
        )
        await update.message.reply_text(status_text, parse_mode=ParseMode.HTML)
        return
    elif text == BTN_RELOAD:
        await handle_system_reload(update, context)
        return
    elif text == BTN_SETTINGS:
        await handle_settings(update, context)
        return
    elif text == BTN_MENU:
        session = storage.load_session(chat_id)
        await update.message.reply_text(
            "⚡ <b>BMO Menu</b>\n\nSelect an action:",
            parse_mode=ParseMode.HTML,
            reply_markup=build_inline_menu(session)
        )
        return
    elif text == BTN_SESSIONS:
        await update.message.reply_text(
            "📋 <b>Loading Session History...</b>",
            parse_mode=ParseMode.HTML
        )
        session = storage.load_session(chat_id)
        fake_query = type('obj', (object,), {
            'message': update.message,
            'edit_message_text': update.message.reply_text,
            'from_user': user
        })()
        await handle_list_sessions_inline(fake_query, session)
        return
    elif text == BTN_SET_TITLE:
        _awaiting_title.add(chat_id)
        current = ""
        session = storage.load_session(chat_id)
        if session and session.title:
            current = f" (current: <b>{session.title}</b>)"
        await update.message.reply_text(
            f"🏷️ <b>Send the session title you want</b>{current}:",
            parse_mode=ParseMode.HTML,
        )
        return
    elif text == BTN_SUMMARY:
        await handle_session_summary(update, context)
        return
    elif text == BTN_RESET_HISTORY:
        session = storage.load_session(chat_id)
        if not session or not session.metadata.get("opencode_session_id"):
            await update.message.reply_text("⚠️ No active session to reset.")
            return
        
        waiting = await update.message.reply_text("🗑️ <i>Resetting session history...</i>", parse_mode=ParseMode.HTML)
        success = await opencode_client.delete_messages(session.metadata["opencode_session_id"])
        
        if success:
            # Also clear local message cache if needed
            session.messages = []
            storage.save_session(session)
            await waiting.edit_text("✅ <b>Session History Reset!</b>\nThe server's memory has been wiped clean for this session.", parse_mode=ParseMode.HTML)
        else:
            await waiting.edit_text("❌ Failed to reset history on the server.")
        return
    elif text == BTN_HISTORY:
        await _handle_view_history(update)
        return
    elif text == BTN_VIEW_HIST:  # Legacy alias
        await _handle_view_history(update)
        return
    elif text == BTN_SAVE:
        await _handle_save_session(update)
        return
    elif text == BTN_SAVE_SESSION:  # Legacy alias
        await _handle_save_session(update)
        return
    elif text == BTN_LOAD:
        await _handle_load_sessions(update)
        return
    elif text == BTN_LOAD_SESSION:  # Legacy alias
        await _handle_load_sessions(update)
        return
    elif text == BTN_SKILLS:
        await handle_use_skill(update, context)
        return
    elif text == BTN_USE_SKILL:  # Legacy alias
        await handle_use_skill(update, context)
        return
    elif text == BTN_MODE:
        await handle_choose_mode(update, context)
        return
    elif text == BTN_CLEAR_HIST:
        await handle_clear_command(update, context)
        return
    elif text == BTN_RELOAD:
        await handle_system_reload(update, context)
        return

    # Handle Key Input
    if chat_id in _awaiting_key:
        info = _awaiting_key.pop(chat_id)
        test_msg = await update.message.reply_text(
            f"⏳ <b>Testing {info['provider_name']} API Key...</b>\n"
            "Please wait a moment while BMO verifies the connection.",
            parse_mode=ParseMode.HTML
        )
        
        # 1. Fetch available models for this provider
        providers_data = await opencode_client.get_providers()
        all_p = providers_data.get("all", [])
        p_info = next((p for p in all_p if p["id"] == info["provider_id"]), None)
        
        models_dict = p_info.get("models", {}) if p_info else {}
        if not models_dict:
            await test_msg.edit_text(f"❌ Error: Could not find any models to test for {info['provider_name']}.")
            return
            
        # Pick first active, text-capable model (avoid deprecated/speech-only)
        test_model = None
        for m_id, m_data in models_dict.items():
            status = m_data.get("status", "")
            can_text = m_data.get("capabilities", {}).get("input", {}).get("text", False)
            if status == "active" and can_text:
                test_model = m_id
                break
        if not test_model:
            test_model = list(models_dict.keys())[0]  # fallback to first
        
        # 2. Try a real test request using the new dedicated method
        try:
            success, message = await opencode_client.test_provider_key(
                provider_id=info["provider_id"],
                model_id=test_model,
                env={info["key_name"]: text}
            )
            
            if success:
                # Store key in context temporarily until TTL is chosen
                context.user_data["pending_key"] = {
                    "provider_id": info["provider_id"],
                    "provider_name": info["provider_name"],
                    "key_name": info["key_name"],
                    "value": text
                }
                
                kb = [
                    [InlineKeyboardButton("1 Hour", callback_data="ttl_1"), InlineKeyboardButton("24 Hours", callback_data="ttl_24")],
                    [InlineKeyboardButton("7 Days", callback_data="ttl_168"), InlineKeyboardButton("Forever 🔒", callback_data="ttl_0")]
                ]
                
                await test_msg.edit_text(
                    f"✅ <b>{info['provider_name']} Verified!</b>\n\n"
                    "Connection confirmed. How long should BMO keep this key in the secure vault?\n"
                    "<i>It will be automatically deleted after this period.</i>",
                    reply_markup=InlineKeyboardMarkup(kb),
                    parse_mode=ParseMode.HTML
                )
            else:
                await test_msg.edit_text(
                    f"❌ <b>Verification Failed</b>\n\n"
                    f"Reason: <i>{message}</i>\n\n"
                    "The key was <b>not</b> saved. Please check your key and try again.",
                    parse_mode=ParseMode.HTML
                )
        except Exception as e:
            logger.error("API Key Test Error: %s", e)
            await test_msg.edit_text(f"❌ <b>Test Error:</b> System encountered an issue while testing the key.")
        return

    # ── Thinking Phase: Immediate Feedback ──
    waiting_msg = await update.message.reply_text(
        "🧠 <i>BMO is thinking...</i>",
        parse_mode=ParseMode.HTML,
        reply_markup=CANCEL_KEYBOARD
    )

    # ── CRITICAL: Ensure Session and OpenCode ID are defined ──
    session = _ensure_session(chat_id, user.id, user.username)
    provider_id, model_id = _get_current_model(chat_id, session)
    
    opencode_sid = session.metadata.get("opencode_session_id")
    if not opencode_sid:
        logger.info("Initializing new OpenCode session for chat %s", chat_id)
        provider_env = None
        p_keys = storage.get_provider_keys(provider_id)
        if p_keys:
            provider_env = p_keys
            
        opencode_sid = await opencode_client.create_session(provider_id, model_id, env=provider_env)
        if opencode_sid:
            session.metadata["opencode_session_id"] = opencode_sid
            storage.save_session(session)
        else:
            await waiting_msg.edit_text("❌ Failed to initialize OpenCode session. Please try again.")
            return

    # Start Status Poller (now that we have opencode_sid)
    stop_event = asyncio.Event()
    poller_task = asyncio.create_task(
        _status_poller(
            context.bot,
            chat_id,
            waiting_msg.message_id,
            opencode_client,
            opencode_sid,
            stop_event,
        )
    )

    # ── Agent Creation Logic ──
    if chat_id in _agent_creation:
        creation = _agent_creation[chat_id]
        if creation["mode"] == "manual":
            if creation["step"] == "name":
                creation["name"] = text
                creation["step"] = "prompt"
                await update.message.reply_text(f"Great! Name: <b>{text}</b>\n\nNow, enter the **System Prompt** (the rules BMO must follow):", parse_mode=ParseMode.HTML)
            elif creation["step"] == "prompt":
                storage.save_custom_agent(chat_id, user.id, creation["name"], "Manual Agent", text)
                _agent_creation.pop(chat_id)
                await update.message.reply_text(f"✅ Agent <b>{creation['name']}</b> created and saved!", parse_mode=ParseMode.HTML, reply_markup=MAIN_MENU_KEYBOARD)
        else: # AI Mode
            if creation["step"] == "desc":
                wait_msg = await update.message.reply_text("🤖 Generating your custom agent persona... please wait.")
                gen_prompt = (
                    "Create a detailed AI system prompt based on this user description:\n"
                    f"Description: {text}\n\n"
                    "Output in JSON format only:\n"
                    "{\"name\": \"Short Name\", \"prompt\": \"Detailed system instructions\"}"
                )
                try:
                    # REUSE current session for speed and stability
                    res = await opencode_client.send_query(
                        query=gen_prompt, 
                        session_id=opencode_sid, 
                        active_mode="ask", 
                        chat_id=chat_id
                    )
                    import json, re
                    match = re.search(r'\{.*\}', res, re.DOTALL)
                    if match:
                        data = json.loads(match.group())
                        storage.save_custom_agent(chat_id, user.id, data["name"], text, data["prompt"])
                        _agent_creation.pop(chat_id)
                        await _send_safe(lambda t, pm=None: wait_msg.edit_text(t, parse_mode=pm), f"✅ AI Agent <b>{data['name']}</b> generated and saved!\n\nRules: {data['prompt'][:100]}...")
                    else:
                        await wait_msg.edit_text("❌ Failed to parse AI response. Please try describing it differently.")
                except Exception as e:
                    await wait_msg.edit_text(f"❌ Error generating agent: {e}")
        return

    # ── Onboarding Logic ──
    if chat_id in _onboarding_step:
        step = _onboarding_step[chat_id]
        
        # Smart Check: Is this a question or a correction?
        lower_text = text.lower()
        is_question = any(q in lower_text for q in ["شنو", "ماذا", "كيف", "تساعدني", "help", "who", "what", "?"])
        is_correction = any(c in lower_text for c in ["لا", "اسمي", "no", "name is"])

        if step == 1:
            if is_question and not is_correction:
                await update.message.reply_text(
                    "أنا BMO، مساعدك البرمجي الذكي! 🤖 أقدر أساعدك بكتابة الكود، شرح المفاهيم المعقدة، وحل المشاكل التقنية.\n\n"
                    "بس قبل ما نبدأ، حاب أعرف اسمك حتى أناديك بي؟ 😊",
                    parse_mode=ParseMode.HTML
                )
                return
            
            # If it's a correction or a normal name entry
            name = text
            if "اسمي" in text:
                name = text.split("اسمي")[-1].strip().replace("هو", "").strip()
            
            context.user_data["onboarding_name"] = name
            _onboarding_step[chat_id] = 2
            await update.message.reply_text(
                f"عاشت الأسامي يا <b>{name}</b>! ✨\n\n"
                "حتى أقدر أساعدك بشكل أفضل، شنو هي لغات البرمجة أو المواضيع التقنية اللي تهمك؟ "
                "(مثلاً: Python, React, AI, Cybersecurity...)",
                parse_mode=ParseMode.HTML
            )
        elif step == 2:
            if is_correction:
                # User is correcting their name from step 1
                new_name = text.split("اسمي")[-1].strip().replace("هو", "").strip()
                context.user_data["onboarding_name"] = new_name
                await update.message.reply_text(f"تمام، اعتذر! تم تعديل الاسم إلى <b>{new_name}</b>. 😊\n\nهسه كلي شنو اهتماماتك التقنية؟", parse_mode=ParseMode.HTML)
                return

            name = context.user_data.get("onboarding_name", user.first_name)
            prefs = {"name": name, "interests": text}
            from models.chat_models import UserMemory
            new_memory = UserMemory(
                user_id=user.id,
                chat_id=chat_id,
                preferences=prefs,
                knowledge={},
                created_at=time.time(),
                updated_at=time.time()
            )
            storage.save_memory(new_memory)
            _onboarding_step.pop(chat_id, None)
            
            await update.message.reply_text(
                f"تم الحفظ! ✅ شكراً الك يا <b>{name}</b>.\n\n"
                "هسه صار عندي فكرة عن تفضيلاتك وراح أحاول أركز عليها بإجاباتي.\n"
                "<b>BMO جاهز للانطلاق! 🚀</b>",
                parse_mode=ParseMode.HTML,
                reply_markup=MAIN_MENU_KEYBOARD
            )
        return

    storage.add_message(chat_id, "user", text, interface='telegram')

    # ── Handle title-waiting mode ──
    if chat_id in _awaiting_title:
        _awaiting_title.discard(chat_id)
        session.set_title(_generate_title(text))
        storage.save_session(session)
        await update.message.reply_text(
            f"✅ <b>Title set:</b> {session.title}",
            parse_mode=ParseMode.HTML,
        )
        return

    # ── Fast-path: casual greetings (skip LLM, respond instantly) ──
    lower_text = text.lower().strip()
    greeting_patterns = [
        "hi", "hey", "hello", "hola", "salut", "مرحبا", "هلو", "هاي",
        "how are you", "how r u", "how do you do", "how's it going",
        "how is it going", "whats up", "what's up", "sup", "yo",
        "good morning", "good evening", "good night", "good afternoon",
        "صباح الخير", "مساء الخير", "شلونك", "شلون", "شخبار", "اخبارك",
    ]
    is_greeting = any(g in lower_text for g in greeting_patterns)
    is_very_short = len(text.strip()) <= 15 and is_greeting
    
    if is_very_short:
        import random
        name = context.user_data.get("onboarding_name", user.first_name or "bro")
        responses = [
            f"Hey {name}! 👋 All good here, ready to code! What are we building today?",
            f"Hi {name}! 😊 Doing great, thanks for asking! Need help with anything?",
            f"Hey! 👋 I'm good and ready to roll. What's on your mind?",
            f"Hello {name}! ✨ All systems go — what can I help you with?",
            f"Hi there! 🤖 Running smooth. What's the mission today?",
        ]
        response = random.choice(responses)
        stop_event.set()
        try:
            await poller_task
        except Exception:
            pass
        await _send_chunks(update, context, waiting_msg, response)
        storage.add_message(chat_id, "assistant", response, interface='telegram')
        return

    # ── Thinking Phase ──
    # Load Context for Continuity (Last 5 messages only — more causes loop confusion)
    history_messages = session.get_context_text(max_messages=5)
    
    # Custom Persona injection
    active_agent = session.metadata.get("active_agent", "default")
    agent_info = None
    if active_agent.startswith("custom_"):
        try:
            agent_id = int(active_agent.split("_")[1])
            agent_info = storage.get_custom_agent_by_id(agent_id)
        except: pass

    query_with_context = f"Previous context for continuity:\n{history_messages}\n\nLatest User Message: {text}"
    if agent_info:
        query_with_context = f"[CUSTOM AGENT SYSTEM PROMPT: {agent_info['system_prompt']}]\n\n{query_with_context}"

    # Prepare request
    active_mode = session.metadata.get("active_mode", "execute")

    async def _process():
        try:
            response = await opencode_client.send_query(
                query=query_with_context,
                session_id=opencode_sid,
                provider_id=provider_id,
                model_id=model_id,
                active_mode=active_mode,
                active_agent=active_agent,
                chat_id=chat_id,
                session_uuid=session.session_id
            )
            # Update session ID if it was new
            if opencode_client.last_session_id:
                session.metadata["opencode_session_id"] = opencode_client.last_session_id
                storage.save_session(session)
            
            storage.add_message(chat_id, "assistant", response, interface='telegram')
            
            stop_event.set()
            await poller_task
            await _send_chunks(update, context, waiting_msg, response)
            # _schedule_auto_summary(chat_id, session, context.bot)
        except asyncio.CancelledError:
            stop_event.set()
            await poller_task
            logger.info("Task cancelled for chat %s", chat_id)
        except Exception as e:
            stop_event.set()
            await poller_task
            logger.error("Processing error: %s", e)
            await waiting_msg.edit_text(f"❌ Error: {str(e)}")

    task = asyncio.create_task(_process())
    _running_tasks[chat_id] = task


async def handle_agents(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Shows the Agent/Persona selection menu."""
    chat_id = update.effective_chat.id
    session = storage.load_session(chat_id)
    active_agent = session.metadata.get("active_agent", "default")
    
    text = (
        "🎭 <b>BMO Agents Factory</b>\n\n"
        "Welcome to the Agent system. You can choose a pre-defined persona, "
        "load one of your custom-made agents, or create a brand new one.\n\n"
        f"📍 <b>Active Agent:</b> <code>{active_agent}</code>"
    )
    
    kb = [
        [InlineKeyboardButton("🆕 Create Agent (Manual)", callback_data="agent_start_manual")],
        [InlineKeyboardButton("🤖 Create Agent (AI-Generated)", callback_data="agent_start_ai")],
        [InlineKeyboardButton("👤 My Custom Agents", callback_data="agent_list_custom")],
        [InlineKeyboardButton("📄 The Document Persona", callback_data="agent_set:document")],
        [InlineKeyboardButton("🦾 Reset to BMO Default", callback_data="agent_set:default")],
    ]
    
    await update.message.reply_text(
        text,
        reply_markup=InlineKeyboardMarkup(kb),
        parse_mode=ParseMode.HTML
    )

async def agent_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles agent selection and creation starts."""
    query = update.callback_query
    await query.answer()
    chat_id = query.message.chat_id
    data = query.data

    if data.startswith("agent_set:"):
        persona_id = data.split(":", 1)[1]
        session = storage.load_session(chat_id)
        session.metadata["active_agent"] = persona_id
        storage.save_session(session)
        await query.edit_message_text(f"✅ <b>Agent Switched:</b> {persona_id}", parse_mode=ParseMode.HTML)
        
    elif data == "agent_start_manual":
        _agent_creation[chat_id] = {"step": "name", "mode": "manual"}
        await query.edit_message_text("📝 <b>Manual Creation</b>\n\nPlease enter a **Name** for your new agent:", parse_mode=ParseMode.HTML)
        
    elif data == "agent_start_ai":
        _agent_creation[chat_id] = {"step": "desc", "mode": "ai"}
        await query.edit_message_text("🤖 <b>AI Creation</b>\n\nDescribe the personality or role you want BMO to take (e.g. 'A pirate coder' or 'A strict lawyer'):", parse_mode=ParseMode.HTML)

    elif data == "agent_list_custom":
        agents = storage.get_custom_agents(chat_id)
        if not agents:
            await query.edit_message_text("📭 You haven't created any custom agents yet.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back", callback_data="agent_back")]]))
            return
        kb = []
        for a in agents:
            kb.append([InlineKeyboardButton(f"👤 {a['name']}", callback_data=f"agent_set:custom_{a['agent_id']}")])
        kb.append([InlineKeyboardButton("⬅️ Back", callback_data="agent_back")])
        await query.edit_message_text("👤 <b>Your Custom Agents</b>\n\nSelect an agent to activate it:", reply_markup=InlineKeyboardMarkup(kb), parse_mode=ParseMode.HTML)

    elif data == "agent_back":
        # Simulate handle_agents update
        session = storage.load_session(chat_id)
        active_agent = session.metadata.get("active_agent", "default")
        kb = [
            [InlineKeyboardButton("🆕 Create Agent (Manual)", callback_data="agent_start_manual")],
            [InlineKeyboardButton("🤖 Create Agent (AI-Generated)", callback_data="agent_start_ai")],
            [InlineKeyboardButton("👤 My Custom Agents", callback_data="agent_list_custom")],
            [InlineKeyboardButton("📄 The Document Persona", callback_data="agent_set:document")],
            [InlineKeyboardButton("🦾 Reset to BMO Default", callback_data="agent_set:default")],
        ]
        await query.edit_message_text(f"🎭 <b>BMO Agents Factory</b>\n\nActive: {active_agent}", reply_markup=InlineKeyboardMarkup(kb), parse_mode=ParseMode.HTML)


async def handle_file(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handles documents and photos."""
    chat_id = update.effective_chat.id
    user = update.effective_user

    if not _is_authorized(user.id):
        return

    # Download file
    msg = update.message
    file_obj = None
    file_name = "image.jpg"
    mime_type = "image/jpeg"

    if msg.document:
        file_obj = await msg.document.get_file()
        file_name = msg.document.file_name
        mime_type = msg.document.mime_type
    elif msg.photo:
        file_obj = await msg.photo[-1].get_file()
    
    if not file_obj:
        return

    local_path = DOWNLOADS_DIR / file_name
    await file_obj.download_to_drive(local_path)

    waiting_msg = await update.message.reply_text(
        f"⏳ <i>Analysing {file_name}...</i>",
        parse_mode=ParseMode.HTML,
        reply_markup=CANCEL_KEYBOARD
    )

    session = _ensure_session(chat_id, user.id, user.username)
    caption = msg.caption or f"Please analyse this file: {file_name}"
    storage.add_message(chat_id, "user", f"[File: {file_name}] {caption}", interface='telegram')

    stop_event = asyncio.Event()
    poller_task = asyncio.create_task(
        _status_poller(
            context.bot,
            chat_id,
            waiting_msg.message_id,
            opencode_client,
            session.metadata.get("opencode_session_id"),
            stop_event,
        )
    )

    provider_id, model_id = _get_current_model(chat_id, session)
    active_mode = session.metadata.get("active_mode", "execute")
    provider_env = storage.get_provider_keys(provider_id) if provider_id else None

    async def _process_file():
        try:
            response = await opencode_client.send_query(
                caption,
                session_id=session.metadata.get("opencode_session_id"),
                files=[{"path": str(local_path), "mime": mime_type}],
                provider_id=provider_id,
                model_id=model_id,
                active_mode=active_mode,
                provider_env=provider_env,
                chat_id=chat_id
            )
            if opencode_client.last_session_id:
                session.metadata["opencode_session_id"] = opencode_client.last_session_id
                storage.save_session(session)
            
            storage.add_message(chat_id, "assistant", response, interface='telegram')

            stop_event.set()
            await poller_task
            await _send_chunks(update, context, waiting_msg, response)
            # _schedule_auto_summary(chat_id, session, context.bot)
        except asyncio.CancelledError:
            stop_event.set()
            await poller_task
        except Exception as e:
            stop_event.set()
            await poller_task
            await waiting_msg.edit_text(f"❌ Error: {str(e)}")

    task = asyncio.create_task(_process_file())
    _running_tasks[chat_id] = task


async def handle_system_reload(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Gracefully exits the process. start_BMO.bat will restart it."""
    # Send confirmation message
    await update.message.reply_text("☢️ <b>System Reload Initiated...</b>\nBot will be back online in 5 seconds.", parse_mode=ParseMode.HTML)
    logger.info("System reload requested by user %s", update.effective_user.id)
    
    # Send notification to user
    try:
        from telegram import Bot
        bot = Bot(token=os.getenv("TELEGRAM_TOKEN", ""))
        await bot.send_message(
            chat_id=732356803,
            text="🔄 <b>BMO is restarting with new buttons!</b>\n\nCheck your keyboard - new cleaner layout is ready!",
            parse_mode=ParseMode.HTML
        )
    except Exception as e:
        logger.error(f"Could not send notification: {e}")
    
    await asyncio.sleep(2)
    os._exit(0)


async def handle_session_summary(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Generates a summary and automatically updates the session title."""
    chat_id = update.effective_chat.id
    session = storage.load_session(chat_id)
    if not session or not session.messages:
        await update.message.reply_text("⚠️ No messages in this session to summarise.")
        return

    waiting_msg = await update.message.reply_text("📝 <i>Analysing history and re-titling session...</i>", parse_mode=ParseMode.HTML)
    
    try:
        provider_id, model_id = _get_current_model(chat_id, session)
        history_text = session.get_context_text(max_messages=50)
        
        # Smart Prompt for Summary + Title
        prompt = (
            f"Based on this history:\n{history_text}\n\n"
            "1. Provide a concise summary of the work done in bold Telegram HTML.\n"
            "2. Suggest a short 3-4 word title for this session.\n\n"
            "Format your response as:\nSUMMARY: [summary text]\nTITLE: [suggested title]"
        )

        response = await opencode_client.send_query(
            prompt,
            session_id=session.metadata.get("opencode_session_id"),
            provider_id=provider_id,
            model_id=model_id,
            active_mode="ask",
            chat_id=chat_id
        )
        
        # Parse response
        summary_text = "No summary available."
        new_title = "Untitled Session"
        
        if "SUMMARY:" in response and "TITLE:" in response:
            parts = response.split("TITLE:")
            summary_text = parts[0].replace("SUMMARY:", "").strip()
            new_title = parts[1].strip()
        else:
            summary_text = response
            
        # Update DB
        session.set_summary(summary_text)
        session.set_title(new_title)
        storage.save_session(session)
        
        await _send_safe(
            lambda t, pm=None: waiting_msg.edit_text(t, parse_mode=pm),
            f"📝 <b>Session Summary:</b>\n\n{summary_text}\n\n"
            f"🏷️ <b>New Title:</b> {new_title}"
        )
    except Exception as e:
        logger.error("Summary/Title error: %s", e)
        await waiting_msg.edit_text(f"❌ Failed to process summary: {str(e)}")


async def handle_list_sessions(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show all saved sessions and let user pick one."""
    chat_id = update.effective_chat.id
    sessions = storage.list_chat_sessions(chat_id)

    if not sessions:
        await update.message.reply_text("📋 <b>No saved sessions.</b>", parse_mode=ParseMode.HTML)
        return

    text = "📋 <b>Your Sessions</b> — tap to switch:\n"
    keyboard = []
    for s in sessions:
        title = s["title"] or s["session_id"][:8]
        label = f"{'✅ ' if s['is_active'] else ''}{title} ({s['msg_count']} msgs)"
        keyboard.append([InlineKeyboardButton(label, callback_data=f"session:{s['session_id']}")])

    await update.message.reply_text(
        text,
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode=ParseMode.HTML,
    )




