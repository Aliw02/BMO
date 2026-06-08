import logging
import os
from typing import Callable, Optional

from core.worker_subprocess import AsyncSubprocessWorker
from core.worker_multiproc import MultiprocessWorker

logger = logging.getLogger(__name__)


class WorkerManager:
    """Unified facade over both worker backends.
    Selects backend via env WORKER_BACKEND (subprocess|multiproc).
    """

    def __init__(self, backend: Optional[str] = None):
        if backend is None:
            backend = os.getenv("WORKER_BACKEND", "subprocess")
        self.backend = backend.lower()

        if self.backend == "subprocess":
            self._worker = AsyncSubprocessWorker()
        elif self.backend == "multiproc":
            self._worker = MultiprocessWorker()
        else:
            raise ValueError(f"Unknown worker backend: {backend}")

        logger.info(f"WorkerManager using backend: {self.backend}")

    async def start(self):
        await self._worker.start()

    async def send_query(
        self,
        session_id: str,
        payload: dict,
        base_url: str,
        poll_interval: float = 2.0,
        poll_timeout: float = 600.0,
        callback: Optional[Callable] = None,
    ) -> str:
        return await self._worker.send_query(
            session_id=session_id,
            payload=payload,
            base_url=base_url,
            poll_interval=poll_interval,
            poll_timeout=poll_timeout,
            callback=callback,
        )

    async def connect(self, base_url: str):
        await self._worker.connect(base_url)

    async def shutdown(self):
        await self._worker.shutdown()

    async def cancel_current(self):
        """Cancel the in-flight request by terminating the underlying worker.

        Delegates to the active backend (subprocess or multiproc). Called by
        the CLI SIGINT handler so Ctrl+C interrupts the actual model request,
        not just the asyncio task wrapping it.
        """
        try:
            await self._worker.cancel_current()
        except Exception as e:
            logger.warning("WorkerManager.cancel_current error: %s", e)

    @property
    def is_alive(self) -> bool:
        return self._worker.is_alive
