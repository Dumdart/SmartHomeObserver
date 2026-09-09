"""Add append-only observed events without manufacturing historical receipts."""
from alembic import op
import sqlalchemy as sa

revision = "d5e9b8f20314"
down_revision = "c4d8a7e1f302"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "observation_history_clock",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("last_sequence", sa.Integer(), nullable=False),
    )
    op.execute("INSERT INTO observation_history_clock (id, last_sequence) VALUES (1, 0)")
    op.create_table(
        "observation_event",
        sa.Column("observation_id", sa.Uuid(), primary_key=True),
        sa.Column("sequence", sa.Integer(), nullable=False, unique=True),
        sa.Column("broker_id", sa.Uuid(), sa.ForeignKey("broker_profile.id", ondelete="CASCADE"), nullable=False),
        sa.Column("topic", sa.String(), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("payload", sa.LargeBinary(), nullable=False),
        sa.Column("payload_size", sa.Integer(), nullable=False),
        sa.Column("qos", sa.Integer(), nullable=False),
        sa.Column("retain", sa.Boolean(), nullable=False),
        sa.Column("is_truncated", sa.Boolean(), nullable=False),
        sa.Column("provenance", sa.String(), nullable=False),
    )
    op.create_index("ix_history_topic_order", "observation_event", ["broker_id", "topic", "received_at", "observation_id"])
    op.create_index("ix_history_broker_order", "observation_event", ["broker_id", "received_at", "observation_id"])
    op.create_index("ix_history_global_order", "observation_event", ["received_at", "observation_id"])


def downgrade() -> None:
    op.drop_table("observation_event")
    op.drop_table("observation_history_clock")
