"""Add persisted health expectation pipeline.

Revision ID: a91e5c7d4b20
Revises: d2a4c6e8f010
Create Date: 2026-09-02 00:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "a91e5c7d4b20"
down_revision: Union[str, Sequence[str], None] = "d2a4c6e8f010"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create health expectations, states, and failure episodes."""
    op.create_table(
        "health_expectation",
        sa.Column("expectation_id", sa.Uuid(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("severity", sa.String(), nullable=False),
        sa.Column("target", sa.JSON(), nullable=False),
        sa.Column("condition", sa.JSON(), nullable=False),
        sa.Column("actions", sa.JSON(), nullable=False),
        sa.PrimaryKeyConstraint("expectation_id"),
    )
    op.create_table(
        "expectation_failure",
        sa.Column("failure_id", sa.Uuid(), nullable=False),
        sa.Column("expectation_id", sa.Uuid(), nullable=False),
        sa.Column("first_failed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("occurrence_count", sa.Integer(), nullable=False),
        sa.Column("expected_revision", sa.Integer(), nullable=False),
        sa.Column("last_healthy_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("recovered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("failure_code", sa.String(), nullable=True),
        sa.Column("evidence_summary", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(
            ["expectation_id"],
            ["health_expectation.expectation_id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("failure_id"),
    )
    op.create_index(
        "ix_expectation_failure_expectation_id",
        "expectation_failure",
        ["expectation_id"],
    )
    op.create_table(
        "expectation_state",
        sa.Column("expectation_id", sa.Uuid(), nullable=False),
        sa.Column("current_status", sa.String(), nullable=False),
        sa.Column("last_evaluated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_healthy_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("active_failure_id", sa.Uuid(), nullable=True),
        sa.ForeignKeyConstraint(
            ["active_failure_id"],
            ["expectation_failure.failure_id"],
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["expectation_id"],
            ["health_expectation.expectation_id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("expectation_id"),
    )


def downgrade() -> None:
    """Remove the health expectation pipeline."""
    op.drop_table("expectation_state")
    op.drop_index(
        "ix_expectation_failure_expectation_id",
        table_name="expectation_failure",
    )
    op.drop_table("expectation_failure")
    op.drop_table("health_expectation")
