from datetime import datetime

from sqlalchemy import Boolean, CheckConstraint, DateTime, Integer, JSON
from sqlalchemy.orm import Mapped, mapped_column

from topicgate.infrastructure.database.base import Base


class HistoryRetentionPolicyRow(Base):
    __tablename__ = "history_retention_policy"
    __table_args__ = (
        CheckConstraint("max_age_seconds IS NULL OR max_age_seconds > 0"),
        CheckConstraint("max_events_per_broker > 0"),
        CheckConstraint("max_events_per_topic IS NULL OR max_events_per_topic > 0"),
        CheckConstraint("max_payload_bytes > 0"),
        CheckConstraint("prune_batch_size BETWEEN 1 AND 500"),
        CheckConstraint("prune_interval_seconds > 0"),
    )
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    max_age_seconds: Mapped[int | None] = mapped_column(Integer)
    max_events_per_broker: Mapped[int] = mapped_column(Integer)
    max_events_per_topic: Mapped[int | None] = mapped_column(Integer)
    max_payload_bytes: Mapped[int] = mapped_column(Integer)
    prune_batch_size: Mapped[int] = mapped_column(Integer)
    prune_interval_seconds: Mapped[int] = mapped_column(Integer)


class HistoryRetentionStateRow(Base):
    __tablename__ = "history_retention_state"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    generation: Mapped[int] = mapped_column(Integer, default=0)
    last_pruned_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    evictions: Mapped[dict] = mapped_column(JSON, default=dict)
    enforcement_pending: Mapped[bool] = mapped_column(Boolean, default=False)
