from dataclasses import dataclass
from datetime import datetime, timezone

from topicgate.app.models.expectation_health_report import (
    ExpectationHealthFinding,
    ExpectationHealthReport,
)
from topicgate.core.models.health import HealthExpectation, HealthStatus


@dataclass(frozen=True)
class HealthSummary:
    label: str
    tone: str
    counts: str
    explanation: str


@dataclass(frozen=True)
class TopicHealthSummary:
    label: str
    tone: str
    detail: str
    evidence: str


def broker_health_summary(
    report: ExpectationHealthReport | None,
    expectations: tuple[HealthExpectation, ...],
    *,
    available: bool,
    now: datetime | None = None,
) -> HealthSummary:
    if not available:
        return HealthSummary(
            "Unavailable",
            "neutral",
            "",
            "Health services are unavailable in this desktop session.",
        )
    if report is None:
        return HealthSummary(
            "Not evaluated",
            "neutral",
            "",
            "Waiting for the first health evaluation.",
        )

    enabled = tuple(item for item in expectations if item.enabled)
    findings = tuple(report.observation_findings) + tuple(report.expectation_findings)
    failed = sum(item.status is HealthStatus.PROBLEM for item in findings)
    unknown = sum(item.status is HealthStatus.UNKNOWN for item in findings)
    counts = _counts_label(failed, unknown, report.omitted_count)
    freshness = _freshness_label(report.evaluated_at, now)

    if not enabled and report.aggregate_status is HealthStatus.HEALTHY:
        return HealthSummary(
            "No enabled expectations",
            "neutral",
            counts,
            f"Broker checks were evaluated {freshness}; no expectation rules are enabled.",
        )
    if report.aggregate_status is HealthStatus.PROBLEM:
        label, tone = "Failed", "problem"
    elif report.aggregate_status is HealthStatus.UNKNOWN:
        label, tone = "Unknown", "warning"
    else:
        label, tone = "Healthy", "success"

    limitations = []
    if not report.evidence_complete:
        limitations.append("evidence is limited or observations are stale")
    if report.omitted_count:
        limitations.append(
            f"{report.omitted_count} expectation result(s) are not shown"
        )
    explanation = f"Evaluated {freshness}."
    if not enabled:
        explanation += (
            " No expectation rules are enabled; this status comes from broker checks."
        )
    if limitations:
        explanation += " " + "; ".join(limitations).capitalize() + "."
    if report.active_failure_count and label != "Failed":
        explanation += (
            f" {report.active_failure_count} earlier failure episode(s) remain "
            "unresolved; that does not mean checks are currently failing."
        )
    return HealthSummary(label, tone, counts, explanation)


def topic_health_summary(
    topic: str,
    report: ExpectationHealthReport | None,
    expectations: tuple[HealthExpectation, ...],
    *,
    available: bool,
) -> TopicHealthSummary:
    if not topic or "+" in topic or "#" in topic:
        return TopicHealthSummary("", "neutral", "", "")
    if not available:
        return TopicHealthSummary(
            "Unavailable",
            "neutral",
            "Health services are unavailable.",
            "",
        )
    enabled = tuple(item for item in expectations if item.enabled)
    if not enabled:
        return TopicHealthSummary(
            "No expectations",
            "neutral",
            "No enabled expectations are configured for this topic.",
            "",
        )
    if report is None:
        return TopicHealthSummary(
            "Not evaluated",
            "neutral",
            "These expectations have not been evaluated yet.",
            "",
        )

    enabled_ids = {item.expectation_id for item in enabled}
    findings = tuple(
        item
        for item in report.expectation_findings
        if item.expectation_id in enabled_ids
    )
    if len({item.expectation_id for item in findings}) < len(enabled_ids):
        return TopicHealthSummary(
            "Unknown",
            "warning",
            "The bounded health report omitted one or more results for this topic.",
            _evidence_label(findings, report.evaluated_at),
        )

    failed = sum(item.status is HealthStatus.PROBLEM for item in findings)
    unknown = sum(item.status is HealthStatus.UNKNOWN for item in findings)
    if failed:
        label, tone = "Failed", "problem"
    elif unknown:
        label, tone = "Unknown", "warning"
    else:
        label, tone = "Healthy", "success"
    counts = []
    if failed > 1:
        counts.append(f"{failed} failed")
    if unknown > 1 or (failed and unknown):
        counts.append(f"{unknown} unknown")
    count_label = " · ".join(counts)
    return TopicHealthSummary(
        f"{label}{f' · {count_label}' if count_label else ''}",
        tone,
        f"{len(findings)} enabled expectation(s) evaluated.",
        _evidence_label(findings, report.evaluated_at),
    )


def finding_result_label(
    expectation: HealthExpectation,
    report: ExpectationHealthReport | None,
) -> str:
    if not expectation.enabled:
        return "Not evaluated"
    finding = _finding_for(expectation, report)
    if finding is None:
        return "Not evaluated" if report is None else "Unknown"
    return {
        HealthStatus.HEALTHY: "Healthy",
        HealthStatus.PROBLEM: "Failed",
        HealthStatus.UNKNOWN: "Unknown",
    }[finding.status]


def _finding_for(
    expectation: HealthExpectation,
    report: ExpectationHealthReport | None,
) -> ExpectationHealthFinding | None:
    if report is None:
        return None
    return next(
        (
            item
            for item in report.expectation_findings
            if item.expectation_id == expectation.expectation_id
        ),
        None,
    )


def _counts_label(failed: int, unknown: int, omitted: int) -> str:
    parts = []
    if failed:
        parts.append(f"Failed {failed}")
    if unknown:
        parts.append(f"Unknown {unknown}")
    if omitted:
        parts.append(f"+{omitted} not shown")
    return " · ".join(parts)


def _freshness_label(evaluated_at: datetime, now: datetime | None) -> str:
    current = now or datetime.now(timezone.utc)
    value = evaluated_at
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    seconds = max(0, int((current - value.astimezone(timezone.utc)).total_seconds()))
    if seconds < 10:
        return "just now"
    if seconds < 60:
        return f"{seconds} seconds ago"
    minutes = seconds // 60
    if minutes < 60:
        return f"{minutes} minute(s) ago"
    return value.astimezone().isoformat(timespec="seconds")


def _evidence_label(
    findings: tuple[ExpectationHealthFinding, ...],
    evaluated_at: datetime,
) -> str:
    evidence = [
        f"{item.name or item.expectation_id}: "
        f"{item.evidence_summary or 'Evidence unavailable'}"
        for item in findings
    ]
    evaluated = evaluated_at.astimezone().isoformat(timespec="seconds")
    return "\n".join((*evidence, f"Evaluated: {evaluated}"))
