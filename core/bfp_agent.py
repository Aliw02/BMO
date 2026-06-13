"""
BFP Agent — orchestrates BFP identity, card, transport, discovery, and relay communication.
Auto-starts a local relay on boot so /bfp commands work out of the box.
"""

import asyncio
import logging
import os
from typing import Optional, Callable, Awaitable

from config.settings import DATA_DIR, BFP_RELAY_URL as _cfg_relay_url
from core.bfp_identity import get_did, sign, verify
from core.bfp_agent_card import get_signed_card, serve_agent_card, generate_agent_card
from core.bfp_transport import BFPServer, BFPClient
from core.bfp_a2a_bridge import get_a2a_agent_card, start_a2a_endpoint
from core.bfp_connector import BFPConnector
from core.bfp_discovery import BFPDiscovery
from core.bfp_tasks import set_handler as _set_task_handler, list_tasks, get_task

logger = logging.getLogger(__name__)

DEFAULT_RELAY_PORT = 9753


class BFPAgent:
    """Manages BFP lifecycle within BMO.

    Auto-starts a local relay (directory + WebSocket relay) on boot.
    If BFP_RELAY_URL is set in .env, connects to that external relay instead.
    If BFP_CONNECT_DID is set, resolves the DID from the registry to get the relay URL.
    """

    def __init__(self):
        self.did = get_did()
        self.card = get_signed_card()
        self.bfp_server: Optional[BFPServer] = None
        self.a2a_server = None
        self.connector = BFPConnector()
        self.discovery: Optional[BFPDiscovery] = None
        self.relay_url: Optional[str] = None
        self._relay_instance = None
        self._running = False

    @property
    def is_running(self):
        return self._running

    async def start(self, bfp_port: int = 8765, a2a_port: int = 8766, relay_url: str = None):
        """Start BFP server, local relay (or connect to external), A2A bridge."""
        self.bfp_server = BFPServer(port=bfp_port)
        self.bfp_server.start()

        try:
            self.a2a_server = start_a2a_endpoint(port=a2a_port)
            if asyncio.iscoroutine(self.a2a_server):
                self.a2a_server = await self.a2a_server
        except Exception as e:
            logger.warning("A2A endpoint start failed (non-fatal): %s", e)
            self.a2a_server = None

        # ── Relay: resolve DID via registry, or connect to external URL, or auto-start local ──
        effective_relay = relay_url or _cfg_relay_url
        connect_did = os.getenv("BFP_CONNECT_DID", "").strip()
        if connect_did:
            # Resolve DID from registry to get current relay URL (no hardcoded tunnel URL needed)
            registry_url = os.getenv("BFP_REGISTRY_URL", "https://bfp-registry.bmo-relay.workers.dev")
            disc = BFPDiscovery(relay_url=registry_url)
            try:
                info = await disc.resolve(connect_did)
                if info and info.get("endpoint"):
                    self.relay_url = info["endpoint"].rstrip("/")
                    logger.info("BFP resolved DID %s → relay %s", connect_did, self.relay_url)
                else:
                    logger.warning("BFP could not resolve DID %s, falling back to local relay", connect_did)
            except Exception as e:
                logger.warning("BFP resolve failed (%s), falling back to local relay", e)
            await disc.close()
        if effective_relay:
            # External relay — connect to it
            self.relay_url = effective_relay.rstrip("/")
            logger.info("BFP connecting to external relay: %s", self.relay_url)
        if not self.relay_url:
            # Auto-start a local relay
            self.relay_url = f"http://127.0.0.1:{DEFAULT_RELAY_PORT}"
            try:
                from tools.bfp_relay import start_detached
                persist = str(DATA_DIR / "bfp" / "relay_state.json")
                self._relay_instance = start_detached(
                    host="127.0.0.1",
                    port=DEFAULT_RELAY_PORT,
                    persist_path=persist,
                )
                logger.info("BFP local relay started on port %s", DEFAULT_RELAY_PORT)
                await asyncio.sleep(0.3)
            except Exception as e:
                logger.warning("BFP local relay start failed (non-fatal): %s", e)

        # ── Connect to relay as a WebSocket client ──────────────────────────
        if self.relay_url:
            self.discovery = BFPDiscovery(relay_url=self.relay_url)
            try:
                await self.connector.connect(self.relay_url, on_message=self._on_relay_message)
                logger.info("BFP connected to relay at %s", self.relay_url)
            except Exception as e:
                logger.warning("BFP relay connection failed (non-fatal): %s", e)

        self._running = True
        logger.info("BFP Agent started — DID: %s", self.did)

    async def stop(self):
        """Shut down BFP servers gracefully."""
        if self.connector:
            await self.connector.disconnect()
        if self.bfp_server:
            await self.bfp_server.stop()
        if hasattr(self, 'a2a_server') and self.a2a_server:
            import threading
            threading.Thread(target=self.a2a_server.shutdown, daemon=True).start()
        if self._relay_instance:
            try:
                self._relay_instance.stop()
            except Exception:
                pass
        self._running = False
        logger.info("BFP Agent stopped")

    def set_task_handler(self, handler):
        """Register an async handler for incoming delegated tasks.

        The handler receives (source_did, task_data) and should return
        the result string. This is called by the engine to route tasks
        through OpenCode for real processing.
        """
        _set_task_handler(handler)

    def list_tasks(self) -> list[dict]:
        return list_tasks()

    def get_task(self, task_id: str) -> Optional[dict]:
        return get_task(task_id)

    async def _on_relay_message(self, from_did: str, task_id: str, result: str):
        logger.info("BFP relay message from %s — task %s: %s", from_did, task_id, result[:100])

    def get_status(self) -> dict:
        """Return BFP status for /bfp status command."""
        return {
            "did": self.did,
            "running": self._running,
            "connected_to_relay": self.connector.is_connected,
            "relay_url": self.relay_url,
            "bfp_port": self.bfp_server.get_port() if self.bfp_server else None,
            "capabilities": [c["name"] for c in self.card.get("capabilities", [])],
        }

    async def find_agents(self, capability: str = None) -> list[dict]:
        if not self.connector.is_connected:
            raise RuntimeError("Not connected to relay — BFP relay required for discovery")
        return await self.connector.find_agents(capability)

    async def delegate(self, target_did: str, task_data: dict) -> dict:
        if not self.connector.is_connected:
            raise RuntimeError("Not connected to relay — BFP relay required for delegation")
        return await self.connector.delegate(target_did, task_data)

    async def talk(self, target_did: str, message: str) -> str:
        if not self.connector.is_connected:
            raise RuntimeError("Not connected to relay — BFP relay required for messaging")
        return await self.connector.talk(target_did, message)
