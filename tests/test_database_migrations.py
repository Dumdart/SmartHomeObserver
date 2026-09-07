from pathlib import Path

import pytest
from alembic import command
from sqlalchemy import create_engine, inspect, text
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
