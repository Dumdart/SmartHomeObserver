from PySide6.QtGui import QIcon

from topicgate.paths import asset_path


def edit_icon() -> QIcon:
    return QIcon(asset_path("edit.svg"))


def delete_icon() -> QIcon:
    return QIcon(asset_path("delete.svg"))
