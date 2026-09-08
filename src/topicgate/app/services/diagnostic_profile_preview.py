from __future__ import annotations

from dataclasses import dataclass

from topicgate.core.models.health import DiagnosticReport, ExpectationEvaluation, HealthExpectation, HealthStatus


@dataclass(frozen=True)
class DiagnosticPreviewFinding:
    """A preview finding paired with its draft-owned identity and presentation data."""

    expectation: HealthExpectation
    evaluation: ExpectationEvaluation

    @property
    def name(self) -> str:
        return self.expectation.name

    @property
    def target(self) -> object:
        return self.expectation.target


@dataclass(frozen=True)
class DiagnosticProfilePreview:
    report: DiagnosticReport
    findings: tuple[DiagnosticPreviewFinding, ...]

    @property
    def aggregate_status(self) -> HealthStatus:
        return self.report.aggregate_status
