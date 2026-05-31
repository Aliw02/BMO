"""
Hot-reload runner for OpenCode Telegram Bot.
Watches project files and automatically restarts main.py on changes.
"""

import os
import signal
import subprocess
import sys
from pathlib import Path

from watchfiles import run_process

BASE_DIR = Path(__file__).resolve().parent

# Directories to watch (exclude .git, __pycache__, data, logs)
WATCH_DIRS = [
    str(BASE_DIR / "config"),
    str(BASE_DIR / "handlers"),
    str(BASE_DIR / "core"),
    str(BASE_DIR / "models"),
    str(BASE_DIR / "storage"),
    str(BASE_DIR),
]

# File patterns to ignore
IGNORE_PATTERNS = [
    r"__pycache__",
    r"\.git",
    r"\.pyc",
    r"\.log",
    r"\.env$",
    r"\.bat$",
    r"sessions\.json",
    r"data[\\/]",
    r"logs[\\/]",
]


def start_bot():
    proc = subprocess.Popen(
        [sys.executable, "-u", "main.py"],
        cwd=BASE_DIR,
        stdout=sys.stdout,
        stderr=sys.stderr,
    )
    return proc





if __name__ == "__main__":
    print("BMO Hot-Reload Runner")
    print(f"Watching: {', '.join(WATCH_DIRS)}")
    print("Edit any file — the bot restarts automatically.")
    print("Press Ctrl+C to stop.\n")

    run_process(
        *WATCH_DIRS,
        target=start_bot,
        watch_filter=lambda change, path: not any(
            p in path for p in IGNORE_PATTERNS
        ),
    )

