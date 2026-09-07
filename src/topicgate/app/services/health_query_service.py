from datetime import datetime
from uuid import UUID

from topicgate.app.models.expectation_health_report import (
    ExpectationHealthFinding,
    ExpectationHealthReport,
    FailureHistoryItem,
    FailureHistoryResult,
)
from topicgate.app.services.expectation_management_service import (
    ExpectationManagementService,
)
from topicgate.app.services.failure_history_service import FailureHistoryService
from topicgate.app.services.health_expectation_service import (
    DEFAULT_STALE_AFTER_SECONDS,
    HealthExpectationService,
)
from topicgate.app.services.health_report_service import HealthReportService
from topicgate.core.models.health import (
    BrokerTarget,
    ExpectationEvaluation,
    ExpectationFailure,
    HealthExpectation,
    HealthStatus,
    TopicTarget,
)


DEFAULT_HEALTH_RESULT_LIMIT = 50
MAX_HEALTH_RESULT_LIMIT = 200
DEFAULT_HEALTH_EVIDENCE_LIMIT = 500


class HealthQueryService:
    """Build bounded health results for presentation adapters."""

    def __init__(
        self,
        evaluator: HealthExpectationService,
        expectation_management: ExpectationManagementService,
        failure_history: FailureHistoryService,
        health_report: HealthReportService,
    ) -> None:
        self._evaluator = evaluator
        self._expectation_management = expectation_management
        self._failure_history = failure_history
        self._health_report = health_report

    def get_health_report(
        self,
        broker_id: UUID,
        *,
        stale_after_seconds: float = DEFAULT_STALE_AFTER_SECONDS,
        limit: int = DEFAULT_HEALTH_RESULT_LIMIT,
    ) -> ExpectationHealthReport:
        limit = _validate_limit(limit)
        report = self._evaluator.evaluate_broker(
            broker_id,
            stale_after_seconds=stale_after_seconds,
        )
        expectations = {
            item.expectation_id: item
            for item in self._expectation_management.list_expectations(broker_id)
        }
        findings = tuple(
            sorted(
                (
                    self._finding(
                        evaluation,
                        expectations.get(evaluation.expectation_id),
                    )
                    for evaluation in report.topic_findings
                ),
                key=_finding_sort_key,
            )
        )
        active_failure_count = sum(
            1
            for failure in self._health_report.get_active_failures()
            if self._health_report.broker_identity(failure) == broker_id
        )
        return ExpectationHealthReport(
            broker_id=report.broker_id,
            evaluated_at=report.evaluated_at,
            aggregate_status=report.aggregate_status,
            evidence_complete=report.evidence_complete,
            observation_status=report.observation_health.status,
            observation_findings=report.observation_health.findings,
            expectation_findings=findings[:limit],
            active_failure_count=active_failure_count,
            returned_count=min(len(findings), limit),
            omitted_count=max(0, len(findings) - limit),
        )

    def query_failure_history(
        self,
        *,
        broker_id: UUID,
        topic: str | None = None,
        status: str = "all",
        after: datetime | None = None,
        before: datetime | None = None,
        cursor: int | None = None,
        limit: int = DEFAULT_HEALTH_RESULT_LIMIT,
    ) -> FailureHistoryResult:
        limit = _validate_limit(limit)
        normalized_status = status.strip().casefold()
        if normalized_status not in {"all", "active", "recovered"}:
            raise ValueError("status must be 'all', 'active', or 'recovered'")
        failures = self._failure_history.filter(
            broker_id,
            topic,
            (after, before),
        )
        if normalized_status == "active":
            failures = tuple(item for item in failures if item.recovered_at is None)
        elif normalized_status == "recovered":
            failures = tuple(item for item in failures if item.recovered_at is not None)
        page = self._failure_history.get_cursor_pagination(
            cursor,
            limit,
            failures,
        )
        items = tuple(self._history_item(item) for item in page.items)
        return FailureHistoryResult(items, page.next_cursor, len(items))

    def delete_failure_history(self, failure_id: UUID) -> None:
        """Permanently delete one failure episode."""
        self._failure_history.delete(failure_id)

    def _finding(
        self,
        evaluation: ExpectationEvaluation,
        expectation: HealthExpectation | None,
    ) -> ExpectationHealthFinding:
        evidence, truncated = _bounded(evaluation.evidence_summary)
        return ExpectationHealthFinding(
            expectation_id=evaluation.expectation_id,
            expectation_revision=evaluation.expectation_revision,
            name="" if expectation is None else expectation.name,
            description="" if expectation is None else expectation.description,
            target_kind=_target_kind(expectation),
            target=_target_identity(expectation),
            status=evaluation.status,
            failure_code=evaluation.failure_code,
            evidence_summary=evidence,
            evidence_complete=evaluation.evidence_complete,
            evidence_truncated=truncated,
        )

    def _history_item(
        self,
        failure: ExpectationFailure,
    ) -> FailureHistoryItem:
        evidence, truncated = _bounded(failure.evidence_summary)
        return FailureHistoryItem(
            failure_id=failure.failure_id,
            expectation_id=failure.expectation_id,
            broker_id=self._health_report.broker_identity(failure),
            target=self._health_report.target_identity(failure),
            first_failed_at=failure.first_failed_at,
            last_seen_at=failure.last_seen_at,
            recovered_at=failure.recovered_at,
            occurrence_count=failure.occurrence_count,
            expected_revision=failure.expected_revision,
            last_healthy_at=failure.last_healthy_at,
            failure_code=failure.failure_code,
            evidence_summary=evidence,
            evidence_truncated=truncated,
            evidence_limitations=self._health_report.get_evidence_limitations(
                failure
            ),
        )


def _validate_limit(value: int) -> int:
    if value <= 0 or value > MAX_HEALTH_RESULT_LIMIT:
        raise ValueError(
            f"limit must be between 1 and {MAX_HEALTH_RESULT_LIMIT}"
        )
    return value


def _bounded(value: str | None) -> tuple[str | None, bool]:
    if value is None:
        return None, False
    return value[:DEFAULT_HEALTH_EVIDENCE_LIMIT], (
        len(value) > DEFAULT_HEALTH_EVIDENCE_LIMIT
    )


def _target_kind(expectation: HealthExpectation | None) -> str:
    if expectation is None:
        return "unknown"
    if isinstance(expectation.target, TopicTarget):
        return "topic"
    if isinstance(expectation.target, BrokerTarget):
        return "broker"
    return "unknown"


def _target_identity(expectation: HealthExpectation | None) -> str:
    if expectation is None:
        return "unknown"
    if isinstance(expectation.target, TopicTarget):
        return expectation.target.topic
    if isinstance(expectation.target, BrokerTarget):
        return "broker"
    return "unknown"


def _finding_sort_key(
    finding: ExpectationHealthFinding,
) -> tuple[int, str, str]:
    status_order = {
        HealthStatus.PROBLEM: 0,
        HealthStatus.UNKNOWN: 1,
        HealthStatus.HEALTHY: 2,
    }
    return (
        status_order[finding.status],
        finding.name.casefold(),
        finding.expectation_id.hex,
    )
