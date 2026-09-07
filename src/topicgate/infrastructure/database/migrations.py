from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import inspect
from sqlalchemy.engine import Connection, Engine


BASELINE_REVISION = "93fa5748f4b5"
EXPECTED_SCHEMA_REVISION = "b3e7d2c9f610"
BASELINE_TABLES = {
    "app_config",
    "broker_profile",
    "mqtt_config",
    "observer_workspace",
    "subscription",
    "workspace_subscription",
}

_DATABASE_PACKAGE_DIR = Path(__file__).resolve().parent
_ALEMBIC_CONFIG_PATH = _DATABASE_PACKAGE_DIR / "alembic.ini"
_ALEMBIC_SCRIPT_LOCATION = _DATABASE_PACKAGE_DIR / "alembic"


def upgrade_database(engine: Engine) -> None:
    """Upgrade a new or existing TopicGate database to the latest schema."""
    with engine.connect() as connection:
        if engine.dialect.name == "sqlite":
            # Check concurrent desktop/MCP startup cannot run Alembic in parallel.
            connection.exec_driver_sql("BEGIN IMMEDIATE")
        else:
            connection.begin()
        config = _alembic_config(connection)
        tables = set(inspect(connection).get_table_names())

        # Check databases created before Alembic was introduced. Their existing
        # tables represent the baseline revision and must not be recreated.
        if "alembic_version" not in tables and tables:
            missing_tables = BASELINE_TABLES - tables
            if missing_tables:
                missing = ", ".join(sorted(missing_tables))
                raise RuntimeError(
                    "Cannot migrate unversioned TopicGate database; "
                    f"baseline tables are missing: {missing}"
                )
            command.stamp(config, BASELINE_REVISION)

        try:
            command.upgrade(config, "head")
            connection.commit()
        except Exception:
            connection.rollback()
            raise


def _alembic_config(connection: Connection) -> Config:
    config = Config(str(_ALEMBIC_CONFIG_PATH))
    config.set_main_option("script_location", str(_ALEMBIC_SCRIPT_LOCATION))
    config.attributes["connection"] = connection
    return config
