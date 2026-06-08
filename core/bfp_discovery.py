"""BFP Discovery — Cloudflare Workers registry client.

REST client for registering, resolving, and finding agents by capability
through the BFP Registry (Cloudflare Workers KV).

Registry URL: https://bfp-registry.aliwey.workers.dev
              (override via BFP_REGISTRY_URL env var or bmo_init config)
"""

import logging
import os
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

# Default public registry — hosted on Cloudflare Workers (free tier)
DEFAULT_REGISTRY_URL = "https://bfp-registry.aliwey.workers.dev"


class BFPDiscovery:
    """Client for the BFP Registry (Cloudflare Workers KV)."""

    def __init__(self, relay_url: str = None):
        # relay_url kept for backward compat but we use the registry URL
        self.relay_url = relay_url  # local relay WebSocket URL (if any)
        self.registry_url = (
            os.getenv("BFP_REGISTRY_URL") or DEFAULT_REGISTRY_URL
        ).rstrip("/")
        self._http = httpx.AsyncClient(timeout=10.0)

    # ── Registry (Cloudflare Workers) ─────────────────────────────────────────

    async def register(
        self,
        did: str,
        endpoint: str,
        caps: list,
        public_key: str = None,     # unused by Workers registry, kept for compat
        name: str = None,           # unused by Workers registry, kept for compat
    ) -> dict:
        resp = await self._http.post(f"{self.registry_url}/register", json={
            "did": did,
            "endpoint": endpoint,
            "caps": caps,
        })
        resp.raise_for_status()
        return resp.json()

    async def unregister(self, did: str) -> None:
        try:
            resp = await self._http.post(f"{self.registry_url}/unregister", json={"did": did})
            resp.raise_for_status()
        except Exception as e:
            logger.warning(f"BFP unregister failed: {e}")

    async def lookup(self, did: str) -> Optional[dict]:
        """Resolve a DID to its current endpoint and capabilities."""
        try:
            resp = await self._http.get(f"{self.registry_url}/lookup", params={"did": did})
            if resp.status_code == 404:
                return None
            resp.raise_for_status()
            return resp.json()
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                return None
            raise

    # kept for backward compat (old code calls resolve(), new calls lookup())
    async def resolve(self, did: str) -> Optional[dict]:
        return await self.lookup(did)

    async def find_agents(self, capability: str = None) -> list[dict]:
        """Find online agents by capability (or list all if no capability given)."""
        params = {"capability": capability} if capability else {}
        resp = await self._http.get(f"{self.registry_url}/list", params=params)
        resp.raise_for_status()
        data = resp.json()
        # Registry returns a list; old relay returned {"agents": [...]}
        if isinstance(data, list):
            return data
        return data.get("agents", [])

    # kept for backward compat (old code calls find_by_capability)
    async def find_by_capability(self, capability: str) -> list[dict]:
        return await self.find_agents(capability)

    async def list_all(self) -> list[dict]:
        resp = await self._http.get(f"{self.registry_url}/list")
        resp.raise_for_status()
        result = resp.json()
        if isinstance(result, list):
            return result
        return result.get("agents", [])

    async def health(self) -> dict:
        """Check registry health."""
        resp = await self._http.get(f"{self.registry_url}/health")
        resp.raise_for_status()
        return resp.json()

    async def close(self):
        await self._http.aclose()
