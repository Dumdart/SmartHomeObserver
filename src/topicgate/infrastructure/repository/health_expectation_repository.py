from contextlib import nullcontext
from uuid import UUID
from dataclasses import replace

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
from topicgate.core.interfaces.diagnostic_pack import PackReference
from topicgate.core.models.health import ActionKind, HealthSeverity
from topicgate.infrastructure.database.models.diagnostic_profile_row import DiagnosticProfileRow


class HealthExpectationRepository:
    def __init__(self, db: DatabaseContext, pack_resolver=None) -> None:
        self._db = db
        self._pack_resolver = pack_resolver

    def has_profile(self, profile_id: UUID) -> bool:
        with self._db.session() as session:
            return session.get(DiagnosticProfileRow, profile_id) is not None

    def get(
        self, expectation_id: UUID, *, transaction: object | None = None
    ) -> HealthExpectation | None:
        with self._scope(transaction, write=False) as session:
            row = session.get(HealthExpectationRow, expectation_id)
            return None if row is None else self._to_model(session, row)

    def list_all(self) -> tuple[HealthExpectation, ...]:
        with self._db.session() as session:
            rows = session.scalars(
                select(HealthExpectationRow).order_by(
                    HealthExpectationRow.profile_id,
                    HealthExpectationRow.normalized_rule_id,
                    HealthExpectationRow.expectation_id,
                )
            ).all()
            return tuple(self._to_model(session, row) for row in rows)

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
            rows = session.scalars(
                select(HealthExpectationRow).order_by(
                    HealthExpectationRow.profile_id,
                    HealthExpectationRow.normalized_rule_id,
                    HealthExpectationRow.expectation_id,
                )
            ).all()
        matches = []
        for row in rows:
            expectation = self._to_model(session, row)
            target = expectation.target
            if (
                isinstance(target, TopicTarget)
                and target.broker_id == broker_id
                and target.topic == topic
            ):
                matches.append(expectation)
        return tuple(matches)

    def create(
        self, expectation: HealthExpectation, *, transaction: object | None = None
    ) -> HealthExpectation:
        with self._scope(transaction) as session:
            if session.get(HealthExpectationRow, expectation.expectation_id):
                raise ValueError(
                    f"Health expectation {expectation.expectation_id} already exists."
                )
            if (
                expectation.profile_id is not None
                and expectation.rule_id.startswith("rule-")
                and expectation.rule_id != f"rule-{expectation.expectation_id}"
            ):
                expectation = replace(
                    expectation, rule_id=f"rule-{expectation.expectation_id}"
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
            if row.source_kind == "pack" and expectation.source_kind == "pack":
                expectation = replace(
                    expectation,
                    source_kind="pack",
                    pack_override=self._pack_override(session, expectation),
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
        with self._db.session() as session:
            refreshed = session.get(HealthExpectationRow, expectation_id)
            return self._to_model(session, refreshed)

    def _to_model(self, session: Session, row: HealthExpectationRow) -> HealthExpectation:
        if row.source_kind != "pack":
            return HealthExpectationMapper.to_model(row)
        profile = session.get(DiagnosticProfileRow, row.profile_id)
        if profile is None or profile.pack_id is None or self._pack_resolver is None:
            reference = "unknown" if profile is None else f"{profile.pack_id}@{profile.pack_version}"
            raise ValueError(f"Cannot hydrate pack-backed rule {row.rule_id!r}; diagnostic pack {reference} is unavailable.")
        pack = self._pack_resolver.resolve(PackReference(profile.pack_id, profile.pack_version))
        rules = {item.rule_id.casefold(): item for item in pack.build_expectations(profile.broker_id, profile.profile_id)}
        try:
            base = rules[row.normalized_rule_id]
        except KeyError as error:
            raise ValueError(
                f"Diagnostic pack {profile.pack_id}@{profile.pack_version} does not contain rule {row.rule_id!r}."
            ) from error
        override = row.pack_override or {}
        updates = {"expectation_id": row.expectation_id, "revision": row.revision, "pack_override": dict(override)}
        if "enabled" in override:
            updates["enabled"] = override["enabled"]
        if "severity" in override:
            updates["severity"] = HealthSeverity(override["severity"])
        if "target" in override:
            updates["target"] = HealthExpectationMapper._dict_to_target(override["target"])
        if "condition" in override:
            updates["condition"] = HealthExpectationMapper._dict_to_condition(override["condition"])
        if "actions" in override:
            updates["actions"] = frozenset(ActionKind(item) for item in override["actions"])
        for field in ("name", "description"):
            if field in override:
                updates[field] = override[field]
        return replace(base, **updates)

    def _pack_override(
        self, session: Session, expectation: HealthExpectation
    ) -> dict:
        profile = session.get(DiagnosticProfileRow, expectation.profile_id)
        if profile is None or profile.pack_id is None or self._pack_resolver is None:
            raise ValueError("Cannot update a pack-backed rule without its exact installed pack.")
        pack = self._pack_resolver.resolve(
            PackReference(profile.pack_id, profile.pack_version)
        )
        defaults = {
            item.rule_id.casefold(): item
            for item in pack.build_expectations(profile.broker_id, profile.profile_id)
        }
        try:
            base = defaults[expectation.rule_id.casefold()]
        except KeyError as error:
            raise ValueError(f"Unknown diagnostic pack rule ID: {expectation.rule_id}") from error
        actual = {
            "enabled": expectation.enabled,
            "severity": expectation.severity.value,
            "target": HealthExpectationMapper._target_to_dict(expectation.target),
            "condition": HealthExpectationMapper._condition_to_dict(expectation.condition),
            "actions": sorted(item.value for item in expectation.actions),
            "name": expectation.name,
            "description": expectation.description,
        }
        expected = {
            "enabled": base.enabled,
            "severity": base.severity.value,
            "target": HealthExpectationMapper._target_to_dict(base.target),
            "condition": HealthExpectationMapper._condition_to_dict(base.condition),
            "actions": sorted(item.value for item in base.actions),
            "name": base.name,
            "description": base.description,
        }
        return {key: value for key, value in actual.items() if value != expected[key]}

    def _scope(self, transaction: object | None, *, write: bool = True):
        if transaction is not None:
            if not isinstance(transaction, Session):
                raise TypeError(
                    "Health repository transaction must be a SQLAlchemy Session."
                )
            return nullcontext(transaction)
        return self._db.transaction() if write else self._db.session()
