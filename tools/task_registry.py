"""
Background Task Registry — tracks all running background tasks (servers, tunnels, etc.)
Auto-cleans dead PIDs on read. Thread-safe with file locking.
"""

import json
import os
import time
import psutil
import threading
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
REGISTRY_PATH = os.path.join(PROJECT_ROOT, "data", "background_tasks.json")
_lock = threading.Lock()


def _ensure_registry():
    """Create registry file if it doesn't exist."""
    if not os.path.exists(REGISTRY_PATH):
        os.makedirs(os.path.dirname(REGISTRY_PATH), exist_ok=True)
        with open(REGISTRY_PATH, "w", encoding="utf-8") as f:
            json.dump({"tasks": []}, f, indent=2)


def _is_process_alive(pid: int) -> bool:
    """Check if a process with the given PID is still running."""
    try:
        proc = psutil.Process(pid)
        return proc.is_running() and proc.status() != psutil.STATUS_ZOMBIE
    except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
        return False


def _cleanup_dead_tasks(tasks: list) -> list:
    """Mark dead PIDs as stopped. Returns cleaned list."""
    cleaned = []
    for task in tasks:
        if task.get("status") == "running" and not _is_process_alive(task["pid"]):
            task["status"] = "stopped"
            task["stopped_at"] = datetime.now().isoformat()
            task["stop_reason"] = "process_terminated"
        cleaned.append(task)
    return cleaned


def read_registry() -> dict:
    """Read the task registry, auto-cleaning dead PIDs."""
    with _lock:
        _ensure_registry()
        try:
            with open(REGISTRY_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (json.JSONDecodeError, IOError):
            data = {"tasks": []}

        data["tasks"] = _cleanup_dead_tasks(data.get("tasks", []))
        _save_unlocked(data)
        return data


def _save_unlocked(data: dict):
    """Save registry without acquiring lock (caller must hold lock)."""
    with open(REGISTRY_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def write_registry(data: dict):
    """Write the task registry (thread-safe)."""
    with _lock:
        _ensure_registry()
        data["tasks"] = _cleanup_dead_tasks(data.get("tasks", []))
        _save_unlocked(data)


def register_task(pid: int, command: str, port: int = None, task_type: str = "server",
                  description: str = "", log_file: str = "") -> dict:
    """Register a new background task."""
    data = read_registry()
    task = {
        "pid": pid,
        "command": command,
        "port": port,
        "type": task_type,
        "status": "running",
        "url": "",
        "log_file": log_file,
        "start_time": datetime.now().isoformat(),
        "description": description,
    }
    data["tasks"].append(task)
    write_registry(data)
    return task


def update_task(pid: int, **kwargs) -> bool:
    """Update fields of an existing task by PID."""
    data = read_registry()
    for task in data["tasks"]:
        if task["pid"] == pid:
            task.update(kwargs)
            write_registry(data)
            return True
    return False


def remove_task(pid: int) -> bool:
    """Remove a task from the registry."""
    data = read_registry()
    original_len = len(data["tasks"])
    data["tasks"] = [t for t in data["tasks"] if t["pid"] != pid]
    if len(data["tasks"]) < original_len:
        write_registry(data)
        return True
    return False


def get_task(pid: int) -> dict | None:
    """Get a single task by PID."""
    data = read_registry()
    for task in data["tasks"]:
        if task["pid"] == pid:
            return task
    return None


def get_active_tasks() -> list:
    """Get all running tasks (auto-cleans dead ones)."""
    data = read_registry()
    return [t for t in data["tasks"] if t.get("status") == "running"]


def get_tasks_by_port(port: int) -> list:
    """Get all tasks using a specific port."""
    data = read_registry()
    return [t for t in data["tasks"] if t.get("port") == port]


def get_used_ports() -> list:
    """Get all ports currently in use by running tasks."""
    data = read_registry()
    return [t["port"] for t in data["tasks"] if t.get("status") == "running" and t.get("port")]


def check_port_conflict(port: int) -> dict | None:
    """Check if a port is already in use. Returns conflicting task or None."""
    tasks = get_tasks_by_port(port)
    for t in tasks:
        if t.get("status") == "running":
            return t
    return None


def format_task_list(tasks: list = None) -> str:
    """Format task list for display to the user."""
    if tasks is None:
        tasks = get_active_tasks()

    if not tasks:
        return "No active background tasks."

    lines = ["📋 Active Background Tasks:", ""]
    for i, task in enumerate(tasks, 1):
        status_icon = "🟢" if task.get("status") == "running" else "🔴"
        type_icon = {
            "tunnel": "🌐",
            "server": "🖥️",
            "webchat": "💬",
            "static": "📁",
        }.get(task.get("type", "server"), "⚙️")

        url_str = f" → {task['url']}" if task.get("url") else ""
        desc_str = f" — {task['description']}" if task.get("description") else ""

        lines.append(
            f"{i}. {status_icon} {type_icon} [{task['type']}] "
            f"PID: {task['pid']}, Port: {task.get('port', 'N/A')}"
            f"{url_str}{desc_str}"
        )

    lines.append("")
    lines.append(f"Total: {len(tasks)} active task(s)")
    return "\n".join(lines)
