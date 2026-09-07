import asyncio
import logging
import math
from collections.abc import Callable
from uuid import UUID

from topicgate.app.services.health_expectation_service import (
    DEFAULT_STALE_AFTER_SECONDS,
    HealthExpectationService,
)
from topicgate.app.services.service_item import ServiceItem
from topicgate.core.models.health import DiagnosticReport


logger = logging.getLogger(__name__)

BrokerIdsReader = Callable[[], tuple[UUID, ...]]

DEFAULT_HEALTH_EVALUATION_INTERVAL_SECONDS = 30.0


class BrokerHealthMonitor(ServiceItem):
    """Run broker-wide health evaluation independently of MQTT messages."""

    def __init__(
        self,
        evaluator: HealthExpectationService,
        broker_ids_reader: BrokerIdsReader,
        *,
        interval_seconds: float = DEFAULT_HEALTH_EVALUATION_INTERVAL_SECONDS,
        stale_after_seconds: float = DEFAULT_STALE_AFTER_SECONDS,
    ) -> None:
        if not math.isfinite(interval_seconds) or interval_seconds <= 0:
            raise ValueError("interval_seconds must be positive.")
        self._evaluator = evaluator
        self._broker_ids_reader = broker_ids_reader
        self._interval_seconds = float(interval_seconds)
        self._stale_after_seconds = stale_after_seconds
        self._stop_event = asyncio.Event()
        self._task: asyncio.Task[None] | None = None
        self._latest_reports: dict[UUID, DiagnosticReport] = {}

    @property
    def latest_reports(self) -> dict[UUID, DiagnosticReport]:
        return dict(self._latest_reports)

    async def start(self) -> None:
        if self._task is not None:
            return
        self._stop_event.clear()
        self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        if self._task is None:
            return
        self._stop_event.set()
        await self._task
        self._task = None

    def evaluate_now(self) -> tuple[DiagnosticReport, ...]:
        reports: list[DiagnosticReport] = []
        for broker_id in self._broker_ids_reader():
            try:
                report = self._evaluator.evaluate_broker(
                    broker_id,
                    stale_after_seconds=self._stale_after_seconds,
                )
            except Exception:
                logger.exception("Broker health evaluation failed for %s.", broker_id)
                continue
            self._latest_reports[broker_id] = report
            reports.append(report)
        return tuple(reports)

    async def _run(self) -> None:
        while True:
            try:
                await asyncio.wait_for(
                    self._stop_event.wait(),
                    timeout=self._interval_seconds,
                )
                return
            except TimeoutError:
                self.evaluate_now()
