import asyncio
import json
import logging
import os
import sys
import time
from typing import Callable, Optional

from core.worker_protocol import (
    encode_msg, decode_msg, MSG_QUERY, MSG_RESULT, MSG_UPDATE,
    MSG_ERROR, MSG_CANCELLED, MSG_CONNECT, MSG_SHUTDOWN,
)

logger = logging.getLogger(__name__)

WORKER_SCRIPT = os.path.join(os.path.dirname(__file__), "request_worker.py")


class AsyncSubprocessWorker:
    """Approach 1: Spawn worker via asyncio.create_subprocess_exec.
    Communicates via JSON-RPC over stdin/stdout.
    """

    def __init__(self):
        self._process: Optional[asyncio.subprocess.Process] = None
        self._msg_id = 0
        self._pending: dict[int, asyncio.Future] = {}
        self._running = False
        self._reader_task: Optional[asyncio.Task] = None

    async def start(self):
        if self._running:
            return
        self._running = True

        python = sys.executable
        self._process = await asyncio.create_subprocess_exec(
            python, WORKER_SCRIPT, "--mode", "stdio",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        )
        logger.info(f"Subprocess worker started (PID {self._process.pid})")

        self._reader_task = asyncio.create_task(self._read_stdout())

    async def _read_stdout(self):
        while self._running and self._process and not self._process.stdout.at_eof():
            line = await self._process.stdout.readline()
            if not line:
                break
            msg = decode_msg(line.decode("utf-8", errors="replace"))
            if not msg:
                continue

            msg_id = msg.get("id")
            if msg_id in self._pending:
                future = self._pending.pop(msg_id)
                if not future.done():
                    future.set_result(msg)

        logger.info("Subprocess worker stdout closed")

    async def send_query(
        self,
        session_id: str,
        payload: dict,
        base_url: str,
        poll_interval: float = 2.0,
        poll_timeout: float = 600.0,
        callback: Optional[Callable] = None,
    ) -> str:
        self._msg_id += 1
        msg_id = self._msg_id
        future: asyncio.Future = asyncio.get_event_loop().create_future()
        self._pending[msg_id] = future

        cmd = {
            "id": msg_id,
            "type": MSG_QUERY,
            "session_id": session_id,
            "payload": payload,
            "base_url": base_url,
            "poll_interval": poll_interval,
            "poll_timeout": poll_timeout,
        }
        await self._write_cmd(cmd)

        full_text = ""
        deadline = time.monotonic() + poll_timeout
        while time.monotonic() < deadline:
            try:
                result = await asyncio.wait_for(
                    asyncio.shield(future), timeout=5.0
                )
            except asyncio.TimeoutError:
                if msg_id in self._pending:
                    future = self._pending[msg_id]
                    if future.done():
                        result = future.result()
                    else:
                        continue
                else:
                    break

            msg_type = result.get("type")
            if msg_type == MSG_UPDATE:
                diff = result.get("content", "")
                full_text = result.get("full_text", full_text + diff)
                if callback:
                    callback(full_text)
                new_future = asyncio.get_event_loop().create_future()
                self._pending[msg_id] = new_future
                future = new_future
            elif msg_type == MSG_RESULT:
                content = result.get("content", "")
                if callback:
                    callback(content)
                return content
            elif msg_type == MSG_ERROR:
                return f"Error: {result.get('message', 'Unknown worker error')}"
            elif msg_type == MSG_CANCELLED:
                return "Error: Request was cancelled."
            else:
                if msg_id in self._pending:
                    del self._pending[msg_id]
                return full_text or "Error: Unexpected response"

        if msg_id in self._pending:
            del self._pending[msg_id]
        return full_text or "Error: Request timed out"

    async def connect(self, base_url: str):
        self._msg_id += 1
        cmd = {"id": self._msg_id, "type": MSG_CONNECT, "base_url": base_url}
        await self._write_cmd(cmd)

    async def _write_cmd(self, cmd: dict):
        line = json.dumps(cmd, default=str) + "\n"
        if self._process and self._process.stdin:
            self._process.stdin.write(line.encode("utf-8"))
            await self._process.stdin.drain()

    async def shutdown(self):
        self._running = False
        try:
            if self._process:
                # Close stdin first to signal EOF to worker (graceful path)
                try:
                    if self._process.stdin and not self._process.stdin.is_closing():
                        self._process.stdin.close()
                except Exception:
                    pass
                # Send shutdown command (may fail if stdin already closed)
                try:
                    await self._write_cmd({"type": MSG_SHUTDOWN})
                except Exception:
                    pass
                try:
                    await asyncio.wait_for(self._process.wait(), timeout=5.0)
                except asyncio.TimeoutError:
                    self._process.kill()
                    await self._process.wait()
        except Exception as e:
            logger.warning(f"Subprocess worker shutdown error: {e}")
        finally:
            if self._reader_task:
                self._reader_task.cancel()
                try:
                    await self._reader_task
                except (asyncio.CancelledError, Exception):
                    pass
                self._reader_task = None
            # Explicitly close all pipe transports to prevent ResourceWarning
            # when the proactor loop tears them down at GC time. On Windows
            # these are `_ProactorBasePipeTransport` whose __del__ tries
            # `fileno()` on a closed pipe and raises ValueError.
            if self._process is not None:
                for pipe_name in ('stdin', 'stdout', 'stderr'):
                    pipe = getattr(self._process, pipe_name, None)
                    if pipe is not None:
                        try:
                            transport = getattr(pipe, '_transport', None) or pipe
                            if hasattr(transport, 'close'):
                                transport.close()
                        except Exception:
                            pass
            self._process = None

    async def cancel_current(self):
        """Cancel the in-flight request by killing and respawning the subprocess.

        Used by SIGINT handler in CLI when user presses Ctrl+C. The pending
        future in self._pending will never resolve (subprocess is dead), so
        the awaiter will hit its timeout and propagate the cancellation.
        """
        if not self._process or self._process.returncode is not None:
            return
        logger.info("Cancelling in-flight request by terminating subprocess (PID %s)", self._process.pid)
        try:
            self._process.kill()
        except Exception as e:
            logger.warning("Error killing subprocess: %s", e)
        # Mark all pending futures as cancelled so awaiters unblock immediately
        for msg_id, future in list(self._pending.items()):
            if not future.done():
                future.cancel()
            self._pending.pop(msg_id, None)
        # Wait for the process to actually die, then respawn so the next
        # request gets a fresh worker (the old one is in a bad state).
        try:
            await asyncio.wait_for(self._process.wait(), timeout=2.0)
        except asyncio.TimeoutError:
            pass
        self._process = None
        self._running = False
        # The next send_query() will trigger a fresh start() via BotClient.ensure_worker()

    @property
    def is_alive(self) -> bool:
        return self._process is not None and self._process.returncode is None
