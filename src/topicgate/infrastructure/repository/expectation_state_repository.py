from uuid import UUID

from sqlalchemy.orm import Session

from topicgate.core.models.health import ExpectationState
from topicgate.infrastructure.database.database_context import DatabaseContext
from topicgate.infrastructure.database.mappers.expectation_state_mapper import (
    ExpectationStateMapper,
)
from topicgate.infrastructure.database.models.expectation_state_row import (
    ExpectationStateRow,
)


class ExpectationStateRepository:
    def __init__(self, db: DatabaseContext) -> None:
        self._db = db

    def get(
        self,
        expectation_id: UUID,
        *,
        transaction: object | None = None,
    ) -> ExpectationState | None:
        if transaction is not None:
            row = self._session(transaction).get(ExpectationStateRow, expectation_id)
            return None if row is None else ExpectationStateMapper.to_model(row)
        with self._db.session() as session:
            row = session.get(ExpectationStateRow, expectation_id)
            return None if row is None else ExpectationStateMapper.to_model(row)

    def get_all_states(self) -> list[ExpectationState]:
        with self._db.session() as session:
            rows = session.query(ExpectationStateRow).all()
            return [ExpectationStateMapper.to_model(row) for row in rows]

    def create(self, state: ExpectationState) -> ExpectationState:
        with self._db.transaction() as session:
            if session.get(ExpectationStateRow, state.expectation_id):
                raise ValueError(
                    f"Expectation state {state.expectation_id} already exists."
                )
            session.add(ExpectationStateMapper.to_row(state))
        return state

    def upsert(
        self,
        state: ExpectationState,
        *,
        transaction: object | None = None,
    ) -> ExpectationState:
        if transaction is not None:
            session = self._session(transaction)
            session.merge(ExpectationStateMapper.to_row(state))
            session.flush()
            return state
        with self._db.transaction() as session:
            session.merge(ExpectationStateMapper.to_row(state))
        return state

    @staticmethod
    def _session(transaction: object) -> Session:
        if not isinstance(transaction, Session):
            raise TypeError(
                "Health repository transaction must be a SQLAlchemy Session."
            )
        return transaction
