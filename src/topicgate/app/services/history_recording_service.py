from collections import deque
from dataclasses import replace
from threading import Condition, Lock, Thread
from time import monotonic
from uuid import UUID, uuid4

from topicgate.core.interfaces.history_recording_store import HistoryRecordingStore
from topicgate.core.interfaces.observation_history import ObservationHistoryWriter
from topicgate.core.models.history_recording import HistoryRecordingStatus
from topicgate.core.models.observation_event import ObservationEvent


class HistoryRecordingService:
    """Bounded history admission; failures never invalidate the current view."""

    def __init__(
        self, writer: ObservationHistoryWriter, store: HistoryRecordingStore,
        *, max_events: int = 1000, max_bytes: int = 16 * 1024 * 1024,
    ) -> None:
        if any(type(value) is not int or value <= 0 for value in (max_events, max_bytes)):
            raise ValueError("History queue bounds must be positive integers.")
        self._writer = writer
        self._store = store
        self._max_events = max_events
        self._max_bytes = max_bytes
        self._condition = Condition()
        self._settings_lock = Lock()
        self._queue: deque[ObservationEvent] = deque()
        self._pending_bytes = 0
        self._pending_count = 0
        self._statuses: dict[UUID, HistoryRecordingStatus] = {}
        self._prior: dict[UUID, HistoryRecordingStatus] = {}
        self._sessions: dict[UUID, UUID] = {}
        self._dirty: set[UUID] = set()
        self._quiesced: set[UUID] = set()
        self._closing = False
        self._thread: Thread | None = None
        for broker_id in store.enabled_brokers():
            self._open_session(broker_id)

    def _open_session(self, broker_id: UUID) -> None:
        prior = self._store.status(broker_id)
        session_id = uuid4()
        status = self._store.begin_session(broker_id, session_id)
        with self._condition:
            self._prior[broker_id] = prior
            self._sessions[broker_id] = session_id
            self._statuses[broker_id] = status
            if self._thread is None:
                self._thread = Thread(target=self._run, name="observation-history-writer", daemon=True)
                self._thread.start()

    def set_enabled(self, broker_id: UUID, enabled: bool) -> None:
        if type(enabled) is not bool:
            raise ValueError("History recording enabled must be a boolean.")
        with self._settings_lock:
            with self._condition:
                if self._closing:
                    raise RuntimeError("History recorder is closed.")
                self._quiesced.add(broker_id)
            try:
                self.flush(broker_id)
                self._store.set_enabled(broker_id, enabled)
                if enabled and broker_id not in self._statuses:
                    self._open_session(broker_id)
                with self._condition:
                    if broker_id in self._statuses:
                        self._statuses[broker_id] = replace(self._statuses[broker_id], enabled=enabled)
            finally:
                with self._condition:
                    self._quiesced.discard(broker_id)

    def record(self, event: ObservationEvent) -> None:
        with self._condition:
            status = self._statuses.get(event.broker_id)
            if status is None or not status.enabled:
                return
            if (self._closing or event.broker_id in self._quiesced
                    or self._pending_count >= self._max_events
                    or self._pending_bytes + len(event.payload) > self._max_bytes):
                self._statuses[event.broker_id] = replace(status, dropped=status.dropped + 1)
            else:
                self._queue.append(event)
                self._pending_count += 1
                self._pending_bytes += len(event.payload)
                self._statuses[event.broker_id] = replace(
                    status, admitted=status.admitted + 1, pending=status.pending + 1,
                )
            self._dirty.add(event.broker_id)
            self._condition.notify_all()

    def status(self, broker_id: UUID) -> HistoryRecordingStatus:
        with self._condition:
            current = self._statuses.get(broker_id)
            if current is not None:
                prior = self._prior[broker_id]
                return replace(current, admitted=current.admitted + prior.admitted,
                               committed=current.committed + prior.committed,
                               dropped=current.dropped + prior.dropped,
                               failed=current.failed + prior.failed)
        return self._store.status(broker_id)

    def flush(self, broker_id: UUID | None = None, timeout: float = 10.0) -> None:
        deadline = monotonic() + timeout
        with self._condition:
            while any(status.pending for owner, status in self._statuses.items()
                      if broker_id is None or owner == broker_id):
                remaining = deadline - monotonic()
                if remaining <= 0:
                    raise TimeoutError("History drain timed out; accepted history may be incomplete.")
                self._condition.wait(remaining)

    def quiesce_broker(self, broker_id: UUID) -> None:
        with self._condition:
            self._quiesced.add(broker_id)
            status = self._statuses.get(broker_id)
            failed_before = 0 if status is None else status.failed
        try:
            self.flush(broker_id)
            with self._condition:
                status = self._statuses.get(broker_id)
                if status is not None and status.failed > failed_before:
                    raise RuntimeError("History drain was incomplete; broker deletion was aborted.")
        except Exception:
            self.resume_broker(broker_id)
            raise

    def resume_broker(self, broker_id: UUID) -> None:
        with self._condition:
            self._quiesced.discard(broker_id)

    def forget_broker(self, broker_id: UUID) -> None:
        with self._condition:
            self._statuses.pop(broker_id, None)
            self._prior.pop(broker_id, None)
            self._sessions.pop(broker_id, None)
            self._dirty.discard(broker_id)

    def close(self, timeout: float = 10.0) -> None:
        with self._settings_lock:
            with self._condition:
                self._closing = True
                self._condition.notify_all()
            if self._thread is not None:
                self._thread.join(timeout)
                if self._thread.is_alive():
                    raise TimeoutError("History shutdown timed out; accepted history may be incomplete.")
            if any(s.failed or s.checkpoint_failed for s in self._statuses.values()):
                raise RuntimeError("History recording was incomplete; inspect failed/checkpoint counters.")

    @property
    def is_alive(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def _checkpoint(self, broker_ids: set[UUID], *, closed: bool = False) -> None:
        for broker_id in broker_ids:
            with self._condition:
                status = self._statuses.get(broker_id)
                session_id = self._sessions.get(broker_id)
            if status is None or session_id is None:
                continue
            try:
                self._store.checkpoint(session_id, status, closed=closed)
            except Exception:
                with self._condition:
                    if broker_id in self._statuses:
                        self._statuses[broker_id] = replace(
                            self._statuses[broker_id], checkpoint_failed=True,
                        )
            else:
                with self._condition:
                    if broker_id in self._statuses:
                        self._statuses[broker_id] = replace(
                            self._statuses[broker_id], checkpoint_failed=False,
                        )

    def _run(self) -> None:
        while True:
            with self._condition:
                self._condition.wait_for(lambda: self._queue or self._dirty or self._closing)
                batch = tuple(self._queue.popleft() for _ in range(min(100, len(self._queue))))
                dirty, self._dirty = self._dirty, set()
                closing = self._closing and not batch
            if closing:
                self._checkpoint(set(self._sessions), closed=True)
                return
            if batch:
                failed = False
                try:
                    self._writer.append(batch)
                except Exception:
                    failed = True
                with self._condition:
                    for event in batch:
                        status = self._statuses[event.broker_id]
                        self._statuses[event.broker_id] = replace(
                            status, pending=status.pending - 1,
                            failed=status.failed + int(failed),
                            committed=status.committed + int(not failed),
                        )
                        self._pending_count -= 1
                        self._pending_bytes -= len(event.payload)
                        dirty.add(event.broker_id)
                    self._condition.notify_all()
            self._checkpoint(dirty)
