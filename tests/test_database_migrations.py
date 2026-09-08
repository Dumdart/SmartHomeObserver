from pathlib import Path

import pytest
from alembic import command
from sqlalchemy import MetaData, create_engine, inspect, text
from uuid import uuid4
from sqlalchemy.exc import IntegrityError

from topicgate.infrastructure.database.base import Base
from topicgate.infrastructure.database.database_context import DatabaseContext
from topicgate.infrastructure.database.migrations import (
    BASELINE_REVISION,
    EXPECTED_SCHEMA_REVISION,
    _alembic_config,
)
import topicgate.infrastructure.database.migrations as migrations
import topicgate.infrastructure.database.models  # noqa: F401


def test_new_database_is_migrated_to_head(tmp_path) -> None:
    database_path = tmp_path / "new.db"

    database = DatabaseContext(f"sqlite:///{database_path.as_posix()}")

    engine = create_engine(f"sqlite:///{database_path.as_posix()}")
    assert "mqtt_message" in inspect(engine).get_table_names()
    assert "observation_retention_policy" in inspect(engine).get_table_names()
    assert "control_operation_lease" in inspect(engine).get_table_names()
    assert "control_operation_state" in inspect(engine).get_table_names()
    with engine.connect() as connection:
        revision = connection.exec_driver_sql(
            "SELECT version_num FROM alembic_version"
        ).scalar_one()
        policy = connection.exec_driver_sql(
            "SELECT max_entries_per_broker, max_entries_total, max_age_seconds, "
            "max_persisted_payload_database_bytes_total "
            "FROM observation_retention_policy WHERE id = 1"
        ).one()
    assert revision != BASELINE_REVISION
    assert policy == (1_000, 10_000, None, 256 * 1024 * 1024)
    database.dispose()
    engine.dispose()


def test_previous_schema_is_upgraded_with_health_tables(tmp_path) -> None:
    database_path = tmp_path / "pre-health.db"
    url = f"sqlite:///{database_path.as_posix()}"
    engine = create_engine(url)
    with engine.begin() as connection:
        command.upgrade(_alembic_config(connection), "d2a4c6e8f010")
    engine.dispose()

    database = DatabaseContext(url)
    engine = create_engine(url)
    assert {
        "health_expectation",
        "expectation_state",
        "expectation_failure",
    }.issubset(inspect(engine).get_table_names())
    with engine.connect() as connection:
        revision = connection.exec_driver_sql(
            "SELECT version_num FROM alembic_version"
        ).scalar_one()
    assert revision == EXPECTED_SCHEMA_REVISION
    database.dispose()
    engine.dispose()


def test_alembic_config_uses_package_local_migrations() -> None:
    config = _alembic_config(None)
    package_database_dir = Path(migrations.__file__).resolve().parent

    assert Path(config.config_file_name).resolve() == (
        package_database_dir / "alembic.ini"
    )
    assert Path(config.get_main_option("script_location")).resolve() == (
        package_database_dir / "alembic"
    )


def test_sqlite_connections_enable_wal_and_busy_timeout(tmp_path) -> None:
    database_path = tmp_path / "coordination.db"
    database = DatabaseContext(f"sqlite:///{database_path.as_posix()}")

    with database.session() as session:
        journal_mode = session.execute(text("PRAGMA journal_mode")).scalar_one()
        busy_timeout = session.execute(text("PRAGMA busy_timeout")).scalar_one()

    assert journal_mode == "wal"
    assert busy_timeout == 5000
    database.dispose()


def test_existing_unversioned_database_is_stamped_then_upgraded(tmp_path) -> None:
    database_path = tmp_path / "legacy.db"
    url = f"sqlite:///{database_path.as_posix()}"
    engine = create_engine(url)
    post_baseline_tables = {
        Base.metadata.tables["mqtt_message"],
        Base.metadata.tables["observation_retention_policy"],
    }
    Base.metadata.create_all(
        engine,
        tables=[
            table
            for table in Base.metadata.sorted_tables
            if table not in post_baseline_tables
        ],
    )
    engine.dispose()


def test_existing_retention_limit_is_preserved_when_column_is_renamed(
    tmp_path,
) -> None:
    database_path = tmp_path / "retention-rename.db"
    url = f"sqlite:///{database_path.as_posix()}"
    engine = create_engine(url)
    with engine.begin() as connection:
        command.upgrade(_alembic_config(connection), "7c3e9f1a2b4d")
        connection.exec_driver_sql(
            "UPDATE observation_retention_policy "
            "SET max_database_bytes = 123456 WHERE id = 1"
        )
    engine.dispose()

    database = DatabaseContext(url)
    engine = create_engine(url)
    with engine.connect() as connection:
        columns = {
            column["name"]
            for column in inspect(connection).get_columns(
                "observation_retention_policy"
            )
        }
        value = connection.exec_driver_sql(
            "SELECT max_persisted_payload_database_bytes_total "
            "FROM observation_retention_policy WHERE id = 1"
        ).scalar_one()

    assert "max_database_bytes" not in columns
    assert "max_persisted_payload_database_bytes_total" in columns
    assert value == 123456
    database.dispose()
    engine.dispose()

    database = DatabaseContext(url)

    engine = create_engine(url)
    assert "mqtt_message" in inspect(engine).get_table_names()
    assert "observation_retention_policy" in inspect(engine).get_table_names()
    with engine.connect() as connection:
        revision = connection.exec_driver_sql(
            "SELECT version_num FROM alembic_version"
        ).scalar_one()
    assert revision != BASELINE_REVISION
    database.dispose()
    engine.dispose()


def test_retention_policy_database_constraints_reject_invalid_values(
    tmp_path,
) -> None:
    database_path = tmp_path / "constraints.db"
    database = DatabaseContext(f"sqlite:///{database_path.as_posix()}")

    try:
        with pytest.raises(IntegrityError):
            with database.transaction() as session:
                session.execute(
                    Base.metadata.tables["observation_retention_policy"]
                    .update()
                    .where(
                        Base.metadata.tables[
                            "observation_retention_policy"
                        ].c.id
                        == 1
                    )
                    .values(max_entries_per_broker=0)
                )
    finally:
        database.dispose()


def test_health_expectations_migrate_into_broker_default_profile(tmp_path) -> None:
    database_path = tmp_path / "pre-profiles.db"
    url = f"sqlite:///{database_path.as_posix()}"
    engine = create_engine(url)
    broker_id = uuid4()
    expectation_id = uuid4()
    workspace_id = uuid4()
    with engine.begin() as connection:
        command.upgrade(_alembic_config(connection), "b3e7d2c9f610")
        metadata = MetaData()
        metadata.reflect(connection)
        config_id = connection.execute(
            metadata.tables["mqtt_config"].insert().values(
                host="localhost", port=1883, username="", use_tls=False
            )
        ).inserted_primary_key[0]
        connection.execute(
            metadata.tables["broker_profile"].insert().values(
                id=broker_id.hex, name="Broker", position=0, is_active=True,
                mqtt_config_id=config_id,
            )
        )
        connection.execute(
            metadata.tables["observer_workspace"].insert().values(
                id=workspace_id.hex, profile_id=broker_id.hex
            )
        )
        connection.execute(
            metadata.tables["health_expectation"].insert().values(
                expectation_id=expectation_id.hex, revision=4, enabled=True,
                severity="critical",
                target={"kind": "topic", "broker_id": str(broker_id), "topic": "status"},
                condition={"kind": "topic_exists"}, actions=[], name="Legacy",
                description="",
            )
        )
    with engine.begin() as connection:
        command.upgrade(_alembic_config(connection), "head")
        profile = connection.exec_driver_sql(
            "SELECT profile_id, name, is_default FROM diagnostic_profile"
        ).one()
        migrated = connection.exec_driver_sql(
            "SELECT expectation_id, revision, profile_id, rule_id FROM health_expectation"
        ).one()
    assert profile[1:] == ("Default", 1)
    assert str(migrated[0]).replace("-", "") == expectation_id.hex
    assert migrated[1] == 4
    assert migrated[2] == profile[0]
    assert migrated[3] == f"legacy-{expectation_id}"

    with engine.begin() as connection:
        command.downgrade(_alembic_config(connection), "b3e7d2c9f610")
        assert "diagnostic_profile" not in inspect(connection).get_table_names()
        columns = {
            column["name"]
            for column in inspect(connection).get_columns("health_expectation")
        }
        remaining = connection.exec_driver_sql(
            "SELECT expectation_id, revision FROM health_expectation"
        ).one()
    assert "profile_id" not in columns
    assert str(remaining[0]).replace("-", "") == expectation_id.hex
    assert remaining[1] == 4
    engine.dispose()


def test_profile_migration_preflights_orphaned_expectations(tmp_path) -> None:
    database_path = tmp_path / "orphan.db"
    url = f"sqlite:///{database_path.as_posix()}"
    engine = create_engine(url)
    expectation_id = uuid4()
    with engine.begin() as connection:
        command.upgrade(_alembic_config(connection), "b3e7d2c9f610")
        metadata = MetaData()
        metadata.reflect(connection)
        connection.execute(
            metadata.tables["health_expectation"].insert().values(
                expectation_id=expectation_id.hex, revision=1, enabled=True,
                severity="critical",
                target={"kind": "broker", "broker_id": str(uuid4())},
                condition={"kind": "equal", "expected_value": "online"},
                actions=[], name="Orphan", description="",
            )
        )
    with engine.begin() as connection:
        with pytest.raises(Exception, match=str(expectation_id)):
            command.upgrade(_alembic_config(connection), "head")
    engine.dispose()
