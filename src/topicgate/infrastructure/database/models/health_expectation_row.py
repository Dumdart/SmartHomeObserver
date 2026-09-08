from uuid import UUID

from sqlalchemy import Boolean, ForeignKey, Integer, JSON, String, Text, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from topicgate.infrastructure.database.base import Base


class HealthExpectationRow(Base):
    __tablename__ = "health_expectation"
    __table_args__ = (
        UniqueConstraint(
            "profile_id", "normalized_rule_id", name="uq_health_expectation_profile_rule"
        ),
    )

    expectation_id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    revision: Mapped[int] = mapped_column(Integer)
    enabled: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    severity: Mapped[str | None] = mapped_column(String, nullable=True)
    target: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    condition: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    actions: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)
    name: Mapped[str | None] = mapped_column(String, default="", nullable=True)
    description: Mapped[str | None] = mapped_column(Text, default="", nullable=True)
    profile_id: Mapped[UUID | None] = mapped_column(
        Uuid,
        ForeignKey("diagnostic_profile.profile_id", ondelete="CASCADE"),
        nullable=True,
    )
    rule_id: Mapped[str] = mapped_column(String(128), default="", nullable=False)
    normalized_rule_id: Mapped[str] = mapped_column(String(128), default="", nullable=False)
    rule_schema_version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    source_kind: Mapped[str] = mapped_column(String, default="custom", nullable=False)
    pack_override: Mapped[dict | None] = mapped_column(JSON, nullable=True)
