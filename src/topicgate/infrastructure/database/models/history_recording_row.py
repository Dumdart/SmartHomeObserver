from datetime import datetime
from uuid import UUID

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from topicgate.infrastructure.database.base import Base


class HistoryRecordingSettingRow(Base):
    __tablename__ = "history_recording_setting"
    broker_id: Mapped[UUID] = mapped_column(Uuid, ForeignKey("broker_profile.id", ondelete="CASCADE"), primary_key=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)


class HistoryRecordingSessionRow(Base):
    __tablename__ = "history_recording_session"
    session_id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    broker_id: Mapped[UUID] = mapped_column(Uuid, ForeignKey("broker_profile.id", ondelete="CASCADE"), index=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    closed: Mapped[bool] = mapped_column(Boolean, default=False)
    admitted: Mapped[int] = mapped_column(Integer, default=0)
    committed: Mapped[int] = mapped_column(Integer, default=0)
    dropped: Mapped[int] = mapped_column(Integer, default=0)
    failed: Mapped[int] = mapped_column(Integer, default=0)
