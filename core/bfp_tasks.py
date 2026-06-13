"""Shared task registry for BFP delegation.

Both the WebSocket transport server and the A2A HTTP bridge use this
module to create, track, and process delegated tasks — no WebSocket
loopback required.
"""

import asyncio
import logging
import uuid
from datetime import datetime, timezone
from typing import Optional, Callable, Awaitable

logger = logging.getLogger(__name__)

_tasks: dict[str, dict] = {}
_task_futures: dict[str, asyncio.Future] = {}
_task_handler: Optional[Callable[[str, dict], Awaitable[str]]] = None


def set_handler(handler: Callable[[str, dict], Awaitable[str]]):
    """Register an async handler that processes incoming tasks.

    The handler receives (source_did, task_data) and should return
    the result string.
    """
    global _task_handler
    _task_handler = handler


async def create_task(task_data: dict, source_did: str) -> str:
    task_id = f"task-{uuid.uuid4().hex[:12]}"
    now = datetime.now(timezone.utc).isoformat()
    _tasks[task_id] = {
        "id": task_id,
        "source": source_did,
        "data": task_data,
        "status": "pending",
        "result": None,
        "error": None,
        "created_at": now,
        "updated_at": now,
        "completed_at": None,
    }
    logger.info("Task %s created by %s", task_id, source_did)

    future = asyncio.get_event_loop().create_future()
    _task_futures[task_id] = future
    asyncio.create_task(_process_task(task_id))

    return task_id


def get_task(task_id: str) -> Optional[dict]:
    return _tasks.get(task_id)


def list_tasks() -> list[dict]:
    return sorted(_tasks.values(), key=lambda t: t["created_at"], reverse=True)


def update_task(task_id: str, status: str, result: str = None, error: str = None):
    task = _tasks.get(task_id)
    if task is None:
        return
    task["status"] = status
    task["updated_at"] = datetime.now(timezone.utc).isoformat()
    if result is not None:
        task["result"] = result
    if error is not None:
        task["error"] = error
    if status in ("completed", "failed", "cancelled"):
        task["completed_at"] = task["updated_at"]


async def _process_task(task_id: str) -> None:
    task = _tasks.get(task_id)
    if task is None or task["status"] != "pending":
        return
    try:
        update_task(task_id, "processing")
        if _task_handler:
            result = await _task_handler(task["source"], task["data"])
            update_task(task_id, "completed", result=result)
        else:
            update_task(task_id, "completed", result="No handler registered for task processing")
    except Exception as e:
        logger.exception("Task %s failed", task_id)
        update_task(task_id, "failed", error=str(e))
    finally:
        future = _task_futures.pop(task_id, None)
        if future and not future.done():
            future.set_result(_tasks[task_id])


async def wait_for_task(task_id: str, timeout: float = 120.0) -> Optional[dict]:
    """Wait for a task to reach a terminal state (completed/failed/cancelled)."""
    future = _task_futures.get(task_id)
    if future is None:
        return get_task(task_id)
    try:
        return await asyncio.wait_for(future, timeout=timeout)
    except asyncio.TimeoutError:
        logger.warning("Task %s timed out after %ss", task_id, timeout)
        update_task(task_id, "failed", error=f"Timed out after {timeout}s")
        _task_futures.pop(task_id, None)
        return get_task(task_id)
