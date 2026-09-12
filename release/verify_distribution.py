import os
import subprocess
import sys
import tempfile
import venv
from pathlib import Path


_RUNTIME_PROBE = """
from importlib.metadata import distribution
from pathlib import Path

from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, inspect, text

import topicgate
from topicgate.infrastructure.database.migrations import (
    _alembic_config,
    upgrade_database,
)

expected_entry_points = {
    ("console_scripts", "topicgate"): "topicgate.mcp.server:run",
    ("console_scripts", "topicgate-cli"): "topicgate.cli.topicgate_cli:main",
    ("gui_scripts", "topicgate-gui"): "topicgate.gui.app:run",
}
installed_entry_points = {
    (entry.group, entry.name): entry
    for entry in distribution("topicgate").entry_points
    if (entry.group, entry.name) in expected_entry_points
}
assert set(installed_entry_points) == set(expected_entry_points), (
    installed_entry_points
)
for key, expected_value in expected_entry_points.items():
    entry = installed_entry_points[key]
    assert entry.value == expected_value, entry
    assert callable(entry.load()), entry

package_dir = Path(topicgate.__file__).resolve().parent
config = _alembic_config(None)
config_path = Path(config.config_file_name).resolve()
script_path = Path(config.get_main_option("script_location")).resolve()

assert package_dir in config_path.parents, config_path
assert package_dir in script_path.parents, script_path
assert config_path.is_file(), config_path
assert script_path.is_dir(), script_path

expected_revision = ScriptDirectory.from_config(config).get_current_head()
database_path = Path("topicgate.db").resolve()
engine = create_engine(f"sqlite:///{database_path.as_posix()}")
upgrade_database(engine)

with engine.connect() as connection:
    installed_revision = connection.execute(
        text("SELECT version_num FROM alembic_version")
    ).scalar_one()

assert installed_revision == expected_revision
assert "observation_event" in inspect(engine).get_table_names()
assert "observation_history_clock" in inspect(engine).get_table_names()
assert {"history_recording_setting", "history_recording_session",
        "history_retention_policy", "history_retention_state"}.issubset(
    inspect(engine).get_table_names()
)
print(f"Verified installed TopicGate wheel at {package_dir}")
print(f"Verified fresh database migration {installed_revision}")
"""


def verify_distribution(wheel_path: Path) -> None:
    wheel_path = wheel_path.resolve()
    if not wheel_path.is_file() or wheel_path.suffix != ".whl":
        raise ValueError(f"Expected a built wheel, got: {wheel_path}")

    with tempfile.TemporaryDirectory(prefix="topicgate-wheel-") as directory:
        verification_dir = Path(directory)
        environment_dir = verification_dir / "environment"
        runtime_dir = verification_dir / "runtime"
        runtime_dir.mkdir()

        venv.EnvBuilder(with_pip=True).create(environment_dir)
        python_path = _environment_python(environment_dir)
        subprocess.run(
            [
                str(python_path),
                "-m",
                "pip",
                "install",
                "--no-cache-dir",
                str(wheel_path),
            ],
            check=True,
        )

        environment = os.environ.copy()
        environment["TOPICGATE_DATA_DIR"] = str(runtime_dir / "data")
        subprocess.run(
            [str(python_path), "-c", _RUNTIME_PROBE],
            cwd=runtime_dir,
            env=environment,
            check=True,
        )
        subprocess.run(
            [str(_environment_script(environment_dir, "topicgate")), "--help"],
            cwd=runtime_dir,
            env=environment,
            check=True,
        )
        subprocess.run(
            [str(_environment_script(environment_dir, "topicgate-cli")), "--help"],
            cwd=runtime_dir,
            env=environment,
            check=True,
        )
        gui_script = _environment_script(environment_dir, "topicgate-gui")
        if not gui_script.is_file():
            raise RuntimeError(f"Missing installed entry point: {gui_script}")


def _environment_python(environment_dir: Path) -> Path:
    executable = "python.exe" if os.name == "nt" else "python"
    return _environment_scripts_dir(environment_dir) / executable


def _environment_script(environment_dir: Path, name: str) -> Path:
    suffix = ".exe" if os.name == "nt" else ""
    return _environment_scripts_dir(environment_dir) / f"{name}{suffix}"


def _environment_scripts_dir(environment_dir: Path) -> Path:
    return environment_dir / ("Scripts" if os.name == "nt" else "bin")


def main(argv: list[str] | None = None) -> None:
    arguments = sys.argv[1:] if argv is None else argv
    if len(arguments) != 1:
        raise SystemExit("Usage: python -m release.verify_distribution DIST.whl")
    verify_distribution(Path(arguments[0]))


if __name__ == "__main__":
    main()
