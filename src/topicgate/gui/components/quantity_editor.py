from PySide6.QtCore import Signal
from PySide6.QtWidgets import QComboBox, QHBoxLayout, QLineEdit, QWidget


class QuantityEditor(QWidget):
    changed = Signal()

    def __init__(self, units: tuple[str, ...], object_name: str) -> None:
        super().__init__()
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.value = QLineEdit()
        self.value.setObjectName(f"{object_name}Value")
        self.value.setAccessibleName(f"{object_name} value")
        self.unit = QComboBox()
        self.unit.setObjectName(f"{object_name}Unit")
        self.unit.setAccessibleName(f"{object_name} unit")
        self.unit.addItems(units)
        layout.addWidget(self.value, 1)
        layout.addWidget(self.unit)
        self.value.textChanged.connect(lambda _text: self.changed.emit())
        self.unit.currentIndexChanged.connect(lambda _index: self.changed.emit())

