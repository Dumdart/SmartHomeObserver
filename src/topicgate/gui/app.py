

import asyncio
import ctypes
import sys

from PySide6.QtCore import QCoreApplication, Qt
from PySide6.QtGui import QColor, QFont, QGuiApplication, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import QApplication, QSplashScreen
from qasync import QEventLoop

from topicgate.app.app_dependencies import AppDependencies
from topicgate.app.services.service_container import ServiceContainer
from topicgate.gui.main_window import MainWindow
from topicgate.gui.main_view_model import MainViewModel
from topicgate.gui.theme import apply_light_theme
from topicgate.paths import asset_path


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
        )
        self._window = MainWindow(self._view_model)


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
                Qt.AlignmentFlag.AlignBottom | Qt.AlignmentFlag.AlignHCenter,
                Qt.GlobalColor.white,
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

    splash_pixmap = QPixmap(520, 220)
    splash_pixmap.fill(QColor("#405d7a"))
    painter = QPainter(splash_pixmap)
    logo = QPixmap(asset_path("icon.png"))
    if not logo.isNull():
        painter.drawPixmap(228, 24, 64, 64, logo)
    painter.setPen(Qt.GlobalColor.white)
    painter.setFont(QFont("Segoe UI", 24, QFont.Weight.DemiBold))
    painter.drawText(0, 124, 520, 38, Qt.AlignmentFlag.AlignHCenter, "TopicGate")
    painter.setFont(QFont("Segoe UI", 11))
    painter.drawText(
        0,
        160,
        520,
        24,
        Qt.AlignmentFlag.AlignHCenter,
        "Secure local access to your MQTT topics",
    )
    painter.end()
    splash_screen = QSplashScreen(
        splash_pixmap,
        Qt.WindowType.WindowStaysOnTopHint,
    )
    splash_screen.showMessage(
        "Starting TopicGate...",
        Qt.AlignmentFlag.AlignBottom | Qt.AlignmentFlag.AlignHCenter,
        Qt.GlobalColor.white,
    )
    splash_screen.show()
    qt_application.processEvents()

    event_loop = QEventLoop(qt_application)
    asyncio.set_event_loop(event_loop)

    app = App(qt_application, splash_screen)

    with event_loop:
        return event_loop.run_until_complete(app.run())
