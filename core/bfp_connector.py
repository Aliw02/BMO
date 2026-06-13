"""BFP Connector — high-level relay-based agent communication.

Manages a persistent WebSocket connection to the BFP relay for:
  - Discovery (find agents by capability)
  - Message passing (send/receive through relay, no NAT issues)
  - Task delegation and response
"""

import asyncio
import json
import logging
import uuid
from typing import Callable, Optional

import websockets

from core.bfp_discovery import BFPDiscovery
from core.bfp_identity import get_did
from core.bfp_agent_card import get_capabilities
from core.bfp_tasks import create_task, get_task, wait_for_task, list_tasks

logger = logging.getLogger(__name__)


class BFPConnector:
    """Persistent relay connection for discovery + message passing.

    Flow:
      1. connect(relay_url) → opens WebSocket to relay, registers DID
      2. find_agents(capability) → REST query to relay directory
      3. send(target_did, payload) → relay forwards to target's WS connection
      4. Incoming messages from relay are dispatched to on_message handler
    """

    def __init__(self):
        self.my_did = get_did()
        self.relay_url = None
        self._ws = None
        self._reader_task = None
        self._running = False
        self._pending: dict[str, asyncio.Future] = {}
        self._on_message: Optional[Callable] = None
        self._inbox: asyncio.Queue = asyncio.Queue()
        self._reconnect_task = None

    @property
    def is_connected(self) -> bool:
        return self._running and self._ws is not None

    async def connect(self, relay_url: str, on_message: Callable = None):
        """Open persistent WS to relay and register."""
        self.relay_url = relay_url.rstrip("/")
        self._on_message = on_message
        ws_url = self.relay_url.replace("http://", "ws://").replace("https://", "wss://") + "/ws"
        self._ws = await websockets.connect(ws_url)
        self._running = True

        caps = [c["id"] for c in get_capabilities()]
        await self._ws.send(json.dumps({
            "action": "register",
            "did": self.my_did,
            "endpoint": f"relay:{self.relay_url}",
            "capabilities": caps,
            "name": "BMO",
        }))
        resp = json.loads(await self._ws.recv())
        logger.info("BFP relay register: %s", resp.get("status", "ok"))

        self._reader_task = asyncio.create_task(self._reader())
        logger.info("BFP connector connected to relay %s", relay_url)

    async def _reader(self):
        try:
            async for raw in self._ws:
                try:
                    msg = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                msg_type = msg.get("type", "")

                if msg_type == "relay_message":
                    request_id = msg.get("requestId", "")
                    from_did = msg.get("fromDid", "")
                    payload = msg.get("payload", {})
                    await self._handle_incoming(from_did, payload, request_id)
                elif msg_type == "send_result":
                    request_id = msg.get("requestId", "")
                    future = self._pending.get(request_id)
                    if future and not future.done():
                        future.set_result(msg)
                elif msg_type in ("list_result", "register_result", "resolve_result"):
                    request_id = msg.get("requestId", "")
                    if request_id:
                        future = self._pending.get(request_id)
                        if future and not future.done():
                            future.set_result(msg)
                elif msg_type == "agent_list":
                    pass
        except websockets.exceptions.ConnectionClosed:
            logger.warning("BFP relay WS disconnected")
        finally:
            self._running = False
            if self._reconnect_task is None:
                self._reconnect_task = asyncio.create_task(self._reconnect_loop())

    async def _handle_incoming(self, from_did: str, payload: dict, request_id: str):
        task_data = payload.get("params", {}).get("task", payload)
        task_id = await create_task(task_data, from_did)
        task = await wait_for_task(task_id)
        result = task.get("result", "") if task else ""

        if request_id and self._ws and not getattr(self._ws, "closed", getattr(getattr(self._ws, "state", None), "name", "") == "CLOSED"):
            await self._ws.send(json.dumps({
                "action": "relay_response",
                "requestId": request_id,
                "payload": {"task_id": task_id, "result": result, "from": self.my_did},
            }))

        if self._on_message:
            await self._on_message(from_did, task_id, result)

    async def _reconnect_loop(self):
        await asyncio.sleep(5)
        while not self._running:
            try:
                await self.connect(self.relay_url, self._on_message)
                logger.info("BFP reconnected to relay")
                self._reconnect_task = None
                return
            except Exception as e:
                logger.warning("BFP reconnect failed: %s, retry in 10s", e)
                await asyncio.sleep(10)

    async def send(self, target_did: str, payload: dict, timeout: float = 60.0) -> dict:
        if not self._ws or getattr(self._ws, "closed", getattr(getattr(self._ws, "state", None), "name", "") == "CLOSED"):
            raise RuntimeError("Not connected to relay")
        request_id = f"bfp-{uuid.uuid4().hex[:12]}"
        future = asyncio.get_event_loop().create_future()
        self._pending[request_id] = future
        try:
            await self._ws.send(json.dumps({
                "action": "send",
                "targetDid": target_did,
                "fromDid": self.my_did,
                "payload": payload,
                "requestId": request_id,
            }))
            result = await asyncio.wait_for(future, timeout=timeout)
            return result.get("response", {})
        finally:
            self._pending.pop(request_id, None)

    async def delegate(self, target_did: str, task_data: dict) -> dict:
        return await self.send(target_did, {
            "method": "bfp.delegate",
            "params": {
                "from": self.my_did,
                "to": target_did,
                "task": task_data,
            },
        })

    async def find_agents(self, capability: str = None) -> list[dict]:
        if not self._ws or getattr(self._ws, "closed", getattr(getattr(self._ws, "state", None), "name", "") == "CLOSED"):
            raise RuntimeError("Not connected to relay")
        request_id = f"bfp-list-{uuid.uuid4().hex[:12]}"
        future = asyncio.get_event_loop().create_future()
        self._pending[request_id] = future
        try:
            await self._ws.send(json.dumps({
                "action": "list",
                "capability": capability,
                "requestId": request_id,
            }))
            result = await asyncio.wait_for(future, timeout=10.0)
            return result.get("agents", [])
        finally:
            self._pending.pop(request_id, None)

    async def talk(self, target_did: str, message: str) -> str:
        result = await self.delegate(target_did, {"query": message, "action": "talk"})
        return result.get("result", "")

    async def disconnect(self):
        self._running = False
        if self._reader_task:
            self._reader_task.cancel()
            self._reader_task = None
        if self._ws:
            await self._ws.close()
            self._ws = None
