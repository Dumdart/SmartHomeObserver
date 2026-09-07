from datetime import datetime, timezone
from decimal import Decimal
from uuid import uuid4

import pytest

from topicgate.core.models.health.condition import EqualCondition
from topicgate.core.models.health.condition import FreshnessCondition
from topicgate.core.models.health.condition import InRangeCondition
from topicgate.core.models.health.condition import NumericRangeCondition
from topicgate.core.models.health.condition import OutSideCondition
from topicgate.core.models.health.condition import TopicAbsentCondition
from topicgate.core.models.health.condition import TopicExistsCondition
from topicgate.core.models.health.expectation_failure import ExpectationFailure
from topicgate.core.models.health.expectation_state import ExpectationState
from topicgate.core.models.health.expectation_target import TopicTarget
from topicgate.core.models.health.health_enums import ActionKind
from topicgate.core.models.health.health_enums import HealthSeverity
from topicgate.core.models.health.health_enums import HealthStatus
from topicgate.core.models.health.health_expectation import HealthExpectation
from topicgate.infrastructure.database.mappers.expectation_failure_mapper import (
    ExpectationFailureMapper,
)
from topicgate.infrastructure.database.mappers.expectation_state_mapper import (
    ExpectationStateMapper,
)
from topicgate.infrastructure.database.mappers.health_expectation_mapper import (
    HealthExpectationMapper,
)


def test_health_expectation_mapper_round_trips_topic_and_condition() -> None:
    expectation = HealthExpectation(
        expectation_id=uuid4(),
        revision=3,
        enabled=True,
        severity=HealthSeverity.CRITICAL,
        target=TopicTarget(uuid4(), "devices/status"),
        condition=EqualCondition(b"online"),
        actions=frozenset({ActionKind.LOG, ActionKind.STORE_FAILURE}),
    )

    row = HealthExpectationMapper.to_row(expectation)
    restored = HealthExpectationMapper.to_model(row)

    assert restored == expectation
    assert row.target == {
        "kind": "topic",
        "broker_id": str(expectation.target.broker_id),
        "topic": "devices/status",
    }
    assert row.actions == ["log", "store_failure"]


@pytest.mark.parametrize(
    ("condition", "serialized"),
    [
        (
            EqualCondition(b"online"),
            {
                "kind": "equal",
                "expected_value": "b25saW5l",
                "value_type": "bytes",
            },
        ),
        (
            InRangeCondition((b"online", b"degraded")),
            {
                "kind": "in_range",
                "expected_values": ["b25saW5l", "ZGVncmFkZWQ="],
                "value_type": "bytes",
            },
        ),
        (
            OutSideCondition(("offline", "unknown")),
            {
                "kind": "outside",
                "expected_values": ["offline", "unknown"],
            },
        ),
        (
            NumericRangeCondition(Decimal("1.5"), Decimal("3")),
            {
                "kind": "numeric_range",
                "minimum": "1.5",
                "maximum": "3",
            },
        ),
        (TopicExistsCondition(), {"kind": "topic_exists"}),
        (TopicAbsentCondition(), {"kind": "topic_absent"}),
        (
            FreshnessCondition(60),
            {"kind": "fresh_within", "max_age_seconds": 60.0},
        ),
    ],
)
def test_condition_mapper_round_trips_all_condition_kinds(
    condition: (
        EqualCondition
        | InRangeCondition
        | OutSideCondition
        | NumericRangeCondition
        | TopicExistsCondition
        | TopicAbsentCondition
        | FreshnessCondition
    ),
    serialized: dict,
) -> None:
    assert HealthExpectationMapper._condition_to_dict(condition) == serialized
    assert HealthExpectationMapper._dict_to_condition(serialized) == condition


@pytest.mark.parametrize(
    "serialized",
    [
        {"kind": "in_range", "expected_values": []},
        {
            "kind": "outside",
            "expected_values": ["%%%="],
            "value_type": "bytes",
        },
        {"kind": "unknown", "expected_value": "online"},
        {"kind": "numeric_range", "minimum": "1"},
        {"kind": "numeric_range", "minimum": "NaN", "maximum": "3"},
        {"kind": "numeric_range", "minimum": "4", "maximum": "3"},
        {"kind": "topic_exists", "expected_value": "unexpected"},
        {"kind": "fresh_within", "max_age_seconds": "60"},
        {"kind": "fresh_within", "max_age_seconds": -1},
    ],
)
def test_condition_mapper_rejects_invalid_condition_json(
    serialized: dict,
) -> None:
    with pytest.raises(ValueError):
        HealthExpectationMapper._dict_to_condition(serialized)


def test_expectation_failure_mapper_round_trips_optional_values() -> None:
    timestamp = datetime.now(timezone.utc)
    failure = ExpectationFailure(
        failure_id=uuid4(),
        expectation_id=uuid4(),
        first_failed_at=timestamp,
        last_seen_at=timestamp,
        occurrence_count=2,
        expected_revision=4,
        last_healthy_at=timestamp,
        recovered_at=None,
        failure_code="mismatch",
        evidence_summary="expected online",
    )

    assert ExpectationFailureMapper.to_model(
        ExpectationFailureMapper.to_row(failure)
    ) == failure


def test_expectation_state_mapper_converts_status() -> None:
    state = ExpectationState(
        expectation_id=uuid4(),
        current_status=HealthStatus.PROBLEM,
        active_failure_id=uuid4(),
    )

    row = ExpectationStateMapper.to_row(state)

    assert row.current_status == "problem"
    assert ExpectationStateMapper.to_model(row) == state
