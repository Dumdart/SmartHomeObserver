import asyncio
import os
from unittest.mock import AsyncMock, MagicMock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QCoreApplication
from PySide6.QtGui import QColor, QGuiApplication, QPixmap
from PySide6.QtWidgets import QApplication

from topicgate.gui.app import (
    STARTUP_SCREEN_SIZE,
    App,
    StartupSplashScreen,
    configure_application_identity,
    create_startup_pixmap,
)


def test_application_uses_topicgate_identity() -> None:
    configure_application_identity()

    assert QCoreApplication.organizationName() == "Dumdart"
    assert QCoreApplication.applicationName() == "TopicGate"
    assert QGuiApplication.applicationDisplayName() == "TopicGate Desktop"


def test_startup_screen_uses_desktop_theme_tokens() -> None:
    application = QApplication.instance() or QApplication([])

    pixmap = create_startup_pixmap(QPixmap())
    image = pixmap.toImage()

    assert (pixmap.width(), pixmap.height()) == STARTUP_SCREEN_SIZE
    assert image.pixelColor(0, 0) == QColor("#f3f4f6")
    assert image.pixelColor(12, 12) == QColor("#ffffff")
    assert image.pixelColor(40, 230) == QColor("#e7eff7")
    assert application is not None


def test_startup_screen_renders_current_progress_message() -> None:
    application = QApplication.instance() or QApplication([])
    splash_screen = StartupSplashScreen(create_startup_pixmap(QPixmap()))

    splash_screen.showMessage("Connecting to MQTT...")
    rendered = splash_screen.grab().toImage()

    assert splash_screen.message() == "Connecting to MQTT..."
    assert rendered.pixelColor(46, 236) == QColor("#405d7a")
    assert application is not None


async def test_app_remains_open_when_initial_mqtt_connection_fails() -> None:
    async def scenario() -> None:
        app = object.__new__(App)
        app._services = MagicMock()
        app._services.start_services = AsyncMock(
            side_effect=ConnectionError("broker unavailable")
        )
        app._services.stop_services = AsyncMock()
        app._view_model = MagicMock()
        app._view_model.start = AsyncMock()
        app._view_model.stop = AsyncMock()
        app._window = MagicMock()
        app._window.cancel_pending_operations = AsyncMock()
        app._qt_application = MagicMock()
        app._qt_application.lastWindowClosed.connect.side_effect = (
            lambda callback: callback()
        )

        result = await app.run()

        assert result == 0
        app._view_model.start.assert_awaited_once()
        app._window.show.assert_called_once_with()
        app._view_model.log_message.emit.assert_called_once()
        app._view_model.stop.assert_awaited_once()
        app._services.stop_services.assert_awaited_once()

    await scenario()


async def test_app_cleans_window_and_view_model_before_services() -> None:
    events: list[str] = []
    app = object.__new__(App)
    app._services = MagicMock()
    app._services.start_services = AsyncMock()
    app._services.stop_services = AsyncMock(
        side_effect=lambda: events.append("services")
    )
    app._view_model = MagicMock()
    app._view_model.start = AsyncMock()
    app._view_model.stop = AsyncMock(
        side_effect=lambda: events.append("view-model")
    )
    app._window = MagicMock()
    app._window.cancel_pending_operations = AsyncMock(
        side_effect=lambda: events.append("window")
    )
    app._qt_application = MagicMock()
    app._qt_application.lastWindowClosed.connect.side_effect = (
        lambda callback: callback()
    )

    assert await app.run() == 0
    assert events == ["window", "view-model", "services"]


async def test_app_keeps_qt_event_loop_running_during_shutdown() -> None:
    app = object.__new__(App)
    app._services = MagicMock()
    app._services.start_services = AsyncMock()
    app._services.stop_services = AsyncMock()
    app._view_model = MagicMock()
    app._view_model.start = AsyncMock()
    app._view_model.stop = AsyncMock()
    app._window = MagicMock()
    app._window.cancel_pending_operations = AsyncMock()
    app._qt_application = MagicMock()
    app._qt_application.lastWindowClosed.connect.side_effect = (
        lambda callback: callback()
    )

    assert await app.run() == 0

    app._qt_application.setQuitOnLastWindowClosed.assert_called_once_with(False)


async def test_repeated_shutdown_signal_only_cleans_up_once() -> None:
    app = object.__new__(App)
    app._services = MagicMock()
    app._services.start_services = AsyncMock()
    app._services.stop_services = AsyncMock()
    app._view_model = MagicMock()
    app._view_model.start = AsyncMock()
    app._view_model.stop = AsyncMock()
    app._window = MagicMock()
    app._window.cancel_pending_operations = AsyncMock()
    app._qt_application = MagicMock()
    app._qt_application.lastWindowClosed.connect.side_effect = (
        lambda callback: (callback(), callback())
    )

    assert await app.run() == 0
    app._view_model.stop.assert_awaited_once()
    app._services.stop_services.assert_awaited_once()


async def test_partial_view_model_startup_is_cleaned_up() -> None:
    app = object.__new__(App)
    app._services = MagicMock()
    app._services.start_services = AsyncMock()
    app._services.stop_services = AsyncMock()
    app._view_model = MagicMock()
    app._view_model.start = AsyncMock(side_effect=RuntimeError("start failed"))
    app._view_model.stop = AsyncMock()
    app._window = MagicMock()
    app._window.cancel_pending_operations = AsyncMock()
    app._qt_application = MagicMock()

    try:
        await app.run()
    except RuntimeError as error:
        assert str(error) == "start failed"
    else:
        raise AssertionError("Expected startup failure")
    app._view_model.stop.assert_awaited_once()
    app._services.stop_services.assert_awaited_once()
    app._window.show.assert_not_called()
