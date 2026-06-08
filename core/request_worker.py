import asyncio
import json
import logging
import multiprocessing
import os
import sys
from typing import Optional

import httpx

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.worker_protocol import (
    encode_msg, decode_msg, MSG_QUERY, MSG_RESULT, MSG_UPDATE,
    MSG_ERROR, MSG_CANCELLED, MSG_CONNECT, MSG_SHUTDOWN,
)

logger = logging.getLogger(__name__)


class RequestWorker:
    """Runs in a child process. Handles all OpenCode API HTTP calls.
    Supports both multiprocessing.Queue transport and stdin/stdout JSON-RPC.
    """

    def __init__(self, task_queue: Optional[multiprocessing.Queue] = None,
                 result_queue: Optional[multiprocessing.Queue] = None):
        self.task_queue = task_queue
        self.result_queue = result_queue
        self._send_fn = None
        self.http_client: Optional[httpx.AsyncClient] = None
        self._running = False
        self._msg_id_counter = 0

    def _set_stdio_transport(self, writer):
        self._send_fn = writer

    async def _send_result(self, data: dict):
        if self._send_fn:
            await self._send_fn(data)
        elif self.result_queue:
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(None, self.result_queue.put, data)

    async def _init_client(self):
        self.http_client = httpx.AsyncClient(
            timeout=httpx.Timeout(600.0, connect=30.0),
            follow_redirects=True,
            trust_env=False
        )

    async def _handle_query(self, task: dict):
        session_id = task["session_id"]
        payload = task["payload"]
        base_url = task["base_url"]
        poll_interval = task.get("poll_interval", 2.0)
        poll_timeout = task.get("poll_timeout", 600.0)

        try:
            post_url = f"{base_url}/session/{session_id}/message"
            resp = await self.http_client.post(post_url, json=payload)
            if resp.status_code != 200:
                await self._send_result({
                    "type": "error",
                    "message": f"POST failed: {resp.status_code} {resp.text[:200]}"
                })
                return

            get_url = f"{base_url}/session/{session_id}/message?limit=20"
            start = asyncio.get_event_loop().time()
            last_text = ""

            while True:
                elapsed = asyncio.get_event_loop().time() - start
                if elapsed > poll_timeout:
                    await self._send_result({
                        "type": "error",
                        "message": f"Poll timeout after {poll_timeout}s"
                    })
                    return

                resp = await self.http_client.get(get_url)
                if resp.status_code != 200:
                    await asyncio.sleep(poll_interval)
                    continue

                data = resp.json()
                messages = data if isinstance(data, list) else data.get("messages", [])
                if not messages:
                    await asyncio.sleep(poll_interval)
                    continue

                latest = messages[-1]
                role = latest.get("role", "")
                content = latest.get("content", "") or ""
                parts = latest.get("parts", [])
                text_parts = [p.get("text", "") for p in parts if p.get("type") == "text"]
                full_text = content or "".join(text_parts)

                status = latest.get("status", "")
                if status == "completed" or latest.get("completed", False):
                    await self._send_result({
                        "type": "result",
                        "content": full_text,
                        "message_id": latest.get("id", ""),
                        "done": True
                    })
                    return

                if full_text != last_text:
                    diff = full_text[len(last_text):] if len(full_text) > len(last_text) else full_text
                    await self._send_result({
                        "type": "update",
                        "content": diff,
                        "full_text": full_text,
                        "done": False
                    })
                    last_text = full_text

                await asyncio.sleep(poll_interval)

        except asyncio.CancelledError:
            await self._send_result({"type": "cancelled"})
        except Exception as e:
            await self._send_result({
                "type": "error",
                "message": f"{type(e).__name__}: {str(e)}"
            })

    async def _handle_connect(self, base_url: str):
        try:
            resp = await self.http_client.get(f"{base_url}/session", timeout=10.0)
            ok = resp.status_code < 500
            await self._send_result({"type": "connect_result", "ok": ok})
        except Exception as e:
            await self._send_result({"type": "connect_result", "ok": False, "error": str(e)})

    async def run(self):
        self._running = True
        await self._init_client()

        while self._running:
            try:
                task = None
                if self.task_queue is not None:
                    try:
                        task = self.task_queue.get_nowait()
                    except Exception:
                        await asyncio.sleep(0.1)
                        continue
                else:
                    await asyncio.sleep(0.1)
                    continue

                task_type = task.get("type", "")
                if task_type == "query":
                    await self._handle_query(task)
                elif task_type == "connect":
                    await self._handle_connect(task["base_url"])
                elif task_type == "shutdown":
                    self._running = False
                    break

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Worker error: {e}")
                await asyncio.sleep(1)

        if self.http_client:
            await self.http_client.aclose()

    async def run_stdio(self):
        """Run worker reading JSON-RPC from stdin, writing to stdout.
        Uses run_in_executor for stdin reads (Windows proactor compatible).
        """
        self._running = True
        await self._init_client()

        loop = asyncio.get_event_loop()

        async def write_json(data):
            line = json.dumps(data, default=str) + "\n"
            sys.stdout.write(line)
            sys.stdout.flush()

        self._send_fn = write_json

        while self._running:
            try:
                line = await asyncio.wait_for(
                    loop.run_in_executor(None, sys.stdin.readline),
                    timeout=0.5,
                )
            except asyncio.TimeoutError:
                continue
            except Exception:
                break

            if not line:
                await asyncio.sleep(0.1)
                continue

            cmd = decode_msg(line)
            if not cmd:
                continue

            task_type = cmd.get("type")
            if task_type == MSG_QUERY:
                await self._handle_query(cmd)
            elif task_type == MSG_CONNECT:
                await self._handle_connect(cmd["base_url"])
            elif task_type == MSG_SHUTDOWN:
                self._running = False
                break

        if self.http_client:
            await self.http_client.aclose()


def worker_main(task_queue, result_queue):
    """Entry point for multiprocessing.Process worker."""
    worker = RequestWorker(task_queue, result_queue)
    asyncio.run(worker.run())


def worker_main_stdio():
    """Entry point for subprocess worker (stdin/stdout JSON-RPC)."""
    worker = RequestWorker(task_queue=None, result_queue=None)
    asyncio.run(worker.run_stdio())


if __name__ == "__main__":
    if "--mode" in sys.argv:
        idx = sys.argv.index("--mode")
        if idx + 1 < len(sys.argv) and sys.argv[idx + 1] == "stdio":
            worker_main_stdio()
        else:
            print("Usage: request_worker.py --mode stdio")
            sys.exit(1)
    else:
        print("Usage: request_worker.py --mode stdio")
        sys.exit(1)
