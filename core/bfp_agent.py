"""
BFP Agent — orchestrates BFP identity, card, transport, discovery, and relay communication.
Started automatically when BMO boots.
"""

import asyncio
import logging
from typing import Optional

from core.bfp_identity import get_did, sign, verify
from core.bfp_agent_card import get_signed_card, serve_agent_card, generate_agent_card
from core.bfp_transport import BFPServer, BFPClient
from core.bfp_a2a_bridge import get_a2a_agent_card, start_a2a_endpoint
from core.bfp_connector import BFPConnector
from core.bfp_discovery import BFPDiscovery

logger = logging.getLogger(__name__)


class BFPAgent:
    """Manages BFP lifecycle within BMO."""

    def __init__(self):
        self.did = get_did()
        self.card = get_signed_card()
        self.bfp_server: Optional[BFPServer] = None
        self.a2a_server = None
        self.connector = BFPConnector()
        self.discovery: Optional[BFPDiscovery] = None
        self.relay_url: Optional[str] = None
        self._running = False

    @property
    def is_running(self):
        return self._running

    async def start(self, bfp_port: int = 8765, a2a_port: int = 8766, relay_url: str = None):
        """Start BFP server, A2A bridge, and optionally connect to relay."""
        self.bfp_server = BFPServer(port=bfp_port)
        # BFPServer.start() is synchronous — it spawns a daemon thread internally
        self.bfp_server.start()
        try:
            self.a2a_server = start_a2a_endpoint(port=a2a_port)
            if asyncio.iscoroutine(self.a2a_server):
                self.a2a_server = await self.a2a_server
        except Exception as e:
            logger.warning("A2A endpoint start failed (non-fatal): %s", e)
            self.a2a_server = None
        if relay_url:
            self.relay_url = relay_url
            self.discovery = BFPDiscovery(relay_url)
            try:
                await self.connector.connect(relay_url, on_message=self._on_relay_message)
            except Exception as e:
                logger.warning("BFP relay not available (non-fatal): %s", e)
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
        self._running = False
        logger.info("BFP Agent stopped")

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
            raise RuntimeError("Not connected to relay")
        return await self.connector.delegate(target_did, task_data)

    async def talk(self, target_did: str, message: str) -> str:
        if not self.connector.is_connected:
            raise RuntimeError("Not connected to relay")
        return await self.connector.talk(target_did, message)
