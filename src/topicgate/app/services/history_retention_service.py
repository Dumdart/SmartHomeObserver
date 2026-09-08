import asyncio
from dataclasses import asdict
from datetime import datetime, timezone
from uuid import UUID

from topicgate.app.services.service_item import ServiceItem
from topicgate.core.interfaces.history_retention_store import HistoryRetentionStore
from topicgate.core.models.history_retention import HistoryRetentionPolicy, HistoryUsage


class HistoryRetentionService(ServiceItem):
    def __init__(self, store: HistoryRetentionStore) -> None:
        self._store = store
        self._task: asyncio.Task | None = None
        self._stop = asyncio.Event()
        self.last_error: str | None = None

    def get_policy(self) -> HistoryRetentionPolicy:
        return self._store.get_policy()

    def update_policy(self, policy: HistoryRetentionPolicy) -> None:
        self._store.set_policy(HistoryRetentionPolicy(**asdict(policy)))

    def usage(self, broker_id: UUID | None = None) -> HistoryUsage:
        return self._store.usage(broker_id)

    async def start(self) -> None:
        if self._task is None:
            self._stop.clear()
            self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        self._stop.set()
        if self._task is not None:
            await self._task
            self._task = None

    async def _run(self) -> None:
        while not self._stop.is_set():
            delay = 60
            try:
                result = await asyncio.to_thread(self._store.prune, datetime.now(timezone.utc))
                policy = await asyncio.to_thread(self._store.get_policy)
                self.last_error = None
                delay = 1 if result.pending else policy.prune_interval_seconds
            except Exception:
                self.last_error = "History pruning failed; retention enforcement is pending."
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=delay)
            except asyncio.TimeoutError:
                pass
