from abc import ABC, abstractmethod
from dataclasses import dataclass

from topicgate.core.models.health.condition_result import ConditionResult
from topicgate.core.models.health.health_enums import HealthStatus

PayloadValue = bytes | str

class Condition(ABC):
    @abstractmethod
    def handle_condition(self, actual: bytes | str) -> ConditionResult:
        """Evaluate an observed value and return its condition result."""


@dataclass(frozen=True)
class EqualCondition(Condition):
    expected_value: bytes | str

    @staticmethod
    def compare(actual: bytes | str, expected: bytes | str) -> bool:
        if type(actual) is not type(expected):
            raise TypeError("Actual and expected values must have the same type.")
        return actual == expected

    def handle_condition(self, actual: bytes | str) -> ConditionResult:
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
