from contextlib import AbstractContextManager
from typing import Protocol
from uuid import UUID

from topicgate.core.models.health import ExpectationFailure
from topicgate.core.models.health import ExpectationState
from topicgate.core.models.health import HealthExpectation


class HealthExpectationReader(Protocol):
    def get(self, expectation_id: UUID) -> HealthExpectation | None: ...

    def list_all(self) -> tuple[HealthExpectation, ...]: ...

    def list_for_broker(self, broker_id: UUID) -> tuple[HealthExpectation, ...]: ...

    def list_for_topic(
        self,
        broker_id: UUID,
        topic: str,
    ) -> tuple[HealthExpectation, ...]: ...

    def create(self, expectation: HealthExpectation) -> HealthExpectation: ...

    def update(self, expectation: HealthExpectation) -> HealthExpectation: ...

    def patch(self, expectation_id: UUID, updates: dict) -> HealthExpectation: ...

    def delete(self, expectation_id: UUID, *, retain_history: bool = False) -> None: ...



class ExpectationStateStore(Protocol):
    def get(
        self,
        expectation_id: UUID,
        *,
        transaction: object | None = None,
    ) -> ExpectationState | None: ...

    def get_all_states(self) -> list[ExpectationState]: ...

    def upsert(
        self,
        state: ExpectationState,
        *,
        transaction: object | None = None,
    ) -> ExpectationState: ...


class ExpectationFailureStore(Protocol):
    def get(
        self,
        failure_id: UUID,
        *,
        transaction: object | None = None,
    ) -> ExpectationFailure | None: ...

    def get_all_states(self) -> list[ExpectationFailure]: ...

    def upsert(
        self,
        failure: ExpectationFailure,
        *,
        transaction: object | None = None,
    ) -> ExpectationFailure: ...

    def delete(self, failure_id: UUID) -> None: ...


class TransactionManager(Protocol):
    def transaction(self) -> AbstractContextManager[object]: ...
