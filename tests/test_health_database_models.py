from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from topicgate.infrastructure.database.base import Base
from topicgate.infrastructure.database.models import (
    ExpectationFailureRow,
    ExpectationStateRow,
    HealthExpectationRow,
)


def test_health_rows_persist_expectation_and_related_state() -> None:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    expectation_id = uuid4()
    failure_id = uuid4()
    timestamp = datetime.now(timezone.utc)

    with Session(engine) as session:
        session.add(
            HealthExpectationRow(
                expectation_id=expectation_id,
                revision=1,
                enabled=True,
                severity="critical",
                target={"kind": "broker", "broker_id": str(uuid4())},
                condition={"kind": "equal", "expected_value": "online"},
                actions=["log", "store_failure"],
            )
        )
        session.add(
            ExpectationFailureRow(
                failure_id=failure_id,
                expectation_id=expectation_id,
                first_failed_at=timestamp,
                last_seen_at=timestamp,
            )
        )
        session.add(
            ExpectationStateRow(
                expectation_id=expectation_id,
                current_status="problem",
                last_evaluated_at=timestamp,
                active_failure_id=failure_id,
            )
        )
        session.commit()

        row = session.scalar(
            select(HealthExpectationRow).where(
                HealthExpectationRow.expectation_id == expectation_id
            )
        )

    assert row is not None
    assert row.actions == ["log", "store_failure"]
