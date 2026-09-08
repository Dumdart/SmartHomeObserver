"""Add independent history retention limits and pruning summaries."""
from alembic import op
import sqlalchemy as sa

revision = "f70b1a242536"
down_revision = "e6fa09131425"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "history_retention_policy",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("max_age_seconds", sa.Integer(), nullable=True),
        sa.Column("max_events_per_broker", sa.Integer(), nullable=False),
        sa.Column("max_events_per_topic", sa.Integer(), nullable=True),
        sa.Column("max_payload_bytes", sa.Integer(), nullable=False),
        sa.Column("prune_batch_size", sa.Integer(), nullable=False),
        sa.Column("prune_interval_seconds", sa.Integer(), nullable=False),
        sa.CheckConstraint("max_age_seconds IS NULL OR max_age_seconds > 0"),
        sa.CheckConstraint("max_events_per_broker > 0"),
        sa.CheckConstraint("max_events_per_topic IS NULL OR max_events_per_topic > 0"),
        sa.CheckConstraint("max_payload_bytes > 0"),
        sa.CheckConstraint("prune_batch_size BETWEEN 1 AND 500"),
        sa.CheckConstraint("prune_interval_seconds > 0"),
    )
    op.execute("INSERT INTO history_retention_policy VALUES (1, 604800, 100000, NULL, 268435456, 500, 60)")
    op.create_table(
        "history_retention_state",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("generation", sa.Integer(), nullable=False),
        sa.Column("last_pruned_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("evictions", sa.JSON(), nullable=False),
        sa.Column("enforcement_pending", sa.Boolean(), nullable=False),
    )
    op.execute("INSERT INTO history_retention_state VALUES (1, 0, NULL, '{}', 0)")


def downgrade() -> None:
    op.drop_table("history_retention_state")
    op.drop_table("history_retention_policy")
