from topicgate.app.services.service_item import ServiceItem
from topicgate.app.services.history_recording_service import HistoryRecordingService
from topicgate.infrastructure.database.database_context import DatabaseContext
from topicgate.infrastructure.repository.topic_message_repository import (
    TopicMessageRepository,
)


class PersistenceLifecycle(ServiceItem):
    """Close application-owned persistence resources after runtime shutdown."""

    def __init__(
        self,
        topic_messages: TopicMessageRepository,
        database: DatabaseContext,
        history_recording: HistoryRecordingService | None = None,
    ) -> None:
        self._topic_messages = topic_messages
        self._database = database
        self._history_recording = history_recording

    async def start(self) -> None:
        """Persistence resources are ready immediately after construction."""

    async def stop(self) -> None:
        """Drain queued messages before disposing database connections."""
        errors: list[Exception] = []
        for resource in (self._history_recording, self._topic_messages):
            if resource is not None:
                try:
                    resource.close()
                except Exception as error:
                    errors.append(error)
        if self._history_recording is None or not self._history_recording.is_alive:
            self._database.dispose()
        if errors:
            raise ExceptionGroup("Persistence shutdown was incomplete.", errors)
