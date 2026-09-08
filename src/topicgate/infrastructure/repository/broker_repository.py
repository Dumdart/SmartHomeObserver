from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.orm import joinedload

from topicgate.core.models.broker_profile_identity import BrokerProfileIdentity
from topicgate.infrastructure.database.database_context import DatabaseContext
from topicgate.infrastructure.database.models.broker_profile_row import BrokerProfileRow
from topicgate.infrastructure.database.models.mqtt_config_row import MqttConfigRow
from topicgate.infrastructure.database.models.observer_workspace_row import (
    ObserverWorkspaceRow,
)
from topicgate.core.models.diagnostic_profile import DEFAULT_PROFILE_NAME, default_profile_id, utc_now
from topicgate.infrastructure.database.models.diagnostic_profile_row import DiagnosticProfileRow


class BrokerRepository:
    """Persist only broker-profile identity and lifecycle metadata."""

    def __init__(self, db: DatabaseContext) -> None:
        self._db = db

    def list_profiles(self) -> tuple[BrokerProfileIdentity, ...]:
        with self._db.session() as session:
            rows = session.scalars(
                self._statement().order_by(BrokerProfileRow.position)
            ).all()
            return tuple(self._to_identity(row) for row in rows)

    def get_profile(self, profile_id: UUID | None = None) -> BrokerProfileIdentity:
        with self._db.session() as session:
            statement = self._statement()
            if profile_id is None:
                statement = statement.where(BrokerProfileRow.is_active.is_(True))
            else:
                statement = statement.where(BrokerProfileRow.id == profile_id)
            row = session.scalar(statement)
            if row is None:
                identifier = "active profile" if profile_id is None else profile_id
                raise KeyError(f"Unknown broker profile: {identifier}")
            return self._to_identity(row)

    def create_profile(
        self,
        name: str,
        config_id: int,
        *,
        profile_id: UUID | None = None,
        workspace_id: UUID | None = None,
        is_active: bool = False,
        session=None,
    ) -> BrokerProfileIdentity:
        normalized_name = self._validate_name(name, session=session)
        if session is not None:
            return self._create(
                session, normalized_name, config_id, profile_id, workspace_id, is_active
            )
        with self._db.transaction() as owned_session:
            return self._create(
                owned_session,
                normalized_name,
                config_id,
                profile_id,
                workspace_id,
                is_active,
            )

    def validate_profile_name(
        self, name: str, profile_id: UUID | None = None
    ) -> str:
        return self._validate_name(name, profile_id)

    @classmethod
    def _create(
        cls,
        session,
        name: str,
        config_id: int,
        profile_id: UUID | None,
        workspace_id: UUID | None,
        is_active: bool,
    ) -> BrokerProfileIdentity:
        config = session.get(MqttConfigRow, config_id)
        if config is None:
            raise KeyError(f"Unknown broker configuration: {config_id}")
        position = max(
            session.scalars(select(BrokerProfileRow.position)).all(), default=-1
        ) + 1
        row = BrokerProfileRow(
            id=profile_id or uuid4(),
            name=name,
            position=position,
            is_active=is_active,
        )
        row.config = config
        row.workspace = ObserverWorkspaceRow(
            id=workspace_id or uuid4(), profile_id=row.id
        )
        session.add(row)
        session.flush()
        now = utc_now()
        session.add(
            DiagnosticProfileRow(
                profile_id=default_profile_id(row.id),
                broker_id=row.id,
                name=DEFAULT_PROFILE_NAME,
                normalized_name=DEFAULT_PROFILE_NAME.casefold(),
                description="",
                pack_id=None,
                pack_version=None,
                rule_schema_version=1,
                is_default=True,
                created_at=now,
                updated_at=now,
            )
        )
        return cls._to_identity(row)

    def update_profile_name(self, profile_id: UUID, name: str) -> None:
        normalized_name = self._validate_name(name, profile_id)
        with self._db.session() as session:
            row = session.get(BrokerProfileRow, profile_id)
            if row is None:
                raise KeyError(f"Unknown broker profile: {profile_id}")
            row.name = normalized_name
            session.commit()

    def select_active_profile(self, profile_id: UUID) -> None:
        with self._db.session() as session:
            rows = session.scalars(select(BrokerProfileRow)).all()
            if not any(row.id == profile_id for row in rows):
                raise KeyError(f"Unknown broker profile: {profile_id}")
            for row in rows:
                row.is_active = row.id == profile_id
            session.commit()

    def delete_profile(self, profile_id: UUID) -> BrokerProfileIdentity:
        with self._db.session() as session:
            row = session.scalar(
                self._statement().where(BrokerProfileRow.id == profile_id)
            )
            if row is None:
                raise KeyError(f"Unknown broker profile: {profile_id}")
            identity = self._to_identity(row)
            session.delete(row)
            session.commit()
            return identity

    @staticmethod
    def _statement():
        return select(BrokerProfileRow).options(joinedload(BrokerProfileRow.workspace))

    @staticmethod
    def _to_identity(row: BrokerProfileRow) -> BrokerProfileIdentity:
        return BrokerProfileIdentity(
            id=row.id,
            name=row.name,
            position=row.position,
            is_active=row.is_active,
            workspace_id=row.workspace.id,
        )

    def _validate_name(
        self,
        name: str,
        profile_id: UUID | None = None,
        *,
        session=None,
    ) -> str:
        normalized_name = name.strip()
        if not normalized_name:
            raise ValueError("A broker profile name is required.")
        if session is None:
            with self._db.session() as owned_session:
                rows = owned_session.execute(
                    select(BrokerProfileRow.id, BrokerProfileRow.name)
                ).all()
        else:
            rows = session.execute(
                select(BrokerProfileRow.id, BrokerProfileRow.name)
            ).all()
        if any(
            row.id != profile_id
            and row.name.casefold() == normalized_name.casefold()
            for row in rows
        ):
            raise ValueError("A broker profile with that name already exists.")
        return normalized_name
