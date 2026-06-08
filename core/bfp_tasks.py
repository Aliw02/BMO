"""Shared task registry for BFP delegation.

Both the WebSocket transport server and the A2A HTTP bridge use this
module to create, track, and process delegated tasks — no WebSocket
loopback required.
"""

import logging
import uuid
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger(__name__)

_tasks: dict[str, dict] = {}


def create_task(task_data: dict, source_did: str) -> str:
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
    process_task(task_id)
    return task_id


def get_task(task_id: str) -> Optional[dict]:
    return _tasks.get(task_id)


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


def process_task(task_id: str) -> None:
    """Process a task — currently auto-completes with placeholder.

    In the future this will route through BMO's engine or dispatch to
    worker processes for real execution.
    """
    task = _tasks.get(task_id)
    if task is None or task["status"] != "pending":
        return
    task["status"] = "processing"
    task["updated_at"] = datetime.now(timezone.utc).isoformat()
    task["status"] = "completed"
    task["result"] = f"Delegated task processed. Payload: {task['data']}"
    task["completed_at"] = datetime.now(timezone.utc).isoformat()
    task["updated_at"] = task["completed_at"]
    logger.info("Task %s completed", task_id)
