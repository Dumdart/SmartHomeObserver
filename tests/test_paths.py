from pathlib import Path

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication

from topicgate.paths import asset_path, prepare_database_path, sqlite_url


@pytest.fixture(scope="module", autouse=True)
def qt_application():
    app = QApplication.instance() or QApplication([])
    yield app


def test_fresh_installation_uses_new_database_path(tmp_path: Path) -> None:
    target = prepare_database_path(tmp_path / "data")

    assert target == tmp_path / "data" / "topicgate.db"
    assert not target.exists()


def test_application_icon_assets_are_available_from_the_package() -> None:
    icon_path = asset_path("icon.png")

    assert Path(icon_path).is_file()
    assert Path(asset_path("icon.svg")).is_file()
    assert not QIcon(icon_path).isNull()


def test_control_icon_catalog_is_complete_and_rejects_strings() -> None:
    import pytest
    from topicgate.gui.icons import IconName, icon

    assert {name.value for name in IconName} == {
        path.stem for path in Path(asset_path("icons")).glob("*.svg")
    }
    for name in IconName:
        assert not icon(name).isNull()
    with pytest.raises(TypeError, match="IconName"):
        icon("edit")
    for legacy in ("edit.svg", "delete.svg"):
        assert not Path(asset_path(legacy)).exists()


def test_control_icon_missing_asset_fails_explicitly(monkeypatch, tmp_path) -> None:
    import pytest
    from topicgate.gui import icons

    monkeypatch.setattr(icons, "asset_path", lambda name: str(tmp_path / name))
    with pytest.raises(FileNotFoundError):
        icons.icon(icons.IconName.EDIT)


def test_gui_control_icons_use_the_shared_catalog() -> None:
    import ast
    import topicgate.gui.icons

    root = Path(topicgate.gui.icons.__file__).parent
    for path in root.rglob("*.py"):
        if path.name == "icons.py":
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            if isinstance(node.func, ast.Attribute):
                assert node.func.attr not in {"standardIcon", "fromTheme"}, path
            if isinstance(node.func, ast.Name) and node.func.id == "QIcon":
                assert path.name in {"app.py", "main_window.py", "connection_controls.py"}, path
                if path.name != "connection_controls.py":
                    assert ast.unparse(node.args[0]) == "asset_path('icon.png')", path
