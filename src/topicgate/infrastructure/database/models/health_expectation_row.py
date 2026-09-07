from uuid import UUID

from sqlalchemy import Boolean, Integer, JSON, String, Text, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from topicgate.infrastructure.database.base import Base


class HealthExpectationRow(Base):
    __tablename__ = "health_expectation"

    expectation_id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    revision: Mapped[int] = mapped_column(Integer)
    enabled: Mapped[bool] = mapped_column(Boolean)
    severity: Mapped[str] = mapped_column(String)
    target: Mapped[dict] = mapped_column(JSON)
    condition: Mapped[dict] = mapped_column(JSON)
    actions: Mapped[list[str]] = mapped_column(JSON)
    name: Mapped[str] = mapped_column(String, default="")
    description: Mapped[str] = mapped_column(Text, default="")
