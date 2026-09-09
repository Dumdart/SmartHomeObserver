from pathlib import Path

from PySide6.QtGui import QIcon

from topicgate.paths import asset_path, prepare_database_path, sqlite_url


def test_fresh_installation_uses_new_database_path(tmp_path: Path) -> None:
    target = prepare_database_path(tmp_path / "data")

    assert target == tmp_path / "data" / "topicgate.db"
    assert not target.exists()


def test_application_icon_assets_are_available_from_the_package() -> None:
    icon_path = asset_path("icon.png")

    assert Path(icon_path).is_file()
    assert Path(asset_path("icon.svg")).is_file()
    assert not QIcon(icon_path).isNull()
