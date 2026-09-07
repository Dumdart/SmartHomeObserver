from contextlib import nullcontext
from uuid import UUID

from sqlalchemy.orm import Session

from sqlalchemy import select

from topicgate.core.models.health import HealthExpectation
from topicgate.core.models.health import TopicTarget
from topicgate.infrastructure.database.database_context import DatabaseContext
from topicgate.infrastructure.database.mappers.health_expectation_mapper import (
    HealthExpectationMapper,
)
from topicgate.infrastructure.database.models.health_expectation_row import (
    HealthExpectationRow,
)
from topicgate.infrastructure.database.models.expectation_failure_row import (
    ExpectationFailureRow,
)


class HealthExpectationRepository:
    def __init__(self, db: DatabaseContext) -> None:
        self._db = db

    def get(
        self, expectation_id: UUID, *, transaction: object | None = None
    ) -> HealthExpectation | None:
        with self._scope(transaction, write=False) as session:
            row = session.get(HealthExpectationRow, expectation_id)
            return None if row is None else HealthExpectationMapper.to_model(row)

    def list_all(self) -> tuple[HealthExpectation, ...]:
        with self._db.session() as session:
            rows = session.scalars(select(HealthExpectationRow)).all()
        return tuple(HealthExpectationMapper.to_model(row) for row in rows)

    def list_for_broker(self, broker_id: UUID) -> tuple[HealthExpectation, ...]:
        return tuple(
            expectation
            for expectation in self.list_all()
            if getattr(expectation.target, "broker_id", None) == broker_id
        )

    def list_for_topic(
        self,
        broker_id: UUID,
        topic: str,
    ) -> tuple[HealthExpectation, ...]:
        with self._db.session() as session:
            rows = session.scalars(select(HealthExpectationRow)).all()
        matches = []
        for row in rows:
            expectation = HealthExpectationMapper.to_model(row)
            target = expectation.target
            if (
                isinstance(target, TopicTarget)
                and target.broker_id == broker_id
                and target.topic == topic
            ):
                matches.append(expectation)
        return tuple(matches)

    def create(self, expectation: HealthExpectation) -> HealthExpectation:
        with self._db.transaction() as session:
            if session.get(HealthExpectationRow, expectation.expectation_id):
                raise ValueError(
                    f"Health expectation {expectation.expectation_id} already exists."
                )
            session.add(HealthExpectationMapper.to_row(expectation))
        return expectation

    def upsert(self, expectation: HealthExpectation) -> HealthExpectation:
        with self._db.transaction() as session:
            session.merge(HealthExpectationMapper.to_row(expectation))
        return expectation

    def update(
        self, expectation: HealthExpectation, *, transaction: object | None = None
    ) -> HealthExpectation:
        with self._scope(transaction) as session:
            row = session.get(HealthExpectationRow, expectation.expectation_id)
            if row is None:
                raise KeyError(
                    f"Unknown health expectation: {expectation.expectation_id}"
                )
            session.merge(HealthExpectationMapper.to_row(expectation))
        return expectation

    def delete(
        self,
        expectation_id: UUID,
        *,
        retain_history: bool = False,
        transaction: object | None = None,
    ) -> None:
        with self._scope(transaction) as session:
            row = session.get(HealthExpectationRow, expectation_id)
            if row is None:
                raise KeyError(f"Unknown health expectation: {expectation_id}")
            if not retain_history:
                session.query(ExpectationFailureRow).filter(
                    ExpectationFailureRow.expectation_id == expectation_id
                ).delete(synchronize_session=False)
            session.delete(row)

    def patch(self, expectation_id: UUID, updates: dict) -> HealthExpectation:
        with self._db.transaction() as session:
            row = session.get(HealthExpectationRow, expectation_id)
            if row is None:
                raise KeyError(f"Unknown health expectation: {expectation_id}")
            for key, value in updates.items():
                setattr(row, key, value)
        return HealthExpectationMapper.to_model(row)

    def _scope(self, transaction: object | None, *, write: bool = True):
        if transaction is not None:
            if not isinstance(transaction, Session):
                raise TypeError(
                    "Health repository transaction must be a SQLAlchemy Session."
                )
            return nullcontext(transaction)
        return self._db.transaction() if write else self._db.session()
