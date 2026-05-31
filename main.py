"""
Main entry point for the OpenCode Telegram Bot.
"""

import logging
import sys

from telegram import Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    MessageHandler,
    filters,
)

from config.settings import TELEGRAM_TOKEN, LOG_FORMAT, LOG_LEVEL
from handlers.messages import (
    start_command,
    menu_command,
    choose_model_callback,
    use_skill_callback,
    session_select_callback,
    handle_message,
    handle_file,
    mode_callback,
    cancel_callback,
    settings_callback,
    agent_callback,
    inline_action_callback,
)

logging.basicConfig(format=LOG_FORMAT, level=getattr(logging, LOG_LEVEL, logging.INFO))
logger = logging.getLogger(__name__)


def main():
    if not TELEGRAM_TOKEN:
        logger.error("TELEGRAM_TOKEN is not set. Create a .env file based on .env.example")
        sys.exit(1)

    # --- LOCK FILE MECHANISM: Prevent multiple instances ---
    import os, psutil
    lock_file = ".bot.lock"
    pid = os.getpid()
    
    if os.path.exists(lock_file):
        try:
            with open(lock_file, "r") as f:
                old_pid = int(f.read().strip())
            if psutil.pid_exists(old_pid):
                logger.warning(f"⚠️ Conflict detected: Another BMO (PID {old_pid}) is already running!")
                logger.warning("Terminating old instance to resolve conflict...")
                try:
                    p = psutil.Process(old_pid)
                    p.terminate()
                    p.wait(timeout=3)
                except Exception as e:
                    logger.error(f"Failed to kill old instance: {e}")
                    sys.exit(1)
        except Exception:
            pass # Stale or invalid lock
            
    with open(lock_file, "w") as f:
        f.write(str(pid))
    # --------------------------------------------------------

    logger.info("Starting OpenCode Telegram Bot (BMO)...")

    app = Application.builder().token(TELEGRAM_TOKEN).build()

    # ── Commands ──────────────────────────────────────────────────────────────
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("menu", menu_command))

    # ── Inline keyboard callbacks ─────────────────────────────────────────────
    app.add_handler(CallbackQueryHandler(choose_model_callback, pattern=r"^(m|mp):"))
    app.add_handler(CallbackQueryHandler(use_skill_callback,    pattern=r"^skill:"))
    app.add_handler(CallbackQueryHandler(cancel_callback,       pattern=r"^cancel$"))
    app.add_handler(CallbackQueryHandler(mode_callback,         pattern=r"^mode_set:"))
    app.add_handler(CallbackQueryHandler(session_select_callback, pattern=r"^session:"))
    app.add_handler(CallbackQueryHandler(settings_callback,       pattern=r"^(set_keys|admin_stats|back_settings|setup_p_.*|ttl_.*|perm_.*)$"))
    app.add_handler(CallbackQueryHandler(agent_callback,          pattern=r"^agent_"))
    app.add_handler(CallbackQueryHandler(inline_action_callback,   pattern=r"^(action:|back_to_menu|history_page:|session_load:)"))

    # ── Plain text messages & Documents & Photos ───────────────────────────────────────
    app.add_handler(MessageHandler(filters.Document.ALL | filters.PHOTO, handle_file))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    # ── Start MCP Server in Background ──
    import threading
    import uvicorn
    from tools.mcp_server import mcp

    def start_mcp():
        uvicorn.run(mcp.sse_app, host="127.0.0.1", port=4097, log_level="error")

    logger.info("Starting BMO MCP Server on port 4097...")
    threading.Thread(target=start_mcp, daemon=True).start()

    logger.info("BMO is online and polling...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("Bot stopped.")