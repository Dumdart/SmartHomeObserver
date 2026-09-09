from unittest.mock import MagicMock
import pytest

from topicgate.app.services.persistence_lifecycle import PersistenceLifecycle


async def test_persistence_lifecycle_closes_repository_before_database() -> None:
    events: list[str] = []
    messages = MagicMock()
    database = MagicMock()
    messages.close.side_effect = lambda: events.append("messages")
    database.dispose.side_effect = lambda: events.append("database")
    lifecycle = PersistenceLifecycle(messages, database)

    await lifecycle.start()
    await lifecycle.stop()

    assert events == ["messages", "database"]


async def test_incomplete_history_shutdown_does_not_dispose_a_live_writer() -> None:
    history = MagicMock()
    history.close.side_effect = TimeoutError("History drain timed out.")
    history.is_alive = True
    messages, database = MagicMock(), MagicMock()
    with pytest.raises(ExceptionGroup, match="incomplete"):
        await PersistenceLifecycle(messages, database, history).stop()
    messages.close.assert_called_once()
    database.dispose.assert_not_called()


async def test_failed_history_writer_still_releases_idle_database() -> None:
    history = MagicMock()
    history.close.side_effect = RuntimeError("History write failed.")
    history.is_alive = False
    messages, database = MagicMock(), MagicMock()
    with pytest.raises(ExceptionGroup, match="incomplete"):
        await PersistenceLifecycle(messages, database, history).stop()
    database.dispose.assert_called_once()
