from PySide6.QtCore import QEvent, QObject, QSize, Qt
from PySide6.QtGui import QResizeEvent, QShowEvent
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)


WORKSPACE_CONTROL_HEIGHT = 32
WORKSPACE_HEADER_HEIGHT = 24


class WorkspacePane(QFrame):
    """Common framed pane used inside the workspace splitter."""

    def __init__(
        self,
        title: str,
        minimum_hint_width: int = 240,
    ) -> None:
        super().__init__()
        self.setProperty("workspacePane", True)
        self._minimum_hint_width = minimum_hint_width
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Expanding,
        )

        self.content_layout = QVBoxLayout(self)
        self.header = QWidget()
        self.header.setObjectName("workspaceHeader")
        self.header.setFixedHeight(WORKSPACE_HEADER_HEIGHT)
        self.header_layout = QHBoxLayout(self.header)
        self.header_layout.setContentsMargins(0, 0, 0, 0)
        self.heading = QLabel(title)
        self.heading.setObjectName("workspaceHeading")
        self.heading.setTextFormat(Qt.TextFormat.PlainText)
        self.heading.setMinimumWidth(0)
        self.heading.setSizePolicy(
            QSizePolicy.Policy.Ignored,
            QSizePolicy.Policy.Preferred,
        )
        self.heading.setToolTip(title)
        self.heading.setAccessibleName(title)
        self.heading.installEventFilter(self)
        self._heading_text = title
        self.header_layout.addWidget(self.heading, 1)
        self.content_layout.addWidget(self.header)

    def minimumSizeHint(self) -> QSize:
        hint = super().minimumSizeHint()
        hint.setWidth(self._minimum_hint_width)
        return hint

    def set_heading(self, text: str, accessible_name: str | None = None) -> None:
        self._heading_text = text
        self.heading.setToolTip(text)
        self.heading.setAccessibleName(accessible_name or text)
        self._update_elided_heading()

    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        self._update_elided_heading()

    def showEvent(self, event: QShowEvent) -> None:
        super().showEvent(event)
        self._update_elided_heading()

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        if watched is self.heading and event.type() == QEvent.Type.Resize:
            self._update_elided_heading()
        return super().eventFilter(watched, event)

    def _update_elided_heading(self) -> None:
        if len(self._heading_text) <= 80:
            self.heading.setText(self._heading_text)
            return
        occupied_width = sum(
            item.widget().sizeHint().width()
            for index in range(self.header_layout.count())
            if (item := self.header_layout.itemAt(index)).widget() is not None
            and item.widget() is not self.heading
        )
        margins = self.content_layout.contentsMargins()
        available_width = max(
            60,
            min(
                self.heading.width(),
                self.width()
                - margins.left()
                - margins.right()
                - occupied_width
                - 24,
            ),
        )
        if not self.isVisible():
            self.heading.setText(self._heading_text)
            return
        self.heading.setText(
            self.heading.fontMetrics().elidedText(
                self._heading_text,
                Qt.TextElideMode.ElideMiddle,
                available_width,
            )
        )
