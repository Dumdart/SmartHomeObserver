"""Complete expectation lifecycle persistence.

Revision ID: b3e7d2c9f610
Revises: a91e5c7d4b20
Create Date: 2026-09-03 00:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "b3e7d2c9f610"
down_revision: Union[str, Sequence[str], None] = "a91e5c7d4b20"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Persist rule metadata, state revisions, and deletion-safe snapshots."""
    with op.batch_alter_table("health_expectation") as batch_op:
        batch_op.add_column(
            sa.Column("name", sa.String(), nullable=False, server_default="")
        )
        batch_op.add_column(
            sa.Column("description", sa.Text(), nullable=False, server_default="")
        )

    with op.batch_alter_table("expectation_state") as batch_op:
        batch_op.add_column(
            sa.Column(
                "expectation_revision",
                sa.Integer(),
                nullable=False,
                server_default="1",
            )
        )

    with op.batch_alter_table("expectation_failure") as batch_op:
        batch_op.add_column(
            sa.Column("snapshot_broker_id", sa.Uuid(), nullable=True)
        )
        batch_op.add_column(
            sa.Column("snapshot_topic", sa.String(), nullable=True)
        )

    # Check deletion cannot remove historical incidents through the old
    # expectation foreign key. The snapshot columns retain reportable identity.
    metadata = sa.MetaData()
    failure_table = sa.Table(
        "expectation_failure",
        metadata,
        sa.Column("failure_id", sa.Uuid(), nullable=False),
        sa.Column("expectation_id", sa.Uuid(), nullable=False),
        sa.Column("first_failed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("occurrence_count", sa.Integer(), nullable=False),
        sa.Column("expected_revision", sa.Integer(), nullable=False),
        sa.Column("last_healthy_at", sa.DateTime(timezone=True)),
        sa.Column("recovered_at", sa.DateTime(timezone=True)),
        sa.Column("failure_code", sa.String()),
        sa.Column("evidence_summary", sa.Text()),
        sa.Column("snapshot_broker_id", sa.Uuid()),
        sa.Column("snapshot_topic", sa.String()),
        sa.PrimaryKeyConstraint("failure_id"),
    )
    with op.batch_alter_table(
        "expectation_failure",
        recreate="always",
        copy_from=failure_table,
    ):
        pass

    op.create_index(
        "ix_expectation_failure_expectation_id",
        "expectation_failure",
        ["expectation_id"],
    )


def downgrade() -> None:
    """Restore the original expectation lifecycle schema."""
    op.drop_index(
        "ix_expectation_failure_expectation_id",
        table_name="expectation_failure",
    )
    with op.batch_alter_table("expectation_failure") as batch_op:
        batch_op.drop_column("snapshot_topic")
        batch_op.drop_column("snapshot_broker_id")
        batch_op.create_foreign_key(
            "fk_expectation_failure_expectation_id_health_expectation",
            "health_expectation",
            ["expectation_id"],
            ["expectation_id"],
            ondelete="CASCADE",
        )
    with op.batch_alter_table("expectation_state") as batch_op:
        batch_op.drop_column("expectation_revision")
    with op.batch_alter_table("health_expectation") as batch_op:
        batch_op.drop_column("description")
        batch_op.drop_column("name")
