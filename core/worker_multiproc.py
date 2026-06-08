import asyncio
import logging
import multiprocessing
import os
import signal
import sys
import time
from typing import Callable, Optional

from core.request_worker import worker_main
from core.worker_protocol import (
    MSG_RESULT, MSG_UPDATE, MSG_ERROR, MSG_CANCELLED,
)

logger = logging.getLogger(__name__)

HEARTBEAT_INTERVAL = 5.0
HEARTBEAT_TIMEOUT = 12.0
MAX_RESPAWN_DELAY = 30.0


class MultiprocessWorker:
    """Approach 2: Multiprocessing.Process + Queue worker.
    Uses loop.run_in_executor for non-blocking result reads.
    """

    def __init__(self):
        self.task_queue: multiprocessing.Queue = multiprocessing.Queue()
        self.result_queue: multiprocessing.Queue = multiprocessing.Queue()
        self.process: Optional[multiprocessing.Process] = None
        self._running = False
        self._spawn_count = 0
        self._closed = False

    async def start(self):
        if self._running:
            return
        self._running = True
        self._spawn_worker()

    def _spawn_worker(self):
        if self.process and self.process.is_alive():
            self._kill_worker()

        self._spawn_count += 1
        self.process = multiprocessing.Process(
            target=worker_main,
            args=(self.task_queue, self.result_queue),
            daemon=True,
            name=f"request-worker-{self._spawn_count}",
        )
        self.process.start()
        logger.info(f"Spawned multiproc worker (PID {self.process.pid}, spawn #{self._spawn_count})")

    def _kill_worker(self):
        if self.process and self.process.is_alive():
            try:
                if sys.platform == "win32":
                    self.process.terminate()
                else:
                    os.kill(self.process.pid, signal.SIGKILL)
                self.process.join(timeout=5)
            except Exception as e:
                logger.warning(f"Error killing worker: {e}")
            finally:
                self.process = None

    def _on_worker_dead(self):
        if self._closed:
            return
        logger.warning("Multiproc worker dead, respawning...")
        self._kill_worker()
        self._spawn_worker()

    async def send_query(
        self,
        session_id: str,
        payload: dict,
        base_url: str,
        poll_interval: float = 2.0,
        poll_timeout: float = 600.0,
        callback: Optional[Callable] = None,
    ) -> str:
        task = {
            "type": "query",
            "session_id": session_id,
            "payload": payload,
            "base_url": base_url,
            "poll_interval": poll_interval,
            "poll_timeout": poll_timeout,
        }
        self.task_queue.put(task)

        loop = asyncio.get_event_loop()
        full_text = ""
        deadline = time.monotonic() + poll_timeout

        while time.monotonic() < deadline:
            try:
                result = await loop.run_in_executor(
                    None, self.result_queue.get, True, 5.0
                )
            except Exception:
                if not self.process or not self.process.is_alive():
                    return "Error: Worker process died"
                continue

            if not isinstance(result, dict):
                continue

            msg_type = result.get("type")
            if msg_type == MSG_UPDATE:
                diff = result.get("content", "")
                full_text = result.get("full_text", full_text + diff)
                if callback and diff:
                    callback(full_text)
            elif msg_type == MSG_RESULT:
                content = result.get("content", "")
                if callback and content:
                    callback(content)
                return content
            elif msg_type == MSG_ERROR:
                return f"Error: {result.get('message', 'Unknown worker error')}"
            elif msg_type == MSG_CANCELLED:
                return "Error: Request was cancelled."

        return "Error: Request timed out"

    async def connect(self, base_url: str):
        self.task_queue.put({"type": "connect", "base_url": base_url})

    async def shutdown(self):
        self._closed = True
        self._running = False
        try:
            self.task_queue.put({"type": "shutdown"})
        except Exception:
            pass
        self._kill_worker()

    async def cancel_current(self):
        """Cancel the in-flight request by killing and respawning the multiproc worker.

        Mirrors AsyncSubprocessWorker.cancel_current() for the multiprocessing
        backend so the SIGINT handler in CLI works uniformly.
        """
        logger.info("Cancelling in-flight request by killing multiproc worker (PID %s)", self.process.pid if self.process else None)
        self._kill_worker()
        # Don't respawn immediately — let the next send_query call it via
        # _on_worker_dead pattern, or let BotClient ensure a fresh one.
        self._running = False

    @property
    def is_alive(self) -> bool:
        return self.process is not None and self.process.is_alive()
