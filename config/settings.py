"""
Configuration settings for OpenCode Telegram Bot
"""

import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
LOGS_DIR = BASE_DIR / "logs"

load_dotenv(BASE_DIR / ".env")

DATA_DIR.mkdir(exist_ok=True)
LOGS_DIR.mkdir(exist_ok=True)

# --- Telegram Configuration ---
# Support both names and prioritize .env values
TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN") or os.getenv("TELEGRAM_TOKEN")
TELEGRAM_API_TIMEOUT = 30

# --- OpenCode Server Configuration ---
# Prioritize full URL if provided, otherwise build from host/port
OPENCODE_SERVER_URL = os.getenv("OPENCODE_SERVER_URL")
OPENCODE_HOST = os.getenv("OPENCODE_HOST", "127.0.0.1")
OPENCODE_PORT = os.getenv("OPENCODE_PORT", "4096")

if OPENCODE_SERVER_URL:
    OPENCODE_BASE_URL = OPENCODE_SERVER_URL.rstrip("/")
else:
    OPENCODE_BASE_URL = f"http://{OPENCODE_HOST}:{OPENCODE_PORT}"

OPENCODE_SESSION_ENDPOINT = f"{OPENCODE_BASE_URL}/session"
OPENCODE_TIMEOUT = 1200
OPENCODE_POLL_INTERVAL = 2.0
OPENCODE_POLL_TIMEOUT = 3600

_raw_allowed = os.getenv("ALLOWED_USER_IDS", "")
ALLOWED_USER_IDS = (
    set(int(uid.strip()) for uid in _raw_allowed.split(",") if uid.strip())
    if _raw_allowed
    else set()
)

STORAGE_TYPE = os.getenv("STORAGE_TYPE", "sqlite")
CHAT_HISTORY_FILE = DATA_DIR / "chat_history.json"
SESSIONS_FILE = DATA_DIR / "sessions.jsonl"
USER_MEMORY_FILE = DATA_DIR / "user_memory.json"
ACTIVE_SESSIONS_FILE = DATA_DIR / "active_sessions.json"
DATABASE_FILE = DATA_DIR / "bot.db"

MAX_HISTORY_PER_CHAT = 500
MAX_CONTEXT_LENGTH = 32000
CHUNK_SIZE = 4096

LOG_LEVEL = "INFO"
LOG_FORMAT = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
LOG_FILE = LOGS_DIR / "bot.log"

ENABLE_MEMORY = True
ENABLE_HISTORY = True
ENABLE_CONTEXT = True
MAX_RETRIES = 3
RETRY_DELAY = 2

DEBUG = os.getenv("DEBUG", "False").lower() == "true"