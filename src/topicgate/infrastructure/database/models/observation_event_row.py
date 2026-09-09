from datetime import datetime
from uuid import UUID

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, LargeBinary, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from topicgate.infrastructure.database.base import Base


class ObservationEventRow(Base):
    __tablename__ = "observation_event"
    __table_args__ = (
        Index("ix_history_topic_order", "broker_id", "topic", "received_at", "observation_id"),
        Index("ix_history_broker_order", "broker_id", "received_at", "observation_id"),
        Index("ix_history_global_order", "received_at", "observation_id"),
    )

    observation_id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    sequence: Mapped[int] = mapped_column(Integer, unique=True)
    broker_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("broker_profile.id", ondelete="CASCADE"),
    )
    topic: Mapped[str] = mapped_column(String)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    payload: Mapped[bytes] = mapped_column(LargeBinary)
    payload_size: Mapped[int] = mapped_column(Integer)
    qos: Mapped[int] = mapped_column(Integer)
    retain: Mapped[bool] = mapped_column(Boolean)
    is_truncated: Mapped[bool] = mapped_column(Boolean)
    provenance: Mapped[str] = mapped_column(String)


class ObservationHistoryClockRow(Base):
    __tablename__ = "observation_history_clock"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    last_sequence: Mapped[int] = mapped_column(Integer, default=0)
