from datetime import datetime
from uuid import UUID

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from topicgate.infrastructure.database.base import Base


class DiagnosticProfileRow(Base):
    __tablename__ = "diagnostic_profile"
    __table_args__ = (
        UniqueConstraint(
            "broker_id", "normalized_name", name="uq_diagnostic_profile_broker_name"
        ),
    )

    profile_id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    broker_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("broker_profile.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    normalized_name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="", nullable=False)
    pack_id: Mapped[str | None] = mapped_column(String, nullable=True)
    pack_version: Mapped[str | None] = mapped_column(String, nullable=True)
    rule_schema_version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    is_default: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
