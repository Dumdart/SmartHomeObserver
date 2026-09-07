from uuid import UUID

from sqlalchemy.orm import Session

from topicgate.core.models.health import ExpectationFailure
from topicgate.infrastructure.database.database_context import DatabaseContext
from topicgate.infrastructure.database.mappers.expectation_failure_mapper import (
    ExpectationFailureMapper,
)
from topicgate.infrastructure.database.models.expectation_failure_row import (
    ExpectationFailureRow,
)


class ExpectationFailureRepository:
    def __init__(self, db: DatabaseContext) -> None:
        self._db = db

    def get(
        self,
        failure_id: UUID,
        *,
        transaction: object | None = None,
    ) -> ExpectationFailure | None:
        if transaction is not None:
            row = self._session(transaction).get(ExpectationFailureRow, failure_id)
            return None if row is None else ExpectationFailureMapper.to_model(row)
        with self._db.session() as session:
            row = session.get(ExpectationFailureRow, failure_id)
            return None if row is None else ExpectationFailureMapper.to_model(row)

    def create(self, failure: ExpectationFailure) -> ExpectationFailure:
        with self._db.transaction() as session:
            if session.get(ExpectationFailureRow, failure.failure_id):
                raise ValueError(
                    f"Expectation failure {failure.failure_id} already exists."
                )
            session.add(ExpectationFailureMapper.to_row(failure))
        return failure

    def get_all_states(self) -> list[ExpectationFailure]:
        with self._db.session() as session:
            rows = session.query(ExpectationFailureRow).all()
            return [ExpectationFailureMapper.to_model(row) for row in rows]

    def upsert(
        self,
        failure: ExpectationFailure,
        *,
        transaction: object | None = None,
    ) -> ExpectationFailure:
        if transaction is not None:
            session = self._session(transaction)
            session.merge(ExpectationFailureMapper.to_row(failure))
            session.flush()
            return failure
        with self._db.transaction() as session:
            session.merge(ExpectationFailureMapper.to_row(failure))
        return failure

    def delete(self, failure_id: UUID) -> None:
        with self._db.transaction() as session:
            row = session.get(ExpectationFailureRow, failure_id)
            if row is None:
                raise KeyError(f"Unknown expectation failure: {failure_id}")
            session.delete(row)

    @staticmethod
    def _session(transaction: object) -> Session:
        if not isinstance(transaction, Session):
            raise TypeError(
                "Health repository transaction must be a SQLAlchemy Session."
            )
        return transaction
