from contextlib import nullcontext
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from topicgate.core.models.diagnostic_profile import DiagnosticProfile
from topicgate.infrastructure.database.database_context import DatabaseContext
from topicgate.infrastructure.database.mappers.diagnostic_profile_mapper import DiagnosticProfileMapper
from topicgate.infrastructure.database.mappers.health_expectation_mapper import HealthExpectationMapper
from topicgate.infrastructure.database.models.diagnostic_profile_row import DiagnosticProfileRow
from topicgate.infrastructure.database.models.health_expectation_row import HealthExpectationRow


class SqlDiagnosticProfileRepository:
    def __init__(self, db: DatabaseContext) -> None:
        self._db = db

    def list_for_broker(self, broker_id: UUID) -> tuple[DiagnosticProfile, ...]:
        with self._db.session() as session:
            rows = session.scalars(
                select(DiagnosticProfileRow)
                .where(DiagnosticProfileRow.broker_id == broker_id)
                .order_by(DiagnosticProfileRow.normalized_name, DiagnosticProfileRow.profile_id)
            ).all()
            return tuple(self._aggregate(session, row) for row in rows)

    def get(self, profile_id: UUID, *, transaction: object | None = None) -> DiagnosticProfile | None:
        with self._scope(transaction, write=False) as session:
            row = session.get(DiagnosticProfileRow, profile_id)
            return None if row is None else self._aggregate(session, row)

    def create(self, profile: DiagnosticProfile, *, transaction: object | None = None) -> DiagnosticProfile:
        with self._scope(transaction) as session:
            if session.get(DiagnosticProfileRow, profile.profile_id) is not None:
                raise ValueError(f"Diagnostic profile {profile.profile_id} already exists.")
            self._ensure_name_available(session, profile)
            session.add(DiagnosticProfileMapper.to_row(profile))
            session.flush()
        return profile

    def update(self, profile: DiagnosticProfile, *, transaction: object | None = None) -> DiagnosticProfile:
        with self._scope(transaction) as session:
            current = session.get(DiagnosticProfileRow, profile.profile_id)
            if current is None:
                raise KeyError(f"Unknown diagnostic profile: {profile.profile_id}")
            if current.is_default and profile.name != current.name:
                raise ValueError("The reserved default profile cannot be renamed.")
            self._ensure_name_available(session, profile)
            session.merge(DiagnosticProfileMapper.to_row(profile))
        return profile

    def delete(self, profile_id: UUID, *, transaction: object | None = None) -> None:
        with self._scope(transaction) as session:
            row = session.get(DiagnosticProfileRow, profile_id)
            if row is None:
                raise KeyError(f"Unknown diagnostic profile: {profile_id}")
            if row.is_default:
                raise ValueError("The reserved default profile cannot be deleted.")
            session.delete(row)

    @staticmethod
    def _aggregate(session: Session, row: DiagnosticProfileRow) -> DiagnosticProfile:
        rule_rows = session.scalars(
            select(HealthExpectationRow)
            .where(HealthExpectationRow.profile_id == row.profile_id)
            .order_by(HealthExpectationRow.normalized_rule_id)
        ).all()
        custom = tuple(
            HealthExpectationMapper.to_model(item)
            for item in rule_rows
            if item.source_kind == "custom"
        )
        overrides = {
            item.rule_id: dict(item.pack_override or {})
            for item in rule_rows
            if item.source_kind == "pack" and item.pack_override
        }
        return DiagnosticProfileMapper.to_model(
            row, custom_rules=custom, pack_overrides=overrides
        )

    @staticmethod
    def _ensure_name_available(session: Session, profile: DiagnosticProfile) -> None:
        conflict = session.scalar(
            select(DiagnosticProfileRow.profile_id).where(
                DiagnosticProfileRow.broker_id == profile.broker_id,
                DiagnosticProfileRow.normalized_name == profile.normalized_name,
                DiagnosticProfileRow.profile_id != profile.profile_id,
            )
        )
        if conflict is not None:
            raise ValueError("A diagnostic profile with that name already exists.")

    def _scope(self, transaction: object | None, *, write: bool = True):
        if transaction is not None:
            if not isinstance(transaction, Session):
                raise TypeError("Diagnostic profile transaction must be a SQLAlchemy Session.")
            return nullcontext(transaction)
        return self._db.transaction() if write else self._db.session()
