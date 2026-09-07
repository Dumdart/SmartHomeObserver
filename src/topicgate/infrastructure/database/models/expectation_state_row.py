from datetime import datetime
from uuid import UUID

from sqlalchemy import DateTime, ForeignKey, Integer, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from topicgate.infrastructure.database.base import Base


class ExpectationStateRow(Base):
    __tablename__ = "expectation_state"

    expectation_id: Mapped[UUID] = mapped_column(
        Uuid,
        ForeignKey("health_expectation.expectation_id", ondelete="CASCADE"),
        primary_key=True,
    )
    current_status: Mapped[str] = mapped_column(String)
    expectation_revision: Mapped[int] = mapped_column(Integer, default=1)
    last_evaluated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_healthy_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    active_failure_id: Mapped[UUID | None] = mapped_column(
        Uuid,
        ForeignKey("expectation_failure.failure_id", ondelete="SET NULL"),
        nullable=True,
    )
