

import asyncio
import ctypes
import sys

from PySide6.QtCore import QCoreApplication, QRectF, Qt
from PySide6.QtGui import (
    QColor,
    QFont,
    QGuiApplication,
    QIcon,
    QPainter,
    QPen,
    QPixmap,
)
from PySide6.QtWidgets import QApplication, QSplashScreen
from qasync import QEventLoop

from topicgate.app.app_dependencies import AppDependencies
from topicgate.app.services.service_container import ServiceContainer
from topicgate.gui.main_window import MainWindow
from topicgate.gui.main_view_model import MainViewModel
from topicgate.gui.theme import apply_light_theme
from topicgate.paths import asset_path


STARTUP_SCREEN_SIZE = (560, 280)


def create_startup_pixmap(logo: QPixmap) -> QPixmap:
    """Create a startup surface using the desktop application's visual tokens."""
    width, height = STARTUP_SCREEN_SIZE
    pixmap = QPixmap(width, height)
    pixmap.fill(QColor("#f3f4f6"))

    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)

    painter.setPen(QPen(QColor("#c8ced6"), 1))
    painter.setBrush(QColor("#ffffff"))
    painter.drawRoundedRect(QRectF(4.5, 4.5, 551, 271), 8, 8)

    if not logo.isNull():
        scaled_logo = logo.scaled(
            52,
            52,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        painter.drawPixmap(28, 26, scaled_logo)

    painter.setPen(QColor("#202124"))
    painter.setFont(QFont("Segoe UI", 22, QFont.Weight.DemiBold))
    painter.drawText(
        QRectF(96, 25, 430, 34),
        Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
        "TopicGate",
    )

    painter.setPen(QColor("#4b5563"))
    painter.setFont(QFont("Segoe UI", 10))
    painter.drawText(
        QRectF(96, 57, 430, 22),
        Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
        "MQTT observer desktop",
    )

    painter.setPen(QPen(QColor("#e4e7eb"), 1))
    painter.drawLine(28, 96, 532, 96)

    painter.setPen(QColor("#405d7a"))
    painter.setFont(QFont("Segoe UI", 9, QFont.Weight.DemiBold))
    painter.drawText(
        QRectF(28, 119, 504, 20),
        Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
        "SECURE LOCAL MQTT ACCESS",
    )

    painter.setPen(QColor("#202124"))
    painter.setFont(QFont("Segoe UI", 14, QFont.Weight.DemiBold))
    painter.drawText(
        QRectF(28, 143, 504, 26),
        Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
        "Preparing your observer workspace",
    )

    painter.setPen(QColor("#4b5563"))
    painter.setFont(QFont("Segoe UI", 10))
    painter.drawText(
        QRectF(28, 174, 504, 24),
        Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
        "TopicGate stores broker state locally; connected agents can inspect observed MQTT data.",
    )

    painter.setPen(QPen(QColor("#b8c9db"), 1))
    painter.setBrush(QColor("#e7eff7"))
    painter.drawRoundedRect(QRectF(28.5, 218.5, 503, 37), 5, 5)
    painter.end()
    return pixmap


class StartupSplashScreen(QSplashScreen):
    """Render startup progress in the same status treatment as the desktop UI."""

    def drawContents(self, painter: QPainter) -> None:
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor("#405d7a"))
        painter.drawEllipse(QRectF(43, 233, 8, 8))

        painter.setPen(QColor("#334e68"))
        painter.setFont(QFont("Segoe UI", 10, QFont.Weight.DemiBold))
        painter.drawText(
            QRectF(63, 219, 450, 37),
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            self.message(),
        )


class App:
    def __init__(
        self,
        qt_application: QApplication,
        splash_screen: QSplashScreen | None = None,
    ):
        self._qt_application = qt_application
        self._splash_screen = splash_screen
        self._dependencies = AppDependencies(control_owner="desktop")
        self._services = ServiceContainer(self._dependencies)
        self._view_model = MainViewModel(
            runtime=self._dependencies.runtime,
            snapshot_service=self._dependencies.snapshot_service,
            mcp_setup_service=self._dependencies.mcp_setup,
            health_query_service=self._dependencies.health_query_service,
            expectation_management_service=(
                self._dependencies.expectation_management_service
            ),
            support_bundle_exporter=self._dependencies.support_bundle_exporter,
            support_bundle_archive_writer=(
                self._dependencies.support_bundle_archive_writer
            ),
        )
        self._window = MainWindow(
            self._view_model,
            diagnostic_profile_editor=self._dependencies.diagnostic_profile_editor,
        )


    async def run(self) -> int:
        # Check that closing the last window does not stop qasync before
        # asynchronous cleanup has completed.
        self._qt_application.setQuitOnLastWindowClosed(False)
        stopped = asyncio.get_running_loop().create_future()

        def request_shutdown() -> None:
            if not stopped.done():
                stopped.set_result(None)

        self._qt_application.lastWindowClosed.connect(request_shutdown)
        try:
            self._show_startup_message("Connecting to MQTT...")
            try:
                await self._services.start_services()
            except ConnectionError as error:
                self._view_model.log_message.emit(
                    f"Initial MQTT connection failed: {error}"
                )
            self._show_startup_message("Preparing TopicGate workspace...")
            await self._view_model.start()
            self._window.show()
            splash_screen = getattr(self, "_splash_screen", None)
            if splash_screen is not None:
                splash_screen.finish(self._window)
            await stopped
            return 0
        finally:
            try:
                try:
                    cancel_operations = getattr(
                        self._window,
                        "cancel_pending_operations",
                        None,
                    )
                    if cancel_operations is not None:
                        await cancel_operations()
                finally:
                    await self._view_model.stop()
            finally:
                await self._services.stop_services()

    def _show_startup_message(self, message: str) -> None:
        splash_screen = getattr(self, "_splash_screen", None)
        if splash_screen is not None:
            splash_screen.showMessage(
                message,
                Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                QColor("#334e68"),
            )
            self._qt_application.processEvents()


def configure_windows_identity() -> None:
    if sys.platform == "win32":
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
            "Dumdart.TopicGate"
        )


def configure_application_identity() -> None:
    QCoreApplication.setOrganizationName("Dumdart")
    QCoreApplication.setApplicationName("TopicGate")
    QGuiApplication.setApplicationDisplayName("TopicGate Desktop")


def run() -> int:
    configure_windows_identity()
    configure_application_identity()

    qt_application = QApplication(sys.argv)
    icon = QIcon(asset_path("icon.png"))
    qt_application.setWindowIcon(icon)

    apply_light_theme(qt_application)

    logo = QPixmap(asset_path("icon.png"))
    splash_screen = StartupSplashScreen(
        create_startup_pixmap(logo),
        Qt.WindowType.WindowStaysOnTopHint,
    )
    splash_screen.showMessage(
        "Starting TopicGate...",
        Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
        QColor("#334e68"),
    )
    splash_screen.show()
    qt_application.processEvents()

    event_loop = QEventLoop(qt_application)
    asyncio.set_event_loop(event_loop)

    app = App(qt_application, splash_screen)

    with event_loop:
        return event_loop.run_until_complete(app.run())
