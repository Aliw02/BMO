"""BFP Discovery — relay directory client.

REST client for registering, resolving, and finding agents by capability
through a BFP relay server.

Default relay: http://127.0.0.1:9753 (auto-started by BMO)
Override via BFP_RELAY_URL or BFP_REGISTRY_URL env var.
"""

import logging
import os
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

# Default is the local relay (auto-started by BMO on port 9753).
# Override via BFP_REGISTRY_URL for a custom registry endpoint.
DEFAULT_RELAY_URL = "http://127.0.0.1:9753"


class BFPDiscovery:
    """Client for the BFP relay directory (REST API)."""

    def __init__(self, relay_url: str = None):
        self.base_url = (
            relay_url or os.getenv("BFP_RELAY_URL") or
            os.getenv("BFP_REGISTRY_URL") or DEFAULT_RELAY_URL
        ).rstrip("/")
        self._http = httpx.AsyncClient(timeout=5.0)

    # ── Directory REST API ─────────────────────────────────────────────────

    async def register(
        self,
        did: str,
        endpoint: str,
        capabilities: list = None,
        public_key: str = None,
        name: str = None,
    ) -> dict:
        resp = await self._http.post(f"{self.base_url}/register", json={
            "did": did,
            "endpoint": endpoint,
            "capabilities": capabilities or [],
            "publicKey": public_key or "",
            "name": name or "",
        })
        resp.raise_for_status()
        return resp.json()

    async def unregister(self, did: str) -> None:
        try:
            resp = await self._http.delete(f"{self.base_url}/unregister/{did}")
            resp.raise_for_status()
        except Exception as e:
            logger.warning("BFP unregister failed: %s", e)

    async def lookup(self, did: str) -> Optional[dict]:
        """Resolve a DID to its current endpoint and capabilities."""
        try:
            resp = await self._http.get(f"{self.base_url}/resolve/{did}")
            if resp.status_code == 404:
                return None
            resp.raise_for_status()
            return resp.json()
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                return None
            raise

    async def resolve(self, did: str) -> Optional[dict]:
        return await self.lookup(did)

    async def find_agents(self, capability: str = None) -> list[dict]:
        """Find online agents by capability (or list all if no capability given)."""
        params = {"capability": capability} if capability else {}
        resp = await self._http.get(f"{self.base_url}/list", params=params)
        resp.raise_for_status()
        data = resp.json()
        if isinstance(data, list):
            return data
        return data.get("agents", [])

    async def find_by_capability(self, capability: str) -> list[dict]:
        return await self.find_agents(capability)

    async def list_all(self) -> list[dict]:
        return await self.find_agents()

    async def health(self) -> dict:
        """Check relay health."""
        resp = await self._http.get(f"{self.base_url}/health")
        resp.raise_for_status()
        return resp.json()

    async def close(self):
        await self._http.aclose()
