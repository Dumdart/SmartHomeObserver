import os
from tempfile import TemporaryDirectory
from uuid import UUID

import pytest


_TOPICGATE_TEST_DATA = TemporaryDirectory(
    prefix="topicgate-tests-",
    ignore_cleanup_errors=True,
)
os.environ["TOPICGATE_DATA_DIR"] = _TOPICGATE_TEST_DATA.name


class MemoryCredentialStore:
    def __init__(self) -> None:
        self.passwords: dict[UUID, str] = {}

    def get_password(self, profile_id: UUID) -> str | None:
        return self.passwords.get(profile_id)

    def set_password(self, profile_id: UUID, password: str, /) -> None:
        self.passwords[profile_id] = password

    def delete_password(self, profile_id: UUID) -> None:
        self.passwords.pop(profile_id, None)


@pytest.fixture
def credential_store() -> MemoryCredentialStore:
    return MemoryCredentialStore()
