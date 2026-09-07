"""Bounded verification on an existing MQTT connection."""

import asyncio
from collections.abc import Awaitable, Callable
from copy import deepcopy
from datetime import datetime, timezone
import math
import time
from uuid import UUID

from topicgate.app.services.expectation_management_service import (
    ExpectationManagementService,
)
from topicgate.app.services.control_operation_service import ControlOperationConflict
from topicgate.app.services.health_expectation_service import (
    DEFAULT_STALE_AFTER_SECONDS,
    HealthExpectationService,
)
from topicgate.app.services.health_query_service import HealthQueryService
from topicgate.app.topicgate_runtime import TopicGateRuntime
from topicgate.core.models.connection_status import ConnectionStatus
from topicgate.core.models.health import HealthStatus


class BrokerHealthWaitService:
    def __init__(
        self,
        runtime: TopicGateRuntime,
        management: ExpectationManagementService,
        evaluator: HealthExpectationService,
        query: HealthQueryService,
        *,
        monotonic: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        utc_now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ) -> None:
        self._runtime = runtime
        self._management = management
        self._evaluator = evaluator
        self._query = query
        self._monotonic = monotonic
        self._sleep = sleep
        self._utc_now = utc_now

    async def wait(
        self,
        broker_id: UUID,
        *,
        required_expectation_ids: tuple[UUID, ...] | None = None,
        timeout_seconds: float = 30,
        poll_interval_seconds: float = 1,
        stable_for_seconds: float = 0,
        stale_after_seconds: float = DEFAULT_STALE_AFTER_SECONDS,
        limit: int = 50,
    ) -> dict:
        for name, value, minimum, maximum in (
            ("timeout_seconds", timeout_seconds, 0.1, 60),
            ("poll_interval_seconds", poll_interval_seconds, 0.1, 5),
            ("stable_for_seconds", stable_for_seconds, 0, timeout_seconds),
            ("stale_after_seconds", stale_after_seconds, 0.000001, float("inf")),
        ):
            if (
                isinstance(value, bool)
                or not math.isfinite(value)
                or not minimum <= value <= maximum
            ):
                raise ValueError(f"Invalid {name} bounds.")
        if poll_interval_seconds > timeout_seconds:
            raise ValueError("poll_interval_seconds must not exceed timeout_seconds.")
        if type(limit) is not int or not 1 <= limit <= 200:
            raise ValueError("limit must be an integer from 1 through 200.")
        if required_expectation_ids is not None and (
            not required_expectation_ids
            or len(set(required_expectation_ids)) != len(required_expectation_ids)
        ):
            raise ValueError("Required expectation IDs must be nonempty and unique.")

        with self._runtime.control_operation("wait for broker health"):
            if self._runtime.active_broker.id != broker_id:
                raise ValueError("Activate the requested broker before waiting.")
            if (
                self._runtime.get_connection_status(broker_id)
                != ConnectionStatus.CONNECTED
            ):
                raise ValueError(
                    "The requested active broker must be connected before waiting."
                )
            definitions = self._definitions(broker_id)
            enabled = {
                item.expectation_id: item for item in definitions if item.enabled
            }
            required = (
                set(enabled)
                if required_expectation_ids is None
                else set(required_expectation_ids)
            )
            if not required or not required <= enabled.keys():
                raise ValueError(
                    "Configure enabled expectations for this broker; required IDs must exist and be enabled."
                )
            subscriptions = deepcopy(self._runtime.list_subscriptions(broker_id))
            profile = deepcopy(self._runtime.get_broker(broker_id))
            start = self._monotonic()
            started_at = self._utc_now()
            deadline = start + timeout_seconds
            healthy_since = None
            count = 0
            report = None
            outcome = "timed_out"

            def check_deadline() -> None:
                if self._monotonic() > deadline:
                    raise TimeoutError("Health evaluation deadline reached.")

            while True:
                await self._sleep(0)
                try:
                    self._runtime.check_control_ownership()
                except ControlOperationConflict:
                    outcome = "configuration_changed"
                    break
                changed = (
                    self._runtime.active_broker.id != broker_id
                    or self._definitions(broker_id) != definitions
                    or self._runtime.list_subscriptions(broker_id) != subscriptions
                    or self._runtime.get_broker(broker_id) != profile
                )
                if changed:
                    outcome = "configuration_changed"
                    break
                count += 1
                try:
                    check_deadline()
                    report = self._evaluator.evaluate_broker(
                        broker_id,
                        stale_after_seconds=stale_after_seconds,
                        evaluated_at=self._utc_now(),
                        deadline_check=check_deadline,
                    )
                    check_deadline()
                except TimeoutError:
                    break
                now = self._monotonic()
                try:
                    self._runtime.check_control_ownership()
                except ControlOperationConflict:
                    outcome = "configuration_changed"
                    break
                if (
                    self._definitions(broker_id) != definitions
                    or self._runtime.active_broker.id != broker_id
                ):
                    outcome = "configuration_changed"
                    break
                if (
                    self._runtime.get_connection_status(broker_id)
                    != ConnectionStatus.CONNECTED
                ):
                    outcome = "disconnected"
                    break
                findings = {item.expectation_id: item for item in report.topic_findings}
                satisfied = (
                    report.observation_health.status == HealthStatus.HEALTHY
                    and report.evidence_complete
                    and all(
                        identity in findings
                        and findings[identity].expectation_revision
                        == enabled[identity].revision
                        and findings[identity].status == HealthStatus.HEALTHY
                        and findings[identity].evidence_complete
                        for identity in required
                    )
                )
                healthy_since = (
                    (now if healthy_since is None else healthy_since)
                    if satisfied
                    else None
                )
                if (
                    satisfied
                    and now <= deadline
                    and now - healthy_since >= stable_for_seconds
                ):
                    outcome = "satisfied"
                    break
                if now >= deadline:
                    break
                await self._sleep(min(poll_interval_seconds, deadline - now))
            final_report = (
                None
                if report is None
                else self._query.present_report(report, limit=limit)
            )
            return {
                "outcome": outcome,
                "broker_id": broker_id,
                "broker_name": profile.name,
                "started_at": started_at,
                "ended_at": self._utc_now(),
                "elapsed_seconds": self._monotonic() - start,
                "evaluation_count": count,
                "scope": "whole_broker"
                if required_expectation_ids is None
                else "required_subset",
                "required_expectations": [
                    {"expectation_id": identity, "revision": enabled[identity].revision}
                    for identity in sorted(required, key=str)
                ],
                "enabled_count": len(enabled),
                "evaluated_count": 0 if report is None else len(report.topic_findings),
                "final_report": final_report,
                "domain_status": HealthStatus.UNKNOWN
                if report is None
                else report.aggregate_status,
                "evidence_complete": False
                if report is None
                else report.evidence_complete,
                "reason": {
                    "satisfied": "Required expectations satisfied for the requested stability interval.",
                    "timed_out": "Success criteria were not satisfied before the shared deadline.",
                    "disconnected": "Broker disconnected; an intended reconnection is required before retrying.",
                    "configuration_changed": "Configuration or control ownership changed; inspect and resolve before retrying.",
                }[outcome],
                "connection_side_effects": [],
                "selected_broker_left_active": self._runtime.active_broker.id
                == broker_id,
                "evidence_note": (
                    "Final report describes its evaluation timestamp and scope. "
                    "Absence means not observed within TopicGate's observation scope. "
                    "Received time is observation time; retained delivery does not prove publisher liveness."
                ),
            }

    def _definitions(self, broker_id: UUID) -> tuple:
        return deepcopy(
            tuple(
                sorted(
                    self._management.list_expectations(broker_id),
                    key=lambda item: item.expectation_id.hex,
                )
            )
        )
