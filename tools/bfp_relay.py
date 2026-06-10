import argparse
import asyncio
import json
import logging
import os
import time
from datetime import datetime, timezone
from typing import Optional

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
import uvicorn

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("bfp-relay")

DEFAULT_PORT = 9753
TTL_SECONDS = 300
CLEANUP_INTERVAL = 60


class BFPRelay:
    def __init__(self, host: str = "0.0.0.0", port: int = DEFAULT_PORT, persist_path: str = None):
        self.host = host
        self.port = port
        self.persist_path = persist_path
        self._agents: dict[str, dict] = {}
        self._ws_clients: set[WebSocket] = set()
        self._ws_by_did: dict[str, WebSocket] = {}
        self._pending_responses: dict[str, asyncio.Future] = {}
        self._app: FastAPI | None = None
        self._cleanup_task: asyncio.Task | None = None
        self._server: uvicorn.Server | None = None
        self._loop: asyncio.AbstractEventLoop | None = None

    def _validate_did(self, did: str) -> None:
        if not did.startswith("did:bfp:"):
            raise ValueError(f"Invalid DID format '{did}' — must start with 'did:bfp:'")

    def register(self, did: str, endpoint: str, caps: list, pubkey: str, name: str = None) -> dict:
        self._validate_did(did)
        now = time.time()
        self._agents[did] = {
            "endpoint": endpoint,
            "capabilities": caps or [],
            "publicKey": pubkey,
            "name": name or did.split(":")[-1][:24],
            "lastSeen": now,
        }
        self._persist()
        log.info("REGISTER  did=%s  endpoint=%s  caps=%s", did, endpoint, caps)
        return {"status": "ok", "ttl": TTL_SECONDS}

    def resolve(self, did: str) -> dict | None:
        agent = self._agents.get(did)
        if agent is None:
            return None
        now = time.time()
        if now - agent["lastSeen"] > TTL_SECONDS:
            self.unregister(did)
            return None
        log.info("RESOLVE   did=%s  endpoint=%s", did, agent["endpoint"])
        return {
            "did": did,
            "endpoint": agent["endpoint"],
            "capabilities": agent["capabilities"],
            "lastSeen": agent["lastSeen"],
        }

    def list_agents(self, capability: str = None) -> list[dict]:
        now = time.time()
        stale = [did for did, a in self._agents.items() if now - a["lastSeen"] > TTL_SECONDS]
        for did in stale:
            self.unregister(did)
        agents = []
        for did, a in self._agents.items():
            if capability and capability not in a["capabilities"]:
                continue
            agents.append({
                "did": did,
                "name": a["name"],
                "capabilities": a["capabilities"],
                "lastSeen": a["lastSeen"],
            })
        agents.sort(key=lambda x: x["lastSeen"], reverse=True)
        return agents

    def unregister(self, did: str) -> None:
        if did in self._agents:
            del self._agents[did]
            self._persist()
            log.info("UNREGISTER did=%s", did)

    def get_agent_count(self) -> int:
        now = time.time()
        self._agents = {d: a for d, a in self._agents.items() if now - a["lastSeen"] <= TTL_SECONDS}
        return len(self._agents)

    def _persist(self) -> None:
        if not self.persist_path:
            return
        data = {did: info for did, info in self._agents.items()}
        tmp = self.persist_path + ".tmp"
        try:
            with open(tmp, "w") as f:
                json.dump(data, f)
            os.replace(tmp, self.persist_path)
        except OSError as e:
            log.warning("persist failed: %s", e)

    def _load(self) -> None:
        if not self.persist_path or not os.path.exists(self.persist_path):
            return
        try:
            with open(self.persist_path) as f:
                data = json.load(f)
            now = time.time()
            for did, info in data.items():
                if now - info.get("lastSeen", 0) <= TTL_SECONDS:
                    self._agents[did] = info
            log.info("loaded %d agents from %s", len(self._agents), self.persist_path)
        except (OSError, json.JSONDecodeError) as e:
            log.warning("load failed: %s", e)

    def _build_app(self) -> FastAPI:
        app = FastAPI(title="BFP Relay", version="1.0.0")

        app.add_middleware(
            CORSMiddleware,
            allow_origins=["*"],
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )

        @app.post("/register")
        async def register_endpoint(body: dict):
            did = body.get("did", "").strip()
            endpoint = body.get("endpoint", "").strip()
            caps = body.get("capabilities", [])
            pubkey = body.get("publicKey", "")
            name = body.get("name")
            if not did or not endpoint:
                raise HTTPException(status_code=400, detail="did and endpoint are required")
            try:
                result = self.register(did, endpoint, caps, pubkey, name)
            except ValueError as e:
                raise HTTPException(status_code=400, detail=str(e))
            return result

        @app.get("/resolve/{did:path}")
        async def resolve_endpoint(did: str):
            did = did.strip()
            result = self.resolve(did)
            if result is None:
                raise HTTPException(status_code=404, detail=f"agent {did} not found")
            return result

        @app.get("/list")
        async def list_endpoint(capability: str = None):
            return {"agents": self.list_agents(capability)}

        @app.delete("/unregister/{did:path}")
        async def unregister_endpoint(did: str):
            did = did.strip()
            self.unregister(did)
            return {"status": "ok"}

        @app.get("/health")
        async def health_endpoint():
            return {"status": "ok", "agentCount": self.get_agent_count()}

        @app.websocket("/ws")
        async def websocket_endpoint(ws: WebSocket):
            await ws.accept()
            self._ws_clients.add(ws)
            log.info("WS connect  clients=%d", len(self._ws_clients))
            registered_did = None
            try:
                while True:
                    data = await ws.receive_text()
                    msg = json.loads(data)
                    action = msg.get("action")
                    if action == "register":
                        try:
                            did = msg["did"]
                            result = self.register(
                                did, msg["endpoint"],
                                msg.get("capabilities", []),
                                msg.get("publicKey", ""),
                                msg.get("name"),
                            )
                            self._ws_by_did[did] = ws
                            registered_did = did
                            await ws.send_json({"type": "register_result", **result})
                            await self._broadcast_agent_list()
                        except (ValueError, KeyError) as e:
                            await ws.send_json({"type": "error", "detail": str(e)})
                    elif action == "resolve":
                        result = self.resolve(msg["did"])
                        if result is None:
                            await ws.send_json({"type": "resolve_result", "found": False})
                        else:
                            await ws.send_json({"type": "resolve_result", "found": True, **result})
                    elif action == "list":
                        agents = self.list_agents(msg.get("capability"))
                        resp = {"type": "list_result", "agents": agents}
                        if "requestId" in msg:
                            resp["requestId"] = msg["requestId"]
                        await ws.send_json(resp)
                    elif action == "forward":
                        target_did = msg.get("targetDid")
                        payload = msg.get("payload", {})
                        target = self.resolve(target_did)
                        if target is None:
                            await ws.send_json({"type": "forward_result", "status": "not_found"})
                        else:
                            await self._relay_forward(target_did, target["endpoint"], payload, ws)
                    elif action == "send":
                        target_did = msg.get("targetDid")
                        payload = msg.get("payload", {})
                        request_id = msg.get("requestId", "")
                        target_ws = self._ws_by_did.get(target_did)
                        if target_ws is None:
                            await ws.send_json({"type": "send_result", "status": "offline", "requestId": request_id})
                        else:
                            future = asyncio.get_event_loop().create_future()
                            self._pending_responses[request_id] = future
                            try:
                                await target_ws.send_json({
                                    "type": "relay_message",
                                    "fromDid": msg.get("fromDid", ""),
                                    "payload": payload,
                                    "requestId": request_id,
                                })
                                resp = await asyncio.wait_for(future, timeout=60.0)
                                await ws.send_json({"type": "send_result", "status": "delivered", "response": resp, "requestId": request_id})
                            except asyncio.TimeoutError:
                                await ws.send_json({"type": "send_result", "status": "timeout", "requestId": request_id})
                            finally:
                                self._pending_responses.pop(request_id, None)
                    elif action == "relay_response":
                        request_id = msg.get("requestId", "")
                        future = self._pending_responses.get(request_id)
                        if future and not future.done():
                            future.set_result(msg.get("payload", {}))
                    else:
                        await ws.send_json({"type": "error", "detail": f"unknown action: {action}"})
            except WebSocketDisconnect:
                pass
            finally:
                self._ws_clients.discard(ws)
                if registered_did:
                    self._ws_by_did.pop(registered_did, None)
                log.info("WS disconnect  clients=%d", len(self._ws_clients))
                await self._broadcast_agent_list()

        return app

    async def _broadcast_agent_list(self) -> None:
        agents = self.list_agents()
        msg = json.dumps({"type": "agent_list", "agents": agents})
        stale = set()
        for ws in self._ws_clients:
            try:
                await ws.send_text(msg)
            except Exception:
                stale.add(ws)
        self._ws_clients -= stale

    async def _relay_forward(self, target_did: str, target_endpoint: str, payload: dict, source_ws: WebSocket) -> None:
        import aiohttp
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"{target_endpoint}/bfp/inbox",
                    json={"from": "relay", "payload": payload},
                    timeout=aiohttp.ClientTimeout(total=10),
                ) as resp:
                    if resp.ok:
                        body = await resp.json()
                        await source_ws.send_json({
                            "type": "forward_result",
                            "status": "delivered",
                            "response": body,
                        })
                    else:
                        await source_ws.send_json({
                            "type": "forward_result",
                            "status": "delivery_failed",
                            "httpStatus": resp.status,
                        })
        except Exception as e:
            await source_ws.send_json({
                "type": "forward_result",
                "status": "delivery_failed",
                "detail": str(e),
            })

    async def _cleanup_loop(self) -> None:
        while True:
            await asyncio.sleep(CLEANUP_INTERVAL)
            before = len(self._agents)
            self.get_agent_count()
            after = len(self._agents)
            if before != after:
                log.info("cleanup  removed=%d  remaining=%d", before - after, after)
                await self._broadcast_agent_list()

    def start(self) -> None:
        self._load()
        app = self._build_app()
        self._app = app

        config = uvicorn.Config(
            app,
            host=self.host,
            port=self.port,
            log_level="info",
            access_log=True,
        )
        self._server = uvicorn.Server(config)
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)

        self._cleanup_task = self._loop.create_task(self._cleanup_loop())

        try:
            log.info("BFP Relay starting on %s:%s", self.host, self.port)
            self._server.run()
        except KeyboardInterrupt:
            pass
        finally:
            self.stop()

    def stop(self) -> None:
        if self._server:
            self._server.should_exit = True
        if self._cleanup_task and self._loop and self._loop.is_running():
            try:
                self._loop.call_soon_threadsafe(self._cleanup_task.cancel)
            except Exception:
                pass
        self._persist()
        log.info("BFP Relay stopped")


def main():
    parser = argparse.ArgumentParser(description="BFP Relay Directory Server")
    parser.add_argument("--host", default="0.0.0.0", help="Bind address")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help=f"Port (default: {DEFAULT_PORT})")
    parser.add_argument("--persist", default=None, help="Path to JSON file for persistence")
    parser.add_argument("--debug", action="store_true", help="Enable debug logging")
    args = parser.parse_args()

    if args.debug:
        logging.getLogger("bfp-relay").setLevel(logging.DEBUG)

    relay = BFPRelay(host=args.host, port=args.port, persist_path=args.persist)
    relay.start()


def start_detached(host: str = "127.0.0.1", port: int = DEFAULT_PORT,
                   persist_path: str = None) -> BFPRelay:
    """Start a BFP Relay in a background thread.

    Returns the BFPRelay instance. Call relay.stop() to terminate.
    This is the import-friendly entry point used by BMO for auto-start.
    """
    import threading
    relay = BFPRelay(host=host, port=port, persist_path=persist_path)

    def _run():
        relay.start()

    t = threading.Thread(target=_run, name="bfp-relay", daemon=True)
    t.start()
    return relay


if __name__ == "__main__":
    main()
