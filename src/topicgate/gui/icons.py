"""Shared semantic catalog for desktop control icons."""

from enum import StrEnum
from pathlib import Path

from PySide6.QtGui import QIcon

from topicgate.paths import asset_path


class IconName(StrEnum):
    BROKER = "broker"
    CLOSE = "close"
    CREATE = "create"
    DELETE = "delete"
    EDIT = "edit"
    HELP = "help"
    OBSERVER_TREE = "observer-tree"
    SETTINGS = "settings"


def icon(name: IconName) -> QIcon:
    """Load a packaged control icon, rejecting undeclared names and missing assets."""
    if not isinstance(name, IconName):
        raise TypeError("Control icons require an IconName")
    path = Path(asset_path(f"icons/{name.value}.svg"))
    if not path.is_file():
        raise FileNotFoundError(path)
    return QIcon(str(path))
