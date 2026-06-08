import asyncio
import json
import logging
import time
import uuid
import threading
from datetime import datetime, timezone
from typing import Optional

import websockets
from websockets.asyncio.server import ServerConnection, serve as ws_serve
from websockets.http11 import Headers, Response

from core.bfp_identity import get_did, sign, verify
from core.bfp_tasks import create_task, get_task, update_task

logger = logging.getLogger(__name__)

BFP_METHODS = frozenset({
    "bfp.discover",
    "bfp.connect",
    "bfp.delegate",
    "bfp.status",
    "bfp.cancel",
    "bfp.ping",
})


def _make_rpc_message(method: str, params: dict, msg_id: Optional[str] = None) -> dict:
    if msg_id is None:
        msg_id = f"msg-{uuid.uuid4().hex[:8]}"
    signed = sign(dict(params))
    signature = signed.pop("signature", None)
    message = {
        "jsonrpc": "2.0",
        "id": msg_id,
        "method": method,
        "params": params,
    }
    if signature:
        message["signature"] = signature
    return message


def _verify_message(remote_did: str, message: dict) -> bool:
    if "signature" not in message:
        return False
    sig = message["signature"]
    params = message.get("params", {})
    check_data = dict(params)
    check_data["signature"] = sig
    return verify(remote_did, check_data)


def _make_error(msg_id, code: int, message_text: str):
    return {
        "jsonrpc": "2.0",
        "id": msg_id,
        "error": {"code": code, "message": message_text},
    }


class BFPRequest:
    def __init__(self, message: dict, connection: "ServerConnection"):
        self.message = message
        self.connection = connection
        self.method = message.get("method", "")
        self.msg_id = message.get("id")
        self.params = message.get("params", {})

    async def reply(self, result: dict):
        response = {"jsonrpc": "2.0", "id": self.msg_id, "result": result}
        await self.connection.send(json.dumps(response))

    async def reply_error(self, code: int, message_text: str, data: dict = None):
        error = {"code": code, "message": message_text}
        if data:
            error["data"] = data
        response = {"jsonrpc": "2.0", "id": self.msg_id, "error": error}
        await self.connection.send(json.dumps(response))


class BFPServer:
    def __init__(self, host: str = "0.0.0.0", port: int = 8765):
        self.host = host
        self.port = port
        self._server = None
        self._thread = None
        self._loop = None
        self._request_queue = asyncio.Queue()
        self._running = False
        self._actual_port = port
        self._connections: dict[str, tuple] = {}

    def get_port(self) -> int:
        return self._actual_port

    async def _handle_method(self, request: BFPRequest):
        method = request.method
        params = request.params
        logger.info(f"Handling {method} from {params.get('from', 'unknown')}")

        if method == "bfp.discover":
            await request.reply({
                "agent": "BMO",
                "did": get_did(),
                "version": "1.0.0",
                "methods": sorted(BFP_METHODS),
                "timestamp": datetime.now(timezone.utc).isoformat(),
            })
        elif method == "bfp.ping":
            await request.reply({"pong": True, "timestamp": datetime.now(timezone.utc).isoformat()})
        elif method == "bfp.connect":
            session_id = f"ses-{uuid.uuid4().hex[:12]}"
            remote_did = params.get("from", "")
            await request.reply({
                "session_id": session_id,
                "status": "connected",
                "remote_did": remote_did,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            })
        elif method == "bfp.delegate":
            task = params.get("task", {})
            source_did = params.get("from", "unknown")
            task_id = create_task(task, source_did)
            task_data = get_task(task_id)
            await request.reply({
                "task_id": task_id,
                "status": task_data["status"] if task_data else "error",
                "result": task_data.get("result") if task_data else None,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            })
        elif method == "bfp.status":
            task_id = params.get("task_id", "")
            task_data = get_task(task_id)
            if task_data:
                await request.reply({
                    "task_id": task_id,
                    "status": task_data["status"],
                    "result": task_data.get("result"),
                    "error": task_data.get("error"),
                    "created_at": task_data["created_at"],
                    "updated_at": task_data["updated_at"],
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                })
            else:
                await request.reply({
                    "task_id": task_id,
                    "status": "not_found",
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                })
        elif method == "bfp.cancel":
            task_id = params.get("task_id", "")
            task_data = get_task(task_id)
            if task_data and task_data["status"] in ("pending", "processing"):
                update_task(task_id, "cancelled")
                await request.reply({
                    "task_id": task_id,
                    "status": "cancelled",
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                })
            else:
                await request.reply({
                    "task_id": task_id,
                    "status": task_data["status"] if task_data else "not_found",
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                })
        else:
            await request.reply_error(-32601, f"Method not found: {method}")

    async def _handler(self, conn: ServerConnection):
        remote_did = None
        conn_id = str(id(conn))
        try:
            async for raw in conn:
                try:
                    message = json.loads(raw)
                except json.JSONDecodeError:
                    await conn.send(json.dumps(_make_error(None, -32700, "Parse error")))
                    continue

                if not isinstance(message, dict) or message.get("jsonrpc") != "2.0":
                    msg_id = message.get("id") if isinstance(message, dict) else None
                    await conn.send(json.dumps(_make_error(msg_id, -32600, "Invalid Request")))
                    continue

                method = message.get("method", "")
                if method not in BFP_METHODS:
                    await conn.send(json.dumps(_make_error(message.get("id"), -32601, f"Method not found: {method}")))
                    continue

                params = message.get("params", {})
                remote_did = params.get("from", remote_did)

                if remote_did and method not in ("bfp.discover",):
                    if not _verify_message(remote_did, message):
                        await conn.send(json.dumps(_make_error(message.get("id"), -32000, "Signature verification failed")))
                        continue

                if remote_did:
                    self._connections[conn_id] = (conn, remote_did)

                request = BFPRequest(message, conn)
                await self._handle_method(request)

        except websockets.exceptions.ConnectionClosed:
            pass
        finally:
            self._connections.pop(conn_id, None)

    async def _process_request(self, conn, request):
        connection_header = request.headers.get("Connection", "")
        if "upgrade" not in connection_header.lower():
            from websockets.http11 import Response
            return Response(426, "Upgrade Required", Headers({"Content-Type": "text/plain"}), b"WebSocket endpoint - use ws:// scheme")

    async def _run_server(self):
        self._server = await ws_serve(
            self._handler,
            self.host,
            self.port,
            process_request=self._process_request,
        )
        sockname = self._server.sockets[0].getsockname() if self._server.sockets else None
        if sockname:
            self._actual_port = sockname[1]
        logger.info(f"BFP Server listening on ws://{self.host}:{self._actual_port}")
        await self._server.serve_forever()

    def start(self):
        if self._running:
            return
        self._running = True

        def _run():
            self._loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self._loop)
            self._loop.run_until_complete(self._run_server())

        self._thread = threading.Thread(target=_run, daemon=True, name="bfp-server")
        self._thread.start()
        logger.info("BFP Server thread started")

    async def stop(self):
        self._running = False
        if self._server:
            self._server.close()
        if self._loop:
            self._loop.call_soon_threadsafe(self._loop.stop)
        if self._thread:
            self._thread.join(timeout=3)

    async def get_request(self, timeout: float = 1.0) -> Optional[BFPRequest]:
        try:
            return await asyncio.wait_for(self._request_queue.get(), timeout=timeout)
        except asyncio.TimeoutError:
            return None


class BFPClient:
    def __init__(self, endpoint: str, remote_did: str):
        self.endpoint = endpoint
        self.remote_did = remote_did
        self._conn = None
        self._reader_task = None
        self._incoming = asyncio.Queue()
        self._running = False

    async def connect(self):
        self._conn = await websockets.connect(self.endpoint)
        self._running = True
        self._reader_task = asyncio.create_task(self._reader())
        logger.info(f"BFP Client connected to {self.endpoint}")
        return True

    async def _reader(self):
        try:
            async for raw in self._conn:
                try:
                    msg = json.loads(raw)
                    await self._incoming.put(msg)
                except json.JSONDecodeError:
                    logger.warning(f"Client received malformed JSON: {raw[:200]}")
        except websockets.exceptions.ConnectionClosed:
            pass
        finally:
            self._running = False

    async def _send_and_wait(self, method: str, params: dict, timeout: float = 30.0) -> dict:
        if not self._conn:
            raise RuntimeError("Not connected. Call connect() first.")

        msg_id = f"msg-{uuid.uuid4().hex[:8]}"
        message = _make_rpc_message(method, params, msg_id)
        await self._conn.send(json.dumps(message))

        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                remaining = deadline - time.monotonic()
                resp = await asyncio.wait_for(self._incoming.get(), timeout=max(remaining, 0.1))
                if resp.get("id") == msg_id:
                    if "error" in resp:
                        raise RuntimeError(f"RPC error: {resp['error']}")
                    return resp
            except asyncio.TimeoutError:
                continue

        raise asyncio.TimeoutError(f"Timeout waiting for response to {method}")

    async def discover(self) -> dict:
        params = {"from": get_did(), "timestamp": datetime.now(timezone.utc).isoformat()}
        response = await self._send_and_wait("bfp.discover", params)
        return response.get("result", {})

    async def delegate(self, task: dict) -> str:
        params = {
            "from": get_did(),
            "to": self.remote_did,
            "task": task,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        response = await self._send_and_wait("bfp.delegate", params)
        result = response.get("result", {})
        return result.get("task_id", "")

    async def status(self, task_id: str) -> dict:
        params = {
            "from": get_did(),
            "task_id": task_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        response = await self._send_and_wait("bfp.status", params)
        return response.get("result", {})

    async def cancel(self, task_id: str) -> dict:
        params = {
            "from": get_did(),
            "task_id": task_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        response = await self._send_and_wait("bfp.cancel", params)
        return response.get("result", {})

    async def ping(self) -> bool:
        params = {"from": get_did(), "timestamp": datetime.now(timezone.utc).isoformat()}
        try:
            response = await self._send_and_wait("bfp.ping", params, timeout=5.0)
            return response.get("result", {}).get("pong", False)
        except (asyncio.TimeoutError, RuntimeError):
            return False

    async def close(self):
        self._running = False
        if self._reader_task:
            self._reader_task.cancel()
        if self._conn:
            await self._conn.close()
            self._conn = None


async def send_message(endpoint: str, remote_did: str, method: str, params: dict) -> dict:
    client = BFPClient(endpoint, remote_did)
    try:
        await client.connect()
        return await client._send_and_wait(method, params)
    finally:
        await client.close()
