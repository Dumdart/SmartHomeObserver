import math
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation

from topicgate.core.models.health.condition_result import ConditionResult
from topicgate.core.models.health.health_enums import HealthStatus

PayloadValue = bytes | str


@dataclass(frozen=True)
class ConditionEvaluationContext:
    payload: PayloadValue | None
    received_at: datetime | None
    evaluated_at: datetime

    @property
    def topic_exists(self) -> bool:
        return self.payload is not None


class Condition(ABC):
    def evaluate(self, context: ConditionEvaluationContext) -> ConditionResult:
        """Evaluate the condition using the available observation evidence."""
        if context.payload is None:
            return ConditionResult(
                status=HealthStatus.UNKNOWN,
                failure_code="TOPIC_NEVER_OBSERVED",
                evidence_summary="Topic has never been observed.",
                evidence_complete=False,
            )
        return self.handle_condition(context.payload)

    @abstractmethod
    def handle_condition(self, actual: PayloadValue) -> ConditionResult:
        """Evaluate a directly supplied payload value."""


@dataclass(frozen=True)
class EqualCondition(Condition):
    expected_value: PayloadValue

    @staticmethod
    def compare(actual: PayloadValue, expected: PayloadValue) -> bool:
        if type(actual) is not type(expected):
            raise TypeError("Actual and expected values must have the same type.")
        return actual == expected

    def handle_condition(self, actual: PayloadValue) -> ConditionResult:
        status = (
            HealthStatus.HEALTHY
            if self.compare(actual, self.expected_value)
            else HealthStatus.PROBLEM
        )

        return ConditionResult(
            status=status,
            evidence_complete=True,
            evidence_summary=(
                f"Expected value: {self.expected_value}, Actual value: {actual}"
            ),
            failure_code=(
                "EQUAL_CONDITION_FAILED"
                if status is HealthStatus.PROBLEM
                else None
            ),
        )


@dataclass(frozen=True)
class InRangeCondition(Condition):
    expected_values: tuple[PayloadValue, ...]

    def __post_init__(self) -> None:
        if not self.expected_values:
            raise ValueError("Expected values must not be empty.")

    @staticmethod
    def compare(
        actual: PayloadValue,
        expected: tuple[PayloadValue, ...],
    ) -> bool:
        if any(type(actual) is not type(value) for value in expected):
            raise TypeError(
                "Actual and expected values must have the same type."
            )

        return actual in expected

    def handle_condition(self, actual: PayloadValue) -> ConditionResult:
        status = (
            HealthStatus.HEALTHY
            if self.compare(actual, self.expected_values)
            else HealthStatus.PROBLEM
        )

        return ConditionResult(
            status=status,
            evidence_complete=True,
            evidence_summary=(
                f"Expected value to be one of {self.expected_values!r}, "
                f"actual value: {actual!r}."
            ),
            failure_code=(
                "INSIDE_RANGE_CONDITION_FAILED"
                if status is HealthStatus.PROBLEM
                else None
            ),
        )


@dataclass(frozen=True)
class OutSideCondition(Condition):
    expected_values: tuple[PayloadValue, ...]

    def __post_init__(self) -> None:
        if not self.expected_values:
            raise ValueError("Expected values must not be empty.")

    @staticmethod
    def compare(
        actual: PayloadValue,
        expected: tuple[PayloadValue, ...],
    ) -> bool:
        if any(type(actual) is not type(value) for value in expected):
            raise TypeError(
                "Actual and expected values must have the same type."
            )

        return actual not in expected

    def handle_condition(self, actual: PayloadValue) -> ConditionResult:
        status = (
            HealthStatus.HEALTHY
            if self.compare(actual, self.expected_values)
            else HealthStatus.PROBLEM
        )

        return ConditionResult(
            status=status,
            evidence_complete=True,
            evidence_summary=(
                f"Expected value not to be one of {self.expected_values!r}, "
                f"actual value: {actual!r}."
            ),
            failure_code=(
                "OUTSIDE_RANGE_CONDITION_FAILED"
                if status is HealthStatus.PROBLEM
                else None
            ),
        )


@dataclass(frozen=True)
class NumericRangeCondition(Condition):
    minimum: Decimal
    maximum: Decimal

    def __post_init__(self) -> None:
        if not self.minimum.is_finite() or not self.maximum.is_finite():
            raise ValueError("Numeric range bounds must be finite.")
        if self.minimum > self.maximum:
            raise ValueError("Numeric range minimum must not exceed maximum.")

    def handle_condition(self, actual: PayloadValue) -> ConditionResult:
        try:
            value = _parse_decimal(actual)
        except (InvalidOperation, UnicodeDecodeError, ValueError):
            return ConditionResult(
                status=HealthStatus.PROBLEM,
                failure_code="NUMERIC_RANGE_CONDITION_FAILED",
                evidence_summary=(
                    f"Expected a finite number from {self.minimum} through "
                    f"{self.maximum}; actual value {actual!r} is not numeric."
                ),
            )

        status = (
            HealthStatus.HEALTHY
            if self.minimum <= value <= self.maximum
            else HealthStatus.PROBLEM
        )
        return ConditionResult(
            status=status,
            failure_code=(
                "NUMERIC_RANGE_CONDITION_FAILED"
                if status is HealthStatus.PROBLEM
                else None
            ),
            evidence_summary=(
                f"Expected number from {self.minimum} through {self.maximum}; "
                f"actual value: {value}."
            ),
        )


@dataclass(frozen=True)
class TopicExistsCondition(Condition):
    def evaluate(self, context: ConditionEvaluationContext) -> ConditionResult:
        status = (
            HealthStatus.HEALTHY
            if context.topic_exists
            else HealthStatus.PROBLEM
        )
        return ConditionResult(
            status=status,
            failure_code=(
                "TOPIC_EXPECTED_PRESENT"
                if status is HealthStatus.PROBLEM
                else None
            ),
            evidence_summary=(
                "Topic has been observed."
                if context.topic_exists
                else "Expected topic to exist, but it has never been observed."
            ),
        )

    def handle_condition(self, actual: PayloadValue) -> ConditionResult:
        now = datetime.now(timezone.utc)
        return self.evaluate(ConditionEvaluationContext(actual, now, now))


@dataclass(frozen=True)
class TopicAbsentCondition(Condition):
    def evaluate(self, context: ConditionEvaluationContext) -> ConditionResult:
        status = (
            HealthStatus.PROBLEM
            if context.topic_exists
            else HealthStatus.HEALTHY
        )
        return ConditionResult(
            status=status,
            failure_code=(
                "TOPIC_EXPECTED_ABSENT"
                if status is HealthStatus.PROBLEM
                else None
            ),
            evidence_summary=(
                "Expected topic to be absent, but it has been observed."
                if context.topic_exists
                else "Topic has not been observed."
            ),
        )

    def handle_condition(self, actual: PayloadValue) -> ConditionResult:
        now = datetime.now(timezone.utc)
        return self.evaluate(ConditionEvaluationContext(actual, now, now))


@dataclass(frozen=True)
class FreshnessCondition(Condition):
    max_age_seconds: float

    def __post_init__(self) -> None:
        if isinstance(self.max_age_seconds, bool):
            raise ValueError(
                "Freshness maximum age must be a finite non-negative value."
            )
        max_age_seconds = float(self.max_age_seconds)
        if not math.isfinite(max_age_seconds) or max_age_seconds < 0:
            raise ValueError(
                "Freshness maximum age must be a finite non-negative value."
            )
        object.__setattr__(self, "max_age_seconds", max_age_seconds)

    def evaluate(self, context: ConditionEvaluationContext) -> ConditionResult:
        if not context.topic_exists or context.received_at is None:
            return ConditionResult(
                status=HealthStatus.PROBLEM,
                failure_code="FRESHNESS_CONDITION_FAILED",
                evidence_summary=(
                    "Expected a fresh topic observation, but the topic has never "
                    "been observed."
                ),
            )

        age_seconds = max(
            0.0,
            (
                _as_utc(context.evaluated_at) - _as_utc(context.received_at)
            ).total_seconds(),
        )
        status = (
            HealthStatus.HEALTHY
            if age_seconds <= self.max_age_seconds
            else HealthStatus.PROBLEM
        )
        return ConditionResult(
            status=status,
            failure_code=(
                "FRESHNESS_CONDITION_FAILED"
                if status is HealthStatus.PROBLEM
                else None
            ),
            evidence_summary=(
                f"Topic was last observed {age_seconds:.1f} seconds ago; "
                f"the maximum age is {self.max_age_seconds:.1f} seconds."
            ),
        )

    def handle_condition(self, actual: PayloadValue) -> ConditionResult:
        now = datetime.now(timezone.utc)
        return self.evaluate(ConditionEvaluationContext(actual, now, now))


def _parse_decimal(value: PayloadValue) -> Decimal:
    text = value.decode("utf-8") if isinstance(value, bytes) else value
    parsed = Decimal(text.strip())
    if not parsed.is_finite():
        raise ValueError("Numeric values must be finite.")
    return parsed


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)
