"""
Configuration settings for OpenCode Telegram Bot
"""

import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
_local_env = BASE_DIR / ".env"

# ── Data home resolution ────────────────────────────────────────────────────
# Priority:
#   1. BMO_HOME env var (explicit override)
#   2. Local dev mode: if a .env exists next to the project, use local data/
#   3. Production / npm install: ~/.bmo/
_env_home = os.getenv("BMO_HOME")
if _env_home:
    BMO_HOME = Path(_env_home)
elif _local_env.exists():
    # Dev mode — keep using the project-local layout
    BMO_HOME = BASE_DIR
else:
    # Global install mode: ~/.bmo/
    BMO_HOME = Path.home() / ".bmo"

DATA_DIR = BMO_HOME / "data"
LOGS_DIR = BMO_HOME / "logs"

# ── Load .env ─────────────────────────────────────────────────────────────────
# Always load BMO_HOME/.env first (covers both dev and global install).
# Also try BASE_DIR/.env as a fallback so local dev still works when
# BMO_HOME happens to equal BASE_DIR.
_bmo_home_env = BMO_HOME / ".env"
if _bmo_home_env.exists():
    load_dotenv(_bmo_home_env)
elif _local_env.exists():
    load_dotenv(_local_env)

# ── Ensure directories exist ──────────────────────────────────────────────────
DATA_DIR.mkdir(parents=True, exist_ok=True)
LOGS_DIR.mkdir(parents=True, exist_ok=True)

# ── Telegram Configuration ────────────────────────────────────────────────────
# Support both names; prioritize .env values
TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN") or os.getenv("TELEGRAM_TOKEN")
TELEGRAM_API_TIMEOUT = 30

# ── OpenCode Server Configuration ─────────────────────────────────────────────
# Prioritize full URL if provided, otherwise build from host/port
OPENCODE_SERVER_URL = os.getenv("OPENCODE_SERVER_URL")
OPENCODE_HOST = os.getenv("OPENCODE_HOST", "127.0.0.1")
OPENCODE_PORT = os.getenv("OPENCODE_PORT", "4800")

if OPENCODE_SERVER_URL:
    OPENCODE_BASE_URL = OPENCODE_SERVER_URL.rstrip("/")
else:
    OPENCODE_BASE_URL = f"http://{OPENCODE_HOST}:{OPENCODE_PORT}"

OPENCODE_SESSION_ENDPOINT = f"{OPENCODE_BASE_URL}/session"
OPENCODE_TIMEOUT = 900
OPENCODE_POLL_INTERVAL = 2.0
OPENCODE_POLL_TIMEOUT = 900

_raw_allowed = os.getenv("ALLOWED_USER_IDS", "")
ALLOWED_USER_IDS = set()
if _raw_allowed:
    for uid in _raw_allowed.split(","):
        uid = uid.strip()
        if uid:
            try:
                ALLOWED_USER_IDS.add(int(uid))
            except ValueError:
                import sys
                print(f"Warning: Invalid user ID '{uid}' in ALLOWED_USER_IDS. It must be a numeric ID. Skipping.", file=sys.stderr)

try:
    OWNER_ID = int(os.getenv("OWNER_ID", "0"))
except ValueError:
    import sys
    print("Warning: Invalid OWNER_ID in environment. Defaulting to 0.", file=sys.stderr)
    OWNER_ID = 0

STORAGE_TYPE = os.getenv("STORAGE_TYPE", "sqlite")
CHAT_HISTORY_FILE = DATA_DIR / "chat_history.json"
SESSIONS_FILE = DATA_DIR / "sessions.jsonl"
USER_MEMORY_FILE = DATA_DIR / "user_memory.json"
ACTIVE_SESSIONS_FILE = DATA_DIR / "active_sessions.json"
DATABASE_FILE = DATA_DIR / "bot.db"
MEMORY_FILE = DATA_DIR / "memory.md"

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

# ── BFP (BMO Friendship Protocol) ─────────────────────────────────────────────
BFP_RELAY_URL = os.getenv("BFP_RELAY_URL") or None
BFP_REGISTRY_URL = os.getenv(
    "BFP_REGISTRY_URL", "https://bfp-registry.aliwey.workers.dev"
)
BFP_TRANSPORT_PORT = int(os.getenv("BFP_TRANSPORT_PORT", "8765"))
BFP_A2A_PORT = int(os.getenv("BFP_A2A_PORT", "8766"))