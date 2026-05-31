import asyncio
import logging
import os
import re
import subprocess
import time
from datetime import datetime
from mcp.server.fastmcp import FastMCP
from storage.sqlite_storage import SQLiteStorage
from core.shared_state import pending_permissions
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Bot
from dotenv import load_dotenv
from tools.task_registry import (
    register_task, update_task, remove_task, get_task,
    get_active_tasks, check_port_conflict, format_task_list,
    PROJECT_ROOT as REGISTRY_PROJECT_ROOT,
)

load_dotenv()

logger = logging.getLogger(__name__)

# Initialize FastMCP
mcp = FastMCP("BMO-Tools")
storage = SQLiteStorage()
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
LOGS_DIR = os.path.join(PROJECT_ROOT, "logs")

os.makedirs(LOGS_DIR, exist_ok=True)


def _start_detached(command: str, log_file: str, cwd: str = None) -> subprocess.Popen:
    """Start a process fully detached with output redirected to a log file."""
    CREATE_NO_WINDOW = 0x08000000
    DETACHED_PROCESS = 0x00000008

    os.makedirs(os.path.dirname(log_file), exist_ok=True)

    log_fh = open(log_file, "w", encoding="utf-8")

    proc = subprocess.Popen(
        command,
        shell=True,
        cwd=cwd or PROJECT_ROOT,
        creationflags=CREATE_NO_WINDOW | DETACHED_PROCESS,
        stdout=log_fh,
        stderr=log_fh,
    )
    return proc


def _poll_log_for_url(log_file: str, timeout: int = 30) -> str | None:
    """Poll a log file for a cloudflared URL. Returns URL or None."""
    start = time.time()
    while time.time() - start < timeout:
        time.sleep(2)
        if os.path.exists(log_file):
            try:
                with open(log_file, "r", encoding="utf-8", errors="ignore") as f:
                    content = f.read()
                    match = re.search(r"https://[a-zA-Z0-9-]+\.trycloudflare\.com", content)
                    if match:
                        return match.group(0)
            except IOError:
                pass
    return None


async def _poll_log_for_url_async(log_file: str, timeout: int = 75) -> str | None:
    """Async version — uses asyncio.sleep instead of time.sleep. Returns URL or None."""
    start = time.time()
    while time.time() - start < timeout:
        await asyncio.sleep(2)
        if os.path.exists(log_file):
            try:
                with open(log_file, "r", encoding="utf-8", errors="ignore") as f:
                    content = f.read()
                    match = re.search(r"https://[a-zA-Z0-9-]+\.trycloudflare\.com", content)
                    if match:
                        return match.group(0)
            except IOError:
                pass
    return None


@mcp.tool()
async def request_permission(chat_id: int, reason: str, scope: str) -> str:
    """
    Requests permission from the user for a sensitive action.
    BMO should call this before reading/writing files outside the project or performing destructive actions.
    """
    # 1. Check permanent permissions
    existing = storage.check_permission(chat_id, scope)
    if existing == 1:
        logger.info(f"Permission auto-granted for {chat_id} on {scope}")
        return "granted"
    if existing == 0:
        logger.info(f"Permission auto-denied for {chat_id} on {scope}")
        return "denied"

    # 2. Need human intervention
    if not BOT_TOKEN:
        return "denied (bot token missing in server env)"

    bot = Bot(token=BOT_TOKEN)
    event = asyncio.Event()
    key = (chat_id, scope)
    pending_permissions[key] = {"event": event, "result": None}

    # Use | as separator to avoid issues with paths containing :
    kb = [
        [
            InlineKeyboardButton("✅ Yes", callback_data=f"perm_yes|{scope}"),
            InlineKeyboardButton("❌ No", callback_data=f"perm_no|{scope}")
        ],
        [InlineKeyboardButton("🔒 Yes, Always", callback_data=f"perm_always|{scope}")]
    ]

    try:
        await bot.send_message(
            chat_id=chat_id,
            text=f"⚠️ <b>BMO SECURITY REQUEST</b>\n\n"
                 f"📋 <b>Reason:</b> {reason}\n"
                 f"📁 <b>Scope:</b> <code>{scope}</code>\n\n"
                 "Do you grant permission for this action?",
            reply_markup=InlineKeyboardMarkup(kb),
            parse_mode="HTML"
        )

        # 3. Wait for user input
        logger.info(f"Waiting for permission from {chat_id} for {scope}...")
        await asyncio.wait_for(event.wait(), timeout=300) # 5 min timeout
        result = pending_permissions[key]["result"]
        return result
    except asyncio.TimeoutError:
        return "denied (timeout)"
    except Exception as e:
        logger.error(f"Error in request_permission: {e}")
        return f"denied (error: {str(e)})"
    finally:
        pending_permissions.pop(key, None)


@mcp.tool()
async def get_session_summaries(chat_id: int, count: int = 3) -> list:
    """
    Fetches the last N session summaries for context.
    Use this to understand what happened in previous conversations or to link topics.
    """
    try:
        sessions = storage.list_sessions(chat_id)
        summaries = []
        for s in sessions:
            if s.get("summary"):
                summaries.append({
                    "title": s["title"],
                    "summary": s["summary"],
                    "date": s["updated_at"]
                })
        return summaries[:count]
    except Exception as e:
        logger.error(f"Error in get_session_summaries: {e}")
        return f"Error: {str(e)}"


@mcp.tool()
async def send_telegram_message(chat_id: int, message: str, parse_mode: str = "HTML") -> str:
    """
    Sends a message to a specific Telegram chat.

    Args:
        chat_id: The target user's Telegram chat ID
        message: The message text to send
        parse_mode: HTML or Markdown (default: HTML)
    """
    if not BOT_TOKEN:
        return "Error: Bot token not configured"

    try:
        bot = Bot(token=BOT_TOKEN)
        await bot.send_message(
            chat_id=chat_id,
            text=message,
            parse_mode=parse_mode
        )
        return f"✅ Message sent to {chat_id}"
    except Exception as e:
        logger.error(f"Error sending message: {e}")


@mcp.tool()
async def start_web_task(
    command: str,
    port: int = 0,
    task_type: str = "server",
    description: str = "",
    log_filename: str = ""
) -> str:
    """
    Starts a web-related task (server, tunnel, static file server) fully detached.
    Returns immediately with PID. Use check_task_status() to poll for URL.

    Args:
        command: The command to run (e.g., "node server.js", "python -m http.server 8080")
        port: The port the service will listen on (for tracking and conflict detection)
        task_type: Type of task — "server", "tunnel", "webchat", "static"
        description: Human-readable description of what this task does
        log_filename: Custom log filename (default: auto-generated based on port/type)
    """
    try:
        # Check for port conflicts
        if port:
            conflict = check_port_conflict(port)
            if conflict:
                return (
                    f"⚠️ Port {port} is already in use by:\n"
                    f"  Type: {conflict['type']}\n"
                    f"  PID: {conflict['pid']}\n"
                    f"  Command: {conflict['command']}\n"
                    f"  Description: {conflict.get('description', 'N/A')}\n\n"
                    f"Stop the existing task first with stop_background_task({conflict['pid']})."
                )

        # Generate log filename if not provided
        if not log_filename:
            if port:
                log_filename = f"task_{task_type}_{port}.log"
            else:
                ts = datetime.now().strftime("%Y%m%d_%H%M%S")
                log_filename = f"task_{task_type}_{ts}.log"

        log_file = os.path.join(LOGS_DIR, log_filename)

        # Start the process detached
        proc = _start_detached(command, log_file)

        # Register in the task registry
        register_task(
            pid=proc.pid,
            command=command,
            port=port if port else None,
            task_type=task_type,
            description=description,
            log_file=log_file,
        )

        return (
            f"🚀 Task started successfully!\n"
            f"PID: {proc.pid}\n"
            f"Type: {task_type}\n"
            f"Port: {port if port else 'N/A'}\n"
            f"Log: {log_filename}\n"
            f"Description: {description or 'N/A'}\n\n"
            f"Use check_task_status(pid={proc.pid}) to poll for URL (wait 10-15s first).\n"
            f"Use list_background_tasks() to see all active tasks."
        )
    except Exception as e:
        logger.error(f"Error in start_web_task: {e}")
        return f"Error: {str(e)}"


@mcp.tool()
async def list_background_tasks() -> str:
    """
    Lists all active background tasks with their status, ports, and URLs.
    Use this to see what's running, find PIDs to stop, or check for port conflicts.
    """
    try:
        return format_task_list()
    except Exception as e:
        logger.error(f"Error in list_background_tasks: {e}")
        return f"Error: {str(e)}"


@mcp.tool()
async def check_task_status(pid: int, timeout: int = 30) -> str:
    """
    Checks the status of a background task by polling its log file.
    For cloudflared tunnels, this extracts the public URL.

    Args:
        pid: The Process ID of the task to check
        timeout: Maximum seconds to poll for URL (default: 30)
    """
    try:
        task = get_task(pid)
        if not task:
            return f"❌ Task with PID {pid} not found in registry."

        if task.get("status") != "running":
            return (
                f"🔴 Task {pid} is not running.\n"
                f"Status: {task.get('status')}\n"
                f"Type: {task.get('type')}\n"
                f"Command: {task.get('command')}\n"
                f"Stopped at: {task.get('stopped_at', 'Unknown')}"
            )

        # Check if process is still alive
        import psutil
        try:
            proc = psutil.Process(pid)
            if not proc.is_running() or proc.status() == psutil.STATUS_ZOMBIE:
                update_task(pid, status="stopped", stop_reason="process_terminated",
                           stopped_at=datetime.now().isoformat())
                return f"🔴 Task {pid} has terminated unexpectedly."
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            update_task(pid, status="stopped", stop_reason="process_not_found",
                       stopped_at=datetime.now().isoformat())
            return f"🔴 Task {pid} not found in system processes."

        # Poll log file for URL
        log_file = task.get("log_file", "")
        url = task.get("url", "")

        if not url and log_file:
            url = _poll_log_for_url(log_file, timeout=timeout)
            if url:
                update_task(pid, url=url)

        # Build status response
        lines = [f"🟢 Task {pid} is running:"]
        lines.append(f"  Type: {task.get('type')}")
        lines.append(f"  Port: {task.get('port', 'N/A')}")
        lines.append(f"  Command: {task.get('command')}")
        if task.get("description"):
            lines.append(f"  Description: {task['description']}")
        if url:
            lines.append(f"  URL: {url}")
        else:
            lines.append(f"  URL: Not yet available (log: {os.path.basename(log_file)})")
        lines.append(f"  Started: {task.get('start_time', 'Unknown')}")

        return "\n".join(lines)
    except Exception as e:
        logger.error(f"Error in check_task_status: {e}")
        return f"Error: {str(e)}"


@mcp.tool()
async def stop_background_task(pid: int) -> str:
    """
    Stops a background task and removes it from the registry.
    Kills the entire process tree to prevent orphaned child processes.

    Args:
        pid: The Process ID of the task to stop
    """
    try:
        import psutil

        task = get_task(pid)
        if not task:
            return f"❌ Task with PID {pid} not found in registry."

        if task.get("status") != "running":
            return f"ℹ️ Task {pid} is already stopped (status: {task.get('status')})."

        # Kill the process tree
        try:
            parent = psutil.Process(pid)
            children = parent.children(recursive=True)
            for child in children:
                try:
                    child.terminate()
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    pass
            parent.terminate()

            # Wait briefly, then force kill if still alive
            gone, alive = psutil.wait_procs(children + [parent], timeout=3)
            for p in alive:
                try:
                    p.kill()
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    pass
        except psutil.NoSuchProcess:
            pass  # Process already dead

        # Update registry
        update_task(pid, status="stopped", stopped_at=datetime.now().isoformat(),
                   stop_reason="user_requested")

        # Clean up log file reference
        log_file = task.get("log_file", "")

        return (
            f"✅ Task {pid} stopped successfully.\n"
            f"Type: {task.get('type')}\n"
            f"Command: {task.get('command')}\n"
            f"Port: {task.get('port', 'N/A')} freed.\n"
            f"Log preserved at: {log_file or 'N/A'}"
        )
    except Exception as e:
        logger.error(f"Error stopping task {pid}: {e}")
        return f"Error: {str(e)}"


@mcp.tool()
async def tunnel_webchat(port: int = 3456) -> str:
    """
    Starts a Cloudflare tunnel for a local web service.
    Returns immediately with PID. Use check_task_status() after 15-30s to get the URL.

    Args:
        port: The local port to tunnel (default: 3456 for webchat)
    """
    try:
        # Check for port conflicts
        conflict = check_port_conflict(port)
        if conflict:
            return (
                f"⚠️ Port {port} already has an active tunnel:\n"
                f"  PID: {conflict['pid']}\n"
                f"  URL: {conflict.get('url', 'Not yet available')}\n\n"
                f"Use list_background_tasks() to see all active tunnels."
            )

        # Check if cloudflared is available
        try:
            subprocess.run(
                ["cloudflared", "--version"],
                capture_output=True, timeout=5, check=True,
                creationflags=subprocess.CREATE_NO_WINDOW
            )
        except (FileNotFoundError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
            return (
                "❌ cloudflared is not installed or not in PATH.\n"
                "Install it: https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/downloads/"
            )

        log_filename = f"tunnel_{port}.log"
        log_file = os.path.join(LOGS_DIR, log_filename)
        command = f"cloudflared tunnel --url http://localhost:{port}"

        # Start detached
        proc = _start_detached(command, log_file)

        # Register in registry
        register_task(
            pid=proc.pid,
            command=command,
            port=port,
            task_type="tunnel",
            description=f"Tunnel for localhost:{port}",
            log_file=log_file,
        )

        return (
            f"🌐 Tunnel starting for port {port}!\n"
            f"PID: {proc.pid}\n"
            f"Log: {log_filename}\n\n"
            f"⏳ Wait 15-30 seconds, then call:\n"
            f"  check_task_status(pid={proc.pid})\n\n"
            f"Or use list_background_tasks() to see all active tunnels."
        )
    except Exception as e:
        logger.error(f"Error in tunnel_webchat: {e}")
        return f"Error: {str(e)}"


@mcp.tool()
async def deploy_local_to_web(directory_path: str, port: int = 8080) -> str:
    """
    Deploys a local directory to the public web using a Cloudflare tunnel.
    Starts an HTTP server + tunnel, registers both in the task registry.
    Returns immediately. Use check_task_status() to get the URL.

    Args:
        directory_path: Path to the directory to serve
        port: Port for the HTTP server (default: 8080)
    """
    try:
        # Ensure directory_path is absolute
        if not os.path.isabs(directory_path):
            directory_path = os.path.abspath(os.path.join(PROJECT_ROOT, directory_path))

        if not os.path.isdir(directory_path):
            return f"❌ Directory not found: {directory_path}"

        # Check for port conflicts
        conflict = check_port_conflict(port)
        if conflict:
            return (
                f"⚠️ Port {port} is already in use by:\n"
                f"  PID: {conflict['pid']}\n"
                f"  Type: {conflict['type']}\n\n"
                f"Stop it first with stop_background_task({conflict['pid']})."
            )

        # 1. Start HTTP server
        server_log = os.path.join(LOGS_DIR, f"server_{port}.log")
        server_cmd = f"python -m http.server {port} --directory \"{directory_path}\""
        server_proc = _start_detached(server_cmd, server_log)

        register_task(
            pid=server_proc.pid,
            command=server_cmd,
            port=port,
            task_type="static",
            description=f"Static server for {directory_path}",
            log_file=server_log,
        )

        # 2. Start cloudflared tunnel
        tunnel_log = os.path.join(LOGS_DIR, f"tunnel_deploy_{port}.log")
        tunnel_cmd = f"cloudflared tunnel --url http://localhost:{port}"
        tunnel_proc = _start_detached(tunnel_cmd, tunnel_log)

        register_task(
            pid=tunnel_proc.pid,
            command=tunnel_cmd,
            port=port,
            task_type="tunnel",
            description=f"Tunnel for static server on port {port}",
            log_file=tunnel_log,
        )

        return (
            f"🚀 Deployment started!\n"
            f"Server PID: {server_proc.pid} (port {port})\n"
            f"Tunnel PID: {tunnel_proc.pid}\n"
            f"Directory: {directory_path}\n\n"
            f"⏳ Wait 15-30 seconds, then call:\n"
            f"  check_task_status(pid={tunnel_proc.pid})\n\n"
            f"Use list_background_tasks() to see all active tasks."
        )
    except Exception as e:
        logger.error(f"Error in deploy_local_to_web: {e}")
        return f"Error: {str(e)}"
