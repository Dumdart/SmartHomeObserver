from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from uuid import UUID

from sqlalchemy import delete, func, select, update

from topicgate.core.models.history_retention import HistoryPruneResult, HistoryRetentionPolicy, HistoryUsage
from topicgate.infrastructure.database.database_context import DatabaseContext
from topicgate.infrastructure.database.models.history_retention_row import (
    HistoryRetentionPolicyRow, HistoryRetentionStateRow,
)
from topicgate.infrastructure.database.models.observation_event_row import ObservationEventRow


class HistoryRetentionRepository:
    def __init__(self, db: DatabaseContext) -> None:
        self._db = db

    @staticmethod
    def _policy(row: HistoryRetentionPolicyRow) -> HistoryRetentionPolicy:
        return HistoryRetentionPolicy(**{
            name: getattr(row, name) for name in HistoryRetentionPolicy.__dataclass_fields__
        })

    def get_policy(self) -> HistoryRetentionPolicy:
        with self._db.session() as session:
            return self._policy(session.get(HistoryRetentionPolicyRow, 1))

    def set_policy(self, policy: HistoryRetentionPolicy) -> None:
        policy = HistoryRetentionPolicy(**asdict(policy))
        with self._db.transaction() as session:
            session.execute(update(HistoryRetentionPolicyRow).where(HistoryRetentionPolicyRow.id == 1)
                            .values(**asdict(policy)))
            session.execute(update(HistoryRetentionStateRow).where(HistoryRetentionStateRow.id == 1)
                            .values(enforcement_pending=True))

    def usage(self, broker_id: UUID | None = None) -> HistoryUsage:
        row = ObservationEventRow
        statement = select(func.count(), func.coalesce(func.sum(func.length(row.payload)), 0),
                           func.min(row.received_at)).select_from(row)
        if broker_id is not None:
            statement = statement.where(row.broker_id == broker_id)
        with self._db.session() as session:
            count, size, oldest = session.execute(statement).one()
            state = session.get(HistoryRetentionStateRow, 1)
            return HistoryUsage(
                broker_id, count, size, _aware(oldest), _aware(state.last_pruned_at),
                state.generation, dict(state.evictions), state.enforcement_pending,
            )

    def prune(self, now: datetime) -> HistoryPruneResult:
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("History pruning time must include a timezone.")
        now = now.astimezone(timezone.utc)
        row = ObservationEventRow
        reasons: dict[str, int] = {}
        with self._db.transaction() as session:
            # Check concurrent pruners serialize before selecting deletion candidates.
            session.execute(update(HistoryRetentionStateRow).where(HistoryRetentionStateRow.id == 1)
                            .values(generation=HistoryRetentionStateRow.generation))
            policy = self._policy(session.get(HistoryRetentionPolicyRow, 1))
            remaining = policy.prune_batch_size

            def remove(statement, reason: str) -> None:
                nonlocal remaining
                if remaining == 0:
                    return
                ids = list(session.scalars(statement.order_by(row.received_at, row.observation_id)
                                           .limit(remaining)))
                if ids:
                    session.execute(delete(row).where(row.observation_id.in_(ids)))
                    reasons[reason] = len(ids)
                    remaining -= len(ids)

            if policy.max_age_seconds is not None:
                remove(select(row.observation_id).where(
                    row.received_at < now - timedelta(seconds=policy.max_age_seconds)), "age")
            for partition, maximum, reason in (
                ([row.broker_id, row.topic], policy.max_events_per_topic, "topic_count"),
                ([row.broker_id], policy.max_events_per_broker, "broker_count"),
            ):
                if maximum is not None and remaining:
                    ranked = select(row.observation_id, func.row_number().over(
                        partition_by=partition,
                        order_by=(row.received_at.desc(), row.observation_id.desc()),
                    ).label("rank")).subquery()
                    remove(select(row.observation_id).where(row.observation_id.in_(
                        select(ranked.c.observation_id).where(ranked.c.rank > maximum))), reason)
            if remaining:
                total = session.scalar(select(func.coalesce(func.sum(func.length(row.payload)), 0)))
                excess = total - policy.max_payload_bytes
                if excess > 0:
                    candidates = session.execute(select(row.observation_id, func.length(row.payload))
                                                 .order_by(row.received_at, row.observation_id)
                                                 .limit(remaining)).all()
                    ids = []
                    for event_id, size in candidates:
                        ids.append(event_id)
                        excess -= size
                        if excess <= 0:
                            break
                    if ids:
                        session.execute(delete(row).where(row.observation_id.in_(ids)))
                        reasons["payload_bytes"] = len(ids)
                        remaining -= len(ids)
            state = session.get(HistoryRetentionStateRow, 1)
            if reasons:
                state.generation += 1
                state.last_pruned_at = now
                state.evictions = {
                    key: state.evictions.get(key, 0) + reasons.get(key, 0)
                    for key in set(state.evictions) | set(reasons)
                }
            state.enforcement_pending = remaining == 0
        return HistoryPruneResult(sum(reasons.values()), reasons, remaining == 0)


def _aware(value: datetime | None) -> datetime | None:
    return value.replace(tzinfo=timezone.utc) if value is not None and value.tzinfo is None else value
