from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
import threading
import time
from uuid import uuid4

from sqlalchemy import text

from topicgate.infrastructure.database.database_context import DatabaseContext


class ControlOperationConflict(RuntimeError):
    """Raised when another TopicGate process owns the control lease."""


class ControlOperationService:
    """Coordinate state-changing desktop and MCP work across processes."""

    def __init__(
        self,
        database: DatabaseContext,
        owner: str,
        *,
        lease_seconds: float = 30.0,
    ) -> None:
        self._database = database
        self._owner = owner
        self._lease_seconds = lease_seconds
        self._token: ContextVar[str | None] = ContextVar(
            "topicgate_control_lease_token",
            default=None,
        )
        with self._database.session() as session:
            self._seen_generation = int(
                session.execute(
                    text(
                        "SELECT generation FROM control_operation_state WHERE id = 1"
                    )
                ).scalar_one()
            )

    @contextmanager
    def operation(self, name: str) -> Iterator[None]:
        inherited_token = self._token.get()
        if inherited_token is not None:
            yield
            return

        token = str(uuid4())
        self._acquire(name, token)
        context_token = self._token.set(token)
        stopped = threading.Event()
        renewer = threading.Thread(
            target=self._renew_until_stopped,
            args=(name, token, stopped),
            daemon=True,
            name="topicgate-control-lease",
        )
        renewer.start()
        try:
            yield
        finally:
            stopped.set()
            renewer.join(timeout=1.0)
            self._release(token)
            self._token.reset(context_token)

    def _acquire(self, name: str, token: str) -> None:
        now = time.time()
        expires_at = now + self._lease_seconds
        statement = text(
            "INSERT INTO control_operation_lease "
            "(id, owner, operation, token, expires_at) "
            "VALUES (1, :owner, :operation, :token, :expires_at) "
            "ON CONFLICT(id) DO UPDATE SET "
            "owner = excluded.owner, operation = excluded.operation, "
            "token = excluded.token, expires_at = excluded.expires_at "
            "WHERE control_operation_lease.expires_at <= :now"
        )
        with self._database.transaction() as session:
            generation = int(
                session.execute(
                    text(
                        "SELECT generation FROM control_operation_state WHERE id = 1"
                    )
                ).scalar_one()
            )
            if generation != self._seen_generation:
                raise ControlOperationConflict(
                    "TopicGate configuration changed in another desktop or MCP "
                    "process. Restart this process to reload the latest broker, "
                    "subscription, retention, and credential state before retrying."
                )
            result = session.execute(
                statement,
                {
                    "owner": self._owner,
                    "operation": name,
                    "token": token,
                    "expires_at": expires_at,
                    "now": now,
                },
            )
            if result.rowcount == 1:
                return
            conflict = session.execute(
                text(
                    "SELECT owner, operation, expires_at "
                    "FROM control_operation_lease WHERE id = 1"
                )
            ).one()
        remaining = max(0.0, float(conflict.expires_at) - now)
        raise ControlOperationConflict(
            f"TopicGate {conflict.owner} is already running "
            f"'{conflict.operation}'. Retry after it finishes "
            f"(lease expires in at most {remaining:.0f} seconds)."
        )

    def check_ownership(self) -> None:
        """Fail if a long operation lost its lease or configuration generation."""
        token = self._token.get()
        with self._database.session() as session:
            owned = session.execute(
                text("SELECT token, expires_at FROM control_operation_lease WHERE id = 1")
            ).first()
            generation = session.execute(
                text("SELECT generation FROM control_operation_state WHERE id = 1")
            ).scalar_one()
        if (
            owned is None
            or owned.token != token
            or owned.expires_at <= time.time()
            or generation != self._seen_generation
        ):
            raise ControlOperationConflict(
                "Control lease or configuration changed during the operation."
            )

    def _renew_until_stopped(
        self,
        name: str,
        token: str,
        stopped: threading.Event,
    ) -> None:
        interval = max(0.01, self._lease_seconds / 3)
        while not stopped.wait(interval):
            with self._database.transaction() as session:
                session.execute(
                    text(
                        "UPDATE control_operation_lease "
                        "SET operation = :operation, expires_at = :expires_at "
                        "WHERE id = 1 AND token = :token"
                    ),
                    {
                        "operation": name,
                        "expires_at": time.time() + self._lease_seconds,
                        "token": token,
                    },
                )

    def _release(self, token: str) -> None:
        with self._database.transaction() as session:
            deleted = session.execute(
                text(
                    "DELETE FROM control_operation_lease "
                    "WHERE id = 1 AND token = :token"
                ),
                {"token": token},
            )
            if deleted.rowcount == 1:
                session.execute(
                    text(
                        "UPDATE control_operation_state "
                        "SET generation = generation + 1 WHERE id = 1"
                    )
                )
                self._seen_generation += 1
