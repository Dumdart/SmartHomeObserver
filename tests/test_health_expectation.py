from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from topicgate.core.models.health import ConditionEvaluationContext
from topicgate.core.models.health import FreshnessCondition
from topicgate.core.models.health import HealthStatus
from topicgate.core.models.health import NumericRangeCondition
from topicgate.core.models.health import TopicAbsentCondition
from topicgate.core.models.health import TopicExistsCondition
from topicgate.core.models.health.health_expectation import EqualCondition
from topicgate.core.models.health.condition_kind import ConditionKind
from topicgate.processors.condition_factory import ConditionFactory


@pytest.mark.parametrize(
    ("actual", "expected", "result"),
    [
        (b"online", b"online", True),
        (b"offline", b"online", False),
        ("online", "online", True),
        ("offline", "online", False),
    ],
)
def test_equal_condition_compares_values_of_the_same_type(
    actual: bytes | str,
    expected: bytes | str,
    result: bool,
) -> None:
    assert EqualCondition.compare(actual, expected) is result


def test_equal_condition_rejects_values_of_different_types() -> None:
    with pytest.raises(
        TypeError,
        match="Actual and expected values must have the same type",
    ):
        EqualCondition.compare(b"online", "online")


@pytest.mark.parametrize("actual", [b"10", "15", b" 2e1 "])
def test_numeric_range_is_inclusive_and_parses_decimal_payloads(actual) -> None:
    result = NumericRangeCondition(Decimal("10"), Decimal("20")).handle_condition(
        actual
    )

    assert result.status is HealthStatus.HEALTHY
    assert result.failure_code is None


@pytest.mark.parametrize("actual", [b"not-a-number", "NaN", b"Infinity", b"\xff"])
def test_numeric_range_reports_unusable_payloads_as_problems(actual) -> None:
    result = NumericRangeCondition(Decimal("1"), Decimal("3")).handle_condition(
        actual
    )

    assert result.status is HealthStatus.PROBLEM
    assert result.failure_code == "NUMERIC_RANGE_CONDITION_FAILED"
    assert "not numeric" in result.evidence_summary


@pytest.mark.parametrize("actual", [b"9.99", "20.01"])
def test_numeric_range_reports_values_outside_the_bounds(actual) -> None:
    result = NumericRangeCondition(Decimal("10"), Decimal("20")).handle_condition(
        actual
    )

    assert result.status is HealthStatus.PROBLEM
    assert result.failure_code == "NUMERIC_RANGE_CONDITION_FAILED"


@pytest.mark.parametrize(
    ("minimum", "maximum"),
    [
        (Decimal("NaN"), Decimal("3")),
        (Decimal("1"), Decimal("Infinity")),
        (Decimal("4"), Decimal("3")),
    ],
)
def test_numeric_range_rejects_invalid_bounds(minimum, maximum) -> None:
    with pytest.raises(ValueError):
        NumericRangeCondition(minimum, maximum)


def test_topic_existence_distinguishes_empty_payload_from_absence() -> None:
    now = datetime(2026, 9, 7, tzinfo=timezone.utc)
    present = ConditionEvaluationContext(b"", now, now)
    missing = ConditionEvaluationContext(None, None, now)

    assert TopicExistsCondition().evaluate(present).status is HealthStatus.HEALTHY
    assert TopicExistsCondition().evaluate(missing).status is HealthStatus.PROBLEM
    assert TopicAbsentCondition().evaluate(present).status is HealthStatus.PROBLEM
    assert TopicAbsentCondition().evaluate(missing).status is HealthStatus.HEALTHY


def test_freshness_uses_inclusive_age_and_normalizes_naive_timestamps() -> None:
    evaluated_at = datetime(2026, 9, 7, 12, tzinfo=timezone.utc)
    received_at = (evaluated_at - timedelta(seconds=60)).replace(tzinfo=None)
    condition = FreshnessCondition(60)

    result = condition.evaluate(
        ConditionEvaluationContext(b"value", received_at, evaluated_at)
    )

    assert result.status is HealthStatus.HEALTHY


def test_freshness_reports_missing_and_stale_topics_as_problems() -> None:
    now = datetime(2026, 9, 7, 12, tzinfo=timezone.utc)
    condition = FreshnessCondition(30)

    missing = condition.evaluate(ConditionEvaluationContext(None, None, now))
    stale = condition.evaluate(
        ConditionEvaluationContext(b"value", now - timedelta(seconds=31), now)
    )

    assert missing.status is HealthStatus.PROBLEM
    assert stale.status is HealthStatus.PROBLEM
    assert missing.failure_code == "FRESHNESS_CONDITION_FAILED"
    assert stale.failure_code == "FRESHNESS_CONDITION_FAILED"


@pytest.mark.parametrize("max_age", [-1, float("nan"), float("inf"), True])
def test_freshness_rejects_invalid_maximum_age(max_age) -> None:
    with pytest.raises(ValueError):
        FreshnessCondition(max_age)


@pytest.mark.parametrize(
    ("kind", "values", "expected_type"),
    [
        (ConditionKind.NUMERIC_RANGE, ("1.5", "3"), NumericRangeCondition),
        (ConditionKind.TOPIC_EXISTS, (), TopicExistsCondition),
        (ConditionKind.TOPIC_ABSENT, (), TopicAbsentCondition),
        (ConditionKind.FRESH_WITHIN, ("60",), FreshnessCondition),
    ],
)
def test_condition_factory_builds_additional_conditions(
    kind,
    values,
    expected_type,
) -> None:
    assert isinstance(ConditionFactory.build_condition(kind, values), expected_type)
