"""Persist broker-scoped diagnostic profiles.

Revision ID: c4d8a7e1f302
Revises: b3e7d2c9f610
Create Date: 2026-09-08 00:00:00.000000
"""
import json
from datetime import datetime, timezone
from typing import Sequence, Union
from uuid import NAMESPACE_URL, UUID, uuid5

from alembic import op
import sqlalchemy as sa


revision: str = "c4d8a7e1f302"
down_revision: Union[str, Sequence[str], None] = "b3e7d2c9f610"
branch_labels = None
depends_on = None


def _default_profile_id(broker_id: UUID) -> UUID:
    return uuid5(NAMESPACE_URL, f"topicgate:diagnostic-profile:default:{broker_id}")


def upgrade() -> None:
    bind = op.get_bind()
    brokers = {UUID(str(row[0])) for row in bind.exec_driver_sql("SELECT id FROM broker_profile")}
    expectations = list(bind.exec_driver_sql("SELECT expectation_id, target FROM health_expectation"))
    orphaned = []
    targets: dict[UUID, UUID] = {}
    for expectation_id, raw_target in expectations:
        value = json.loads(raw_target) if isinstance(raw_target, str) else raw_target
        try:
            broker_id = UUID(value["broker_id"])
        except (KeyError, TypeError, ValueError):
            orphaned.append(str(UUID(str(expectation_id))))
            continue
        if broker_id not in brokers:
            orphaned.append(str(UUID(str(expectation_id))))
        else:
            targets[UUID(str(expectation_id))] = broker_id
    if orphaned:
        raise RuntimeError(
            "Cannot migrate health expectations with orphaned broker references: "
            + ", ".join(sorted(orphaned))
        )

    op.create_table(
        "diagnostic_profile",
        sa.Column("profile_id", sa.Uuid(), nullable=False),
        sa.Column("broker_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("normalized_name", sa.String(200), nullable=False),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column("pack_id", sa.String(), nullable=True),
        sa.Column("pack_version", sa.String(), nullable=True),
        sa.Column("rule_schema_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("is_default", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["broker_id"], ["broker_profile.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("profile_id"),
        sa.UniqueConstraint("broker_id", "normalized_name", name="uq_diagnostic_profile_broker_name"),
    )
    now = datetime.now(timezone.utc)
    profile_table = sa.table(
        "diagnostic_profile",
        sa.column("profile_id", sa.Uuid()), sa.column("broker_id", sa.Uuid()),
        sa.column("name"), sa.column("normalized_name"), sa.column("description"),
        sa.column("pack_id"), sa.column("pack_version"), sa.column("rule_schema_version"),
        sa.column("is_default"), sa.column("created_at"), sa.column("updated_at"),
    )
    if brokers:
        op.bulk_insert(profile_table, [
            {"profile_id": _default_profile_id(broker), "broker_id": broker,
             "name": "Default", "normalized_name": "default", "description": "",
             "pack_id": None, "pack_version": None, "rule_schema_version": 1,
             "is_default": True, "created_at": now, "updated_at": now}
            for broker in sorted(brokers, key=str)
        ])

    with op.batch_alter_table("health_expectation") as batch:
        batch.alter_column("enabled", existing_type=sa.Boolean(), nullable=True)
        batch.alter_column("severity", existing_type=sa.String(), nullable=True)
        batch.alter_column("target", existing_type=sa.JSON(), nullable=True)
        batch.alter_column("condition", existing_type=sa.JSON(), nullable=True)
        batch.alter_column("actions", existing_type=sa.JSON(), nullable=True)
        batch.alter_column("name", existing_type=sa.String(), nullable=True)
        batch.alter_column("description", existing_type=sa.Text(), nullable=True)
        batch.add_column(sa.Column("profile_id", sa.Uuid(), nullable=True))
        batch.add_column(sa.Column("rule_id", sa.String(128), nullable=False, server_default=""))
        batch.add_column(sa.Column("normalized_rule_id", sa.String(128), nullable=False, server_default=""))
        batch.add_column(sa.Column("rule_schema_version", sa.Integer(), nullable=False, server_default="1"))
        batch.add_column(sa.Column("source_kind", sa.String(), nullable=False, server_default="custom"))
        batch.add_column(sa.Column("pack_override", sa.JSON(), nullable=True))

    for expectation_id, broker_id in targets.items():
        rule_id = f"legacy-{expectation_id}"
        bind.execute(
            sa.text("UPDATE health_expectation SET profile_id=:profile_id, rule_id=:rule_id, normalized_rule_id=:rule_id WHERE expectation_id=:expectation_id"),
            {"profile_id": _default_profile_id(broker_id).hex, "rule_id": rule_id, "expectation_id": expectation_id.hex},
        )

    with op.batch_alter_table("health_expectation") as batch:
        batch.create_foreign_key("fk_health_expectation_profile", "diagnostic_profile", ["profile_id"], ["profile_id"], ondelete="CASCADE")
        batch.create_unique_constraint("uq_health_expectation_profile_rule", ["profile_id", "normalized_rule_id"])


def downgrade() -> None:
    op.execute("DELETE FROM health_expectation WHERE source_kind = 'pack'")
    with op.batch_alter_table("health_expectation") as batch:
        batch.drop_constraint("uq_health_expectation_profile_rule", type_="unique")
        batch.drop_constraint("fk_health_expectation_profile", type_="foreignkey")
        batch.drop_column("pack_override")
        batch.drop_column("source_kind")
        batch.drop_column("rule_schema_version")
        batch.drop_column("normalized_rule_id")
        batch.drop_column("rule_id")
        batch.drop_column("profile_id")
        batch.alter_column("description", existing_type=sa.Text(), nullable=False, server_default="")
        batch.alter_column("name", existing_type=sa.String(), nullable=False, server_default="")
        batch.alter_column("actions", existing_type=sa.JSON(), nullable=False)
        batch.alter_column("condition", existing_type=sa.JSON(), nullable=False)
        batch.alter_column("target", existing_type=sa.JSON(), nullable=False)
        batch.alter_column("severity", existing_type=sa.String(), nullable=False)
        batch.alter_column("enabled", existing_type=sa.Boolean(), nullable=False)
    op.drop_table("diagnostic_profile")
