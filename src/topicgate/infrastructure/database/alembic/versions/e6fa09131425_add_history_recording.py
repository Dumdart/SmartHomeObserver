"""Persist per-broker opt-in and recording completeness checkpoints."""
from alembic import op
import sqlalchemy as sa

revision = "e6fa09131425"
down_revision = "d5e9b8f20314"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "history_recording_setting",
        sa.Column("broker_id", sa.Uuid(), sa.ForeignKey("broker_profile.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.create_table(
        "history_recording_session",
        sa.Column("session_id", sa.Uuid(), primary_key=True),
        sa.Column("broker_id", sa.Uuid(), sa.ForeignKey("broker_profile.id", ondelete="CASCADE"), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("closed", sa.Boolean(), nullable=False, server_default=sa.false()),
        *(sa.Column(name, sa.Integer(), nullable=False, server_default="0")
          for name in ("admitted", "committed", "dropped", "failed")),
    )
    op.create_index("ix_history_recording_session_broker_id", "history_recording_session", ["broker_id"])


def downgrade() -> None:
    op.drop_table("history_recording_session")
    op.drop_table("history_recording_setting")
